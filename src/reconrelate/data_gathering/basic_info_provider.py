from __future__ import annotations

import logging
import re
from html import unescape
import aiohttp
from urllib.parse import urljoin, urlsplit

from reconrelate.core.errors import ProviderError
from reconrelate.core.http import read_limited_bytes
from reconrelate.core.provider_budget import consume_page
from reconrelate.security.safe_http import safe_client_session, safe_get
from reconrelate.core.types import BasicIntelRecord, TrackerVerification
from reconrelate.security.safe_target import validate_scan_target
from reconrelate.core.normalize import normalize_domain, registrable_domain
from reconrelate.core.tracker import is_plausible_tracker

logger = logging.getLogger(__name__)

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    flags=re.IGNORECASE | re.DOTALL,
)

# Web analytics / tag ids. Same id across two domains ⇒ almost certainly the same operator.
_TRACKER_RES = (
    re.compile(r"UA-\d{4,10}-\d{1,4}"),      # Universal Analytics
    re.compile(r"G-[A-Z0-9]{6,12}"),         # GA4
    re.compile(r"GTM-[A-Z0-9]{4,10}"),       # Google Tag Manager
    re.compile(r"ca-pub-\d{10,20}"),         # AdSense publisher id
)
_COPYRIGHT_RE = re.compile(
    r"(?:\u00a9|&copy;|copyright)\s*(?:\d{4}(?:\s*[-\u2013\u2014]\s*\d{4})?)?[\s,]*"
    r"([A-Za-z0-9][A-Za-z0-9&.,'\- ]{2,60})",
    flags=re.IGNORECASE,
)
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', flags=re.IGNORECASE)
_LEGAL_PATH_RE = re.compile(
    r"(?:^|/)(?:privacy(?:-policy)?|terms(?:-of-(?:use|service))?|legal|imprint|impressum|about|company)(?:[/.?#-]|$)",
    flags=re.IGNORECASE,
)
_LEGAL_SUFFIX = (
    r"(?:Inc\.?|Incorporated|LLC|L\.L\.C\.?|Ltd\.?|Limited|Corporation|Corp\.?|"
    r"Company|Co\.?|plc|L\.P\.?|LP|GmbH|S\.A\.?|B\.V\.?)"
)
_LEGAL_ENTITY_RE = re.compile(
    rf"\b(?:legal\s+(?:name|entity)|company\s+name|owned\s+and\s+operated\s+by|"
    rf"operated\s+by|provided\s+by|website\s+is\s+operated\s+by)\s*[:\-]?\s*"
    rf"([A-Z][A-Za-z0-9&'’.,()\- ]{{1,100}}?\s+{_LEGAL_SUFFIX})"
    r"(?=[.,;)]|$|\s+(?:is|with|whose|which|that|registered|having)\b)",
    flags=re.IGNORECASE,
)


def _extract_relationship_signals(html: str) -> tuple[list[str], str]:
    """Deterministic cross-domain signals from page source: tracker ids + copyright entity."""
    trackers: set[str] = set()
    for rx in _TRACKER_RES:
        for hit in rx.findall(html):
            if is_plausible_tracker(hit):
                trackers.add(hit.strip())
    copyright_org = ""
    m = _COPYRIGHT_RE.search(html)
    if m:
        ent = " ".join(m.group(1).split())
        ent = re.split(r"all rights reserved", ent, flags=re.IGNORECASE)[0]
        copyright_org = ent.strip(" .,-|")
    return sorted(trackers), copyright_org


def _legal_page_urls(html: str, base_url: str, domain: str, limit: int = 2) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for href in _HREF_RE.findall(html):
        try:
            url = urljoin(base_url, unescape(href).strip())
            parsed = urlsplit(url)
            host = normalize_domain(parsed.hostname or "")
            if parsed.scheme not in {"http", "https"} or registrable_domain(host) != registrable_domain(domain):
                continue
            if not _LEGAL_PATH_RE.search(parsed.path.lower()):
                continue
            clean = parsed._replace(fragment="").geturl()
        except Exception:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        urls.append(clean)
        if len(urls) >= limit:
            break
    return urls


