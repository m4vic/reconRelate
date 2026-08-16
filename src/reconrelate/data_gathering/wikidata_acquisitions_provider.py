"""Acquisition/ownership source (free, Wikidata) via aiohttp.

WHOIS/DNS pivoting only sees shared infrastructure, so a freshly-acquired company (still on
its own WHOIS) looks unrelated. Wikidata records the org→org edges directly (parent,
subsidiary, owned-by, owns) AND each org's official website (P856) — the reliable org→domain
link that avoids ambiguous text search.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from reconrelate.core.errors import ProviderMalformedError, ProviderRateLimitError
from reconrelate.core.http import read_limited_json
from reconrelate.core.provider_budget import consume_page
from reconrelate.security.safe_http import safe_client_session, safe_get
logger = logging.getLogger(__name__)

_API = "https://www.wikidata.org/w/api.php"
_UA = "ReconRelate/0.1 (open-source OSINT recon tool)"
_REL_PROPS = {"P749": "parent", "P355": "subsidiary", "P127": "owned_by", "P1830": "owns"}

# Wikidata asks callers to be polite. Serialize requests with a minimum spacing and retry on
# 429/503, so the extra per-org P856 lookups don't trip rate limits mid-scan.
_THROTTLE = asyncio.Lock()
_MIN_INTERVAL_SEC = 0.34  # ~3 req/s
_last_call_at = 0.0


class WikidataAcquisitionsProvider:
    """Free org→org ownership relations + official-website (P856) resolution from Wikidata."""

    def __init__(self) -> None:
        self._entity_cache: dict[str, dict] = {}

    async def _get_json(self, params: dict) -> dict:
        global _last_call_at
        timeout = aiohttp.ClientTimeout(total=15)
        async with _THROTTLE:
            loop = asyncio.get_running_loop()
            gap = _MIN_INTERVAL_SEC - (loop.time() - _last_call_at)
            if gap > 0:
                await asyncio.sleep(gap)
            _last_call_at = loop.time()
        async with safe_client_session(timeout) as session:
            async with safe_get(session, _API, params=params, headers={"User-Agent": _UA}) as resp:
                if resp.status == 429:
                    raise ProviderRateLimitError("Wikidata rate limit")
                resp.raise_for_status()
                consume_page()
                data = await read_limited_json(resp, max_bytes=2_097_152)
                if not isinstance(data, dict):
                    raise ProviderMalformedError("Wikidata response must be an object")
                return data

    async def _search_qid(self, name: str) -> str:
        data = await self._get_json({
            "action": "wbsearchentities", "search": name, "language": "en",
            "format": "json", "type": "item", "limit": 1,
        })
        hits = data.get("search") or []
        return str(hits[0]["id"]) if hits else ""

    async def _entity(self, qid: str) -> dict:
        if qid in self._entity_cache:
            return self._entity_cache[qid]
        data = await self._get_json({
            "action": "wbgetentities", "ids": qid, "props": "claims|labels",
            "languages": "en", "format": "json",
        })
        ent = (data.get("entities") or {}).get(qid, {}) or {}
        self._entity_cache[qid] = ent
        return ent

    @staticmethod
    def _label_from(ent: dict) -> str:
        return str(((ent.get("labels") or {}).get("en") or {}).get("value") or "")

    @staticmethod
    def _website_domain_from(ent: dict) -> str:
        """Registrable domain of the org's official website (Wikidata P856), or '' if none.

        The *reliable* org→domain link: instead of text-searching an ambiguous org name
        (which returns junk like englishclub.com for "day one"), read the website Wikidata
        records for that exact entity.
        """
        from reconrelate.core.normalize import normalize_domain, registrable_domain

        for stmt in (ent.get("claims") or {}).get("P856", []):
            try:
                url = stmt["mainsnak"]["datavalue"]["value"]
            except (KeyError, TypeError):
                continue
            if not url:
                continue
            try:
                return registrable_domain(normalize_domain(str(url)))
            except Exception:
                continue
        return ""

    async def related_orgs(self, name: str, max_results: int = 20) -> list[dict]:
        """[{relation, org, qid, domain}] — orgs tied to `name` by ownership on Wikidata.

        `domain` is the related org's official-website registrable domain (P856), or '' —
        the reliable link used to turn an acquisition into a real domain without text search.
        """
        qid = await self._search_qid(name.strip())
        if not qid:
            return []
        ent = await self._entity(qid)
        claims = ent.get("claims") or {}
        out: list[dict] = []
        seen: set[str] = set()
        for prop, relation in _REL_PROPS.items():
            for stmt in claims.get(prop, []):
                try:
                    target = stmt["mainsnak"]["datavalue"]["value"]["id"]
                except (KeyError, TypeError):
                    continue
                if target in seen:
                    continue
                seen.add(target)
                target_ent = await self._entity(target)
                out.append({
                    "relation": relation,
                    "qid": target,
                    "org": self._label_from(target_ent) or target,
                    "domain": self._website_domain_from(target_ent),
                })
                if len(out) >= max_results:
                    return out
        return out
