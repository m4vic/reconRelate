from __future__ import annotations

import re
from reconrelate.core.normalize import normalize_domain
from reconrelate.core.provider_budget import consume_page, consume_request
from reconrelate.core.sdk_process import run_sdk_operation
from reconrelate.core.types import Identifier
from reconrelate.llm_orchestration.response_parser import is_noise_domain

DOMAIN_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", flags=re.IGNORECASE)


class ReverseWhoisProvider:
    """Free pivot provider using search results (duckduckgo-search) asynchronously."""

    async def _query(self, query: str, limit: int) -> list[str]:
        consume_request()
        result = await run_sdk_operation(
            "duckduckgo", {"query": query, "limit": limit},
            timeout_sec=30, max_output_bytes=1_048_576,
        )
        if not result.get("available"):
            return []
        rows = result.get("results") or []
        if not isinstance(rows, list):
            return []
        out: list[str] = []
        consume_page()
        for row in rows:
            if not isinstance(row, dict):
                continue
            text_blob = " ".join(
                [str(row.get("href") or ""), str(row.get("body") or ""), str(row.get("title") or "")]
            )
            for match in DOMAIN_RE.findall(text_blob):
                try:
                    out.append(normalize_domain(match.lower().strip(".")))
                except Exception:
                    continue
        return out

    async def search(self, identifier: Identifier, max_results: int = 5) -> list[str]:
        id_type = identifier.id_type.lower().strip()
        value = identifier.value.strip()
        if not value:
            return []

        if id_type == "email":
            query = f"\"{value}\""
        elif id_type in {"org", "name"}:
            query = f"\"{value}\" domain"
        elif id_type == "ns":
            query = f"\"{value}\" nameserver"
        elif id_type == "phone":
            query = f"\"{value}\" whois"
        elif id_type == "tracker":
            query = f"\"{value}\""
        else:
            query = value

        out = await self._query(query, max(10, max_results * 3))
        unique: list[str] = []
        seen: set[str] = set()
        for item in out:
            if item in seen or is_noise_domain(item):
                continue
            seen.add(item)
            unique.append(item)
            if len(unique) >= max_results:
                break
        return unique