def _extract_legal_entities(html: str) -> list[str]:
    text = " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).replace("\xa0", " ").split())
    entities: list[str] = []
    seen: set[str] = set()
    for match in _LEGAL_ENTITY_RE.finditer(text):
        entity = " ".join(match.group(1).split()).strip(" ,.;:-")
        key = entity.casefold()
        if key not in seen:
            seen.add(key)
            entities.append(entity)
    return entities


class BasicInfoProvider:
    """Basic intel provider using free async HTTP fetch + HTML parsing."""

    async def _fetch_url(self, url: str) -> tuple[str, str]:
        headers = {"User-Agent": "ReconRelate/0.1"}
        timeout = aiohttp.ClientTimeout(total=8)
        async with safe_client_session(timeout) as session:
            async with safe_get(session, url, headers=headers) as resp:
                resp.raise_for_status()
                consume_page()
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and content_type:
                    return "", str(getattr(resp, "url", url))
                content = await read_limited_bytes(resp, max_bytes=524_288)
                return content[:100_000].decode("utf-8", errors="replace"), str(getattr(resp, "url", url))

    async def _fetch_html(self, domain: str) -> tuple[str, str]:
        for scheme in ("https", "http"):
            try:
                return await self._fetch_url(f"{scheme}://{domain}")
            except ProviderError:
                raise
            except (aiohttp.ClientError, TimeoutError):
                continue
        return "", ""

    async def lookup(self, domain: str) -> BasicIntelRecord:
        validate_scan_target(domain)
        html, final_url = await self._fetch_html(domain)
        title = ""
        description = ""
        aliases: list[str] = []

        if html:
            m_title = TITLE_RE.search(html)
            if m_title:
                title = " ".join(unescape(m_title.group(1)).split())[:200]
            m_desc = META_DESC_RE.search(html)
            if m_desc:
                description = " ".join(unescape(m_desc.group(1)).split())[:300]
            # Do NOT harvest capitalized title words as org aliases: it produced junk
            # identifiers like [org] "essential"/"security"/"listen" split from marketing
            # taglines. The domain's own root label (below) and the copyright entity are the
            # meaningful org signals; a tagline word is noise, not an organization.

        tracker_ids, copyright_org = _extract_relationship_signals(html) if html else ([], "")

        legal_entities: list[str] = []
        legal_entity_sources: dict[str, str] = {}
        for legal_url in _legal_page_urls(html, final_url or f"https://{domain}", domain):
            try:
                legal_html, resolved_legal_url = await self._fetch_url(legal_url)
            except (ProviderError, aiohttp.ClientError, TimeoutError):
                continue
            for entity in _extract_legal_entities(legal_html):
                if entity not in legal_entity_sources:
                    legal_entities.append(entity)
                    legal_entity_sources[entity] = resolved_legal_url or legal_url

        redirect_domain = ""
        try:
            final_host = normalize_domain(urlsplit(final_url).hostname or "")
            if final_host and registrable_domain(final_host) != registrable_domain(domain):
                redirect_domain = registrable_domain(final_host)
        except Exception:
            pass

        root_label = domain.split(".")[0].replace("-", " ").title()
        if root_label and root_label not in aliases:
            aliases.insert(0, root_label)
        return BasicIntelRecord(
            domain=domain,
            title=title,
            description=description,
            aliases=aliases[:5],
            tracker_ids=tracker_ids,
            copyright_org=copyright_org,
            final_url=final_url,
            redirect_domain=redirect_domain,
            legal_entities=legal_entities,
            legal_entity_sources=legal_entity_sources,
            raw={"source": "http-html", "legal_pages": list(legal_entity_sources.values())},
        )

    async def verify_tracker(self, domain: str, tracker_id: str) -> TrackerVerification:
        """Root-only exact tracker check used to verify reverse-search candidates."""
        validate_scan_target(domain)
        normalized = tracker_id.strip().upper()
        html, final_url = await self._fetch_html(domain)
        trackers, _ = _extract_relationship_signals(html) if html else ([], "")
        return TrackerVerification(
            domain=domain,
            tracker_id=normalized,
            matched=normalized in {value.upper() for value in trackers},
            final_url=final_url,
        )
