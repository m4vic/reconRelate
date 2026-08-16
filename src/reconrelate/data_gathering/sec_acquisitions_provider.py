"""Conservative completed-acquisition evidence from official SEC EDGAR filings."""

from __future__ import annotations

import asyncio
import os
import re
import unicodedata
from html import unescape

import aiohttp

from reconrelate.core.errors import ProviderMalformedError, ProviderRateLimitError
from reconrelate.core.http import read_limited_json, read_limited_text
from reconrelate.core.provider_budget import consume_page
from reconrelate.security.safe_http import safe_client_session, safe_get


_TICKERS = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
_THROTTLE = asyncio.Lock()
_LAST_CALL = 0.0
_MIN_INTERVAL_SEC = 0.5  # deliberately far below SEC's published 10 requests/second maximum

_LEGAL_SUFFIX = (
    r"(?:Inc\.?|Incorporated|LLC|L\.L\.C\.?|Ltd\.?|Limited|Corporation|Corp\.?|"
    r"Company|Co\.?|plc|L\.P\.?|LP|GmbH|S\.A\.?|B\.V\.?)"
)
_TARGET = (
    rf"([A-Z][A-Za-z0-9&'’.,()\- ]{{1,100}}?\s+{_LEGAL_SUFFIX})"
    r"(?=[.,;)]|$|\s+(?:on|for|from|which|that|pursuant|in\s+exchange|as)\b)"
)
_ACQUISITION_PATTERNS = (
    re.compile(rf"\b(?:completed|closed|consummated)\s+(?:the\s+)?acquisition\s+of\s+{_TARGET}", re.I),
    re.compile(rf"\b(?:the\s+)?(?:registrant|company|we)\s+(?:has\s+)?acquired\s+{_TARGET}", re.I),
)
_AGREED_TARGET_PATTERN = re.compile(
    rf"\b(?:registrant|company|we)\s+agreed\s+to\s+acquire\s+(?:all\s+of\s+)?{_TARGET}", re.I
)
_CROSS_REFERENCE_COMPLETION = re.compile(
    r"\b(?:completed\s+the\s+Acquisition(?:\s+described\s+(?:in|under)\s+Item\s+1\.01)?|"
    r"closing\s+of\s+the\s+Acquisition[^.]{0,120}\boccurred)\b", re.I
)


def _normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def _plain_text(html: str) -> str:
    html = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(unescape(text).replace("\xa0", " ").split())


def _clean_target(value: str) -> str:
    value = " ".join(value.split()).strip(" ,.;:-")
    # A legal name captured across a section heading or sentence boundary is unsafe.
    if len(value) > 120 or any(mark in value for mark in (". On ", ". The ", ";")):
        return ""
    return value


