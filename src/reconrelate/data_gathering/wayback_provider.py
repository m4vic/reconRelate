"""Bounded historical root-page evidence from the Internet Archive Wayback Machine."""

from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlsplit

import aiohttp

from reconrelate.core.errors import ProviderMalformedError, ProviderRateLimitError
from reconrelate.core.http import read_limited_bytes, read_limited_json
from reconrelate.core.normalize import normalize_domain
from reconrelate.core.provider_budget import consume_page
from reconrelate.core.types import HistoricalWebRecord
from reconrelate.data_gathering.basic_info_provider import TITLE_RE, _extract_relationship_signals
from reconrelate.security.safe_http import safe_client_session, safe_get
from reconrelate.security.safe_target import validate_scan_target


_CDX = "https://web.archive.org/cdx/search/cdx"
_REPLAY = "https://web.archive.org/web/{timestamp}id_/{original}"


def _capture_time(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError as exc:
        raise ProviderMalformedError("Wayback capture has an invalid timestamp") from exc


def parse_cdx(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, list) or not payload:
        return []
    header = payload[0]
    if not isinstance(header, list):
        raise ProviderMalformedError("Wayback CDX header must be an array")
    required = {"timestamp", "original", "mimetype", "statuscode", "digest"}
    if not required.issubset({str(value) for value in header}):
        raise ProviderMalformedError("Wayback CDX response is missing required fields")
    rows: list[dict[str, str]] = []
    for values in payload[1:]:
        if not isinstance(values, list) or len(values) != len(header):
            continue
        row = {str(key): str(value) for key, value in zip(header, values)}
        if row["statuscode"] == "200" and row["mimetype"].lower().startswith("text/html"):
            rows.append(row)
    return rows


def record_from_html(domain: str, row: dict[str, str], html: str) -> HistoricalWebRecord | None:
    original = row["original"]
    host = (urlsplit(original).hostname or "").lower().rstrip(".")
    if host not in {domain, f"www.{domain}"}:
        return None
    archive_url = _REPLAY.format(timestamp=row["timestamp"], original=original)
    title = ""
    match = TITLE_RE.search(html)
    if match:
        title = " ".join(unescape(match.group(1)).split())[:200]
    trackers, copyright_org = _extract_relationship_signals(html)
    return HistoricalWebRecord(
        domain=domain,
        captured_at=_capture_time(row["timestamp"]),
        original_url=original,
        archive_url=archive_url,
        digest=row["digest"],
        title=title,
        tracker_ids=trackers,
        copyright_org=copyright_org,
    )


class WaybackProvider:
    async def _query(self, target_url: str, limit: int) -> list[dict[str, str]]:
        timeout = aiohttp.ClientTimeout(total=20)
        params = {
            "url": target_url,
            "matchType": "exact",
            "output": "json",
            "fl": "timestamp,original,mimetype,statuscode,digest",
            "filter": "statuscode:200",
            "collapse": "digest",
            "limit": str(limit),
        }
        async with safe_client_session(timeout) as session:
            async with safe_get(session, _CDX, params=params, headers={"User-Agent": "ReconRelate/0.1"}) as response:
                if response.status == 429:
                    raise ProviderRateLimitError("Wayback CDX rate limit")
                response.raise_for_status()
                consume_page()
                payload = await read_limited_json(response, max_bytes=1_048_576)
        return parse_cdx(payload)

    async def _snapshot(self, domain: str, row: dict[str, str]) -> HistoricalWebRecord | None:
        original = row["original"]
        host = (urlsplit(original).hostname or "").lower().rstrip(".")
        if host not in {domain, f"www.{domain}"}:
            return None
        archive_url = _REPLAY.format(timestamp=row["timestamp"], original=original)
        timeout = aiohttp.ClientTimeout(total=20)
        async with safe_client_session(timeout) as session:
            async with safe_get(session, archive_url, headers={"User-Agent": "ReconRelate/0.1"}) as response:
                if response.status == 429:
                    raise ProviderRateLimitError("Wayback replay rate limit")
                response.raise_for_status()
                consume_page()
                content = await read_limited_bytes(response, max_bytes=1_048_576)
        html = content.decode("utf-8", errors="replace")
        return record_from_html(domain, row, html)

    async def lookup(self, domain: str, max_results: int = 4) -> list[HistoricalWebRecord]:
        domain = normalize_domain(domain)
        validate_scan_target(domain)
        if max_results <= 0:
            return []
        rows: list[dict[str, str]] = []
        for root in (f"https://{domain}/", f"https://www.{domain}/"):
            rows.extend(await self._query(root, 2))
            rows.extend(await self._query(root, -2))
        unique: dict[tuple[str, str], dict[str, str]] = {}
        for row in rows:
            unique[(row["timestamp"], row["digest"])] = row
        selected = sorted(unique.values(), key=lambda row: row["timestamp"])
        if len(selected) > max_results:
            # Preserve both ends of the observed history instead of only recent captures.
            first_count = (max_results + 1) // 2
            last_count = max_results - first_count
            selected = selected[:first_count] + (selected[-last_count:] if last_count else [])
        out: list[HistoricalWebRecord] = []
        for row in selected:
            snapshot = await self._snapshot(domain, row)
            if snapshot is not None:
                out.append(snapshot)
        return out
