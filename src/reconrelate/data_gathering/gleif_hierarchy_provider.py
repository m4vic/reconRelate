"""Free, authoritative LEI corporate-hierarchy data from GLEIF.

GLEIF Level 2 relationships describe accounting consolidation, not acquisition events.  This
adapter deliberately keeps those semantics and abstains when a name does not identify one exact
active LEI record.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

import aiohttp

from reconrelate.core.errors import ProviderMalformedError, ProviderRateLimitError
from reconrelate.core.http import read_limited_json
from reconrelate.core.provider_budget import consume_page
from reconrelate.security.safe_http import safe_client_session, safe_get


_API = "https://api.gleif.org/api/v1"
_UA = "ReconRelate/0.1 (open-source OSINT recon tool)"
_RELATIONS = (
    ("direct-parent", "direct_accounting_parent"),
    ("ultimate-parent", "ultimate_accounting_parent"),
    ("direct-children", "direct_accounting_child"),
    ("ultimate-children", "ultimate_accounting_child"),
)


def _normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def _names(record: dict) -> set[str]:
    entity = ((record.get("attributes") or {}).get("entity") or {})
    values = [((entity.get("legalName") or {}).get("name") or "")]
    values.extend(str(item.get("name") or "") for item in entity.get("otherNames") or [])
    values.extend(str(item.get("name") or "") for item in entity.get("transliteratedOtherNames") or [])
    return {_normalized_name(value) for value in values if value}


class GleifHierarchyProvider:
    """Exact legal-name to direct/ultimate accounting parent and child relationships."""

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        timeout = aiohttp.ClientTimeout(total=15)
        async with safe_client_session(timeout) as session:
            async with safe_get(
                session, f"{_API}{path}", params=params, headers={"User-Agent": _UA}
            ) as response:
                if response.status == 404:
                    return {"data": None}
                if response.status == 429:
                    raise ProviderRateLimitError("GLEIF API rate limit")
                response.raise_for_status()
                consume_page()
                data = await read_limited_json(response, max_bytes=2_097_152)
                if not isinstance(data, dict):
                    raise ProviderMalformedError("GLEIF response must be an object")
                return data

    async def _resolve_exact(self, name: str) -> dict | None:
        data = await self._get_json("/lei-records", {
            "filter[entity.legalName]": name,
            "page[size]": "10",
        })
        records = data.get("data") or []
        if not isinstance(records, list):
            raise ProviderMalformedError("GLEIF search data must be a list")
        wanted = _normalized_name(name)
        matches = []
        for record in records:
            if not isinstance(record, dict) or wanted not in _names(record):
                continue
            attrs = record.get("attributes") or {}
            entity = attrs.get("entity") or {}
            registration = attrs.get("registration") or {}
            if entity.get("status") == "ACTIVE" and registration.get("status") == "ISSUED":
                matches.append(record)
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _relation_items(data: object) -> list[dict]:
        if isinstance(data, dict):
            return [data]
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    async def related_orgs(self, name: str, max_results: int = 20) -> list[dict]:
        if max_results <= 0:
            return []
        record = await self._resolve_exact(name.strip())
        if record is None:
            return []
        lei = str(record.get("id") or (record.get("attributes") or {}).get("lei") or "")
        if not lei:
            raise ProviderMalformedError("GLEIF record has no LEI")
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for endpoint, relation in _RELATIONS:
            payload = await self._get_json(
                f"/lei-records/{quote(lei, safe='')}/{endpoint}",
                {"page[size]": str(min(100, max_results - len(out)))},
            )
            for target in self._relation_items(payload.get("data")):
                target_lei = str(target.get("id") or (target.get("attributes") or {}).get("lei") or "")
                legal_name = (((target.get("attributes") or {}).get("entity") or {})
                              .get("legalName") or {}).get("name")
                if not target_lei or not legal_name or (relation, target_lei) in seen:
                    continue
                seen.add((relation, target_lei))
                out.append({
                    "relation": relation,
                    "org": str(legal_name),
                    "lei": target_lei,
                    "qid": target_lei,  # compatibility with the current CLI result contract
                    "domain": "",
                    "subject_lei": lei,
                    "source_record_id": f"{lei}:{relation}:{target_lei}",
                })
                if len(out) >= max(0, max_results):
                    return out
        return out