def extract_completed_acquisitions(text: str) -> list[tuple[str, str]]:
    """Return unique (legal entity, supporting sentence) pairs; uncertain text abstains."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern in _ACQUISITION_PATTERNS:
        for match in pattern.finditer(text):
            target = _clean_target(match.group(1))
            key = _normalize_name(target)
            if not target or key in seen:
                continue
            start = max(text.rfind(". ", 0, match.start()) + 2, match.start() - 180)
            end_pos = text.find(". ", match.end())
            end = min(len(text), end_pos + 1 if end_pos >= 0 else match.end() + 180)
            sentence = " ".join(text[start:end].split())[:500]
            seen.add(key)
            out.append((target, sentence))
    # Some 8-Ks define the named target in Item 1.01 and confirm completion by referring to the
    # capitalized "Acquisition" in Item 2.01. Neither statement is sufficient alone.
    completion = _CROSS_REFERENCE_COMPLETION.search(text)
    if completion:
        for match in _AGREED_TARGET_PATTERN.finditer(text):
            target = _clean_target(match.group(1))
            key = _normalize_name(target)
            if not target or key in seen:
                continue
            agreement_start = max(text.rfind(". ", 0, match.start()) + 2, match.start() - 120)
            agreement_end = text.find(". ", match.end())
            agreement = text[agreement_start:agreement_end + 1 if agreement_end >= 0 else match.end()]
            completion_end = text.find(". ", completion.end())
            confirmation = text[completion.start():completion_end + 1
                                if completion_end >= 0 else completion.end()]
            supporting = " ".join(f"{agreement} {confirmation}".split())[:500]
            seen.add(key)
            out.append((target, supporting))
    return out


class SecAcquisitionsProvider:
    """Resolve a public filer and extract explicit completed acquisitions from recent Item 2.01s."""

    def __init__(self) -> None:
        self.user_agent = os.getenv("RECONRELATE_SEC_USER_AGENT", "").strip()
        if self.user_agent and re.search(
            r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", self.user_agent
        ) is None:
            raise ValueError("RECONRELATE_SEC_USER_AGENT must contain a contact email address")

    async def _get(self, url: str, *, json_response: bool) -> object:
        global _LAST_CALL
        if not self.user_agent:
            raise ValueError("RECONRELATE_SEC_USER_AGENT is required for SEC fair-access compliance")
        async with _THROTTLE:
            loop = asyncio.get_running_loop()
            delay = _MIN_INTERVAL_SEC - (loop.time() - _LAST_CALL)
            if delay > 0:
                await asyncio.sleep(delay)
            _LAST_CALL = loop.time()
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        async with safe_client_session(timeout) as session:
            async with safe_get(session, url, headers=headers) as response:
                if response.status == 429:
                    raise ProviderRateLimitError("SEC EDGAR rate limit")
                response.raise_for_status()
                consume_page()
                if json_response:
                    value = await read_limited_json(response, max_bytes=4_194_304)
                    if not isinstance(value, dict):
                        raise ProviderMalformedError("SEC JSON response must be an object")
                    return value
                return await read_limited_text(response, max_bytes=1_048_576)

    async def _resolve_cik(self, name: str) -> tuple[str, str] | None:
        payload = await self._get(_TICKERS, json_response=True)
        wanted = _normalize_name(name)
        matches: list[tuple[str, str]] = []
        for row in payload.values():
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "")
            try:
                cik = f"{int(row.get('cik_str')):010d}"
            except (TypeError, ValueError):
                continue
            if title and _normalize_name(title) == wanted:
                matches.append((cik, title))
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _filings(payload: dict) -> list[dict[str, str]]:
        recent = ((payload.get("filings") or {}).get("recent") or {})
        if not isinstance(recent, dict):
            raise ProviderMalformedError("SEC submissions recent filings must be an object")
        keys = ("accessionNumber", "filingDate", "form", "primaryDocument", "items")
        columns = [recent.get(key) or [] for key in keys]
        if not all(isinstance(column, list) for column in columns):
            raise ProviderMalformedError("SEC submissions filing columns must be arrays")
        return [dict(zip(keys, values)) for values in zip(*columns)]

    async def related_orgs(self, name: str, max_results: int = 20) -> list[dict]:
        if max_results <= 0:
            return []
        resolved = await self._resolve_cik(name.strip())
        if resolved is None:
            return []
        cik, filer_name = resolved
        submissions = await self._get(_SUBMISSIONS.format(cik=cik), json_response=True)
        candidates = [
            filing for filing in self._filings(submissions)[:25]
            if filing["form"] in {"8-K", "8-K/A"}
            and "2.01" in {item.strip() for item in filing["items"].split(",")}
            and filing["accessionNumber"] and filing["primaryDocument"]
        ][:5]
        out: list[dict] = []
        seen: set[str] = set()
        for filing in candidates:
            accession_compact = filing["accessionNumber"].replace("-", "")
            cik_compact = str(int(cik))
            url = _ARCHIVE.format(
                cik=cik_compact, accession=accession_compact,
                document=filing["primaryDocument"],
            )
            html = await self._get(url, json_response=False)
            for target, sentence in extract_completed_acquisitions(_plain_text(str(html))):
                key = _normalize_name(target)
                if key == _normalize_name(filer_name) or key in seen:
                    continue
                seen.add(key)
                out.append({
                    "relation": "acquired",
                    "org": target,
                    "domain": "",
                    "cik": cik,
                    "qid": filing["accessionNumber"],
                    "source_record_id": filing["accessionNumber"],
                    "filing_date": filing["filingDate"],
                    "filing_url": url,
                    "supporting_text": sentence,
                })
                if len(out) >= max_results:
                    return out
        return out
