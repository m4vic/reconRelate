"""Reverse-WHOIS source (paid, Whoxy) asynchronously via aiohttp.

Indexes registrant contact fields (name/company/email) to return registered domains.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
import aiohttp

from reconrelate.core.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInputError,
    ProviderMalformedError,
    ProviderRateLimitError,
    ProviderResponseLimitError,
)
from reconrelate.core.http import read_limited_json
from reconrelate.core.provider_budget import consume_page
from reconrelate.core.normalize import normalize_domain
from reconrelate.core.types import Identifier
from reconrelate.core.provider_quota import ProviderQuotaSnapshot
from reconrelate.security.safe_http import safe_client_session, safe_get

logger = logging.getLogger(__name__)

_API = "https://api.whoxy.com/"
_UA = "ReconRelate/0.1 (open-source OSINT recon tool)"
_FIELD_FOR = {"email": "email", "org": "company", "name": "name"}
_MAX_IDENTIFIER_CHARS = 320
_MAX_MICRO_ROWS = 2_500
_MAX_ERROR_CHARS = 300


class WhoxyReverseWhoisProvider:
    """Reverse-WHOIS pivots from Whoxy asynchronously (paid; needs WHOXY_API_KEY)."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("WHOXY_API_KEY", "")

    async def _get_json(self, params: dict) -> dict:
        timeout = aiohttp.ClientTimeout(total=20)
        async with safe_client_session(timeout) as session:
            async with safe_get(session, _API, params=params, headers={"User-Agent": _UA}) as resp:
                if resp.status in {401, 403}:
                    raise ProviderAuthError(f"Whoxy HTTP authentication failure ({resp.status})")
                if resp.status == 429:
                    raise ProviderRateLimitError("Whoxy HTTP rate limit (429)")
                if resp.status >= 400:
                    raise ProviderError(f"Whoxy HTTP failure ({resp.status})")
                consume_page()
                data = await read_limited_json(resp, max_bytes=1_048_576)
                if not isinstance(data, dict):
                    raise ProviderMalformedError("Whoxy response must be an object")
                return data

    def _safe_reason(self, value: object) -> str:
        reason = " ".join(str(value or "unknown error").split())[:_MAX_ERROR_CHARS]
        return reason.replace(self._api_key, "[redacted]") if self._api_key else reason

    def _parse_domains(self, data: dict, max_results: int) -> list[str]:
        status = data.get("status")
        if type(status) is not int or status not in {0, 1}:
            raise ProviderMalformedError("Whoxy status must be integer 0 or 1")
        if status == 0:
            reason = self._safe_reason(data.get("status_reason"))
            lowered = reason.lower()
            if "key" in lowered or "auth" in lowered or "unauthor" in lowered:
                raise ProviderAuthError(reason)
            if any(term in lowered for term in ("limit", "quota", "credit", "too many")):
                raise ProviderRateLimitError(reason)
            if any(term in lowered for term in ("no record", "no result", "zero result")):
                return []
            raise ProviderError(f"Whoxy API error: {reason}")

        if data.get("api_query") not in {None, "reverse_whois"}:
            raise ProviderMalformedError("Whoxy api_query does not match reverse_whois")
        rows = data.get("search_result")
        if not isinstance(rows, list):
            raise ProviderMalformedError("Whoxy search_result must be an array")
        if len(rows) > _MAX_MICRO_ROWS:
            raise ProviderResponseLimitError("Whoxy micro result exceeds 2,500 rows")
        for field in ("total_results", "total_pages", "current_page"):
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, bool):
                raise ProviderMalformedError(f"Whoxy {field} must be a non-negative integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise ProviderMalformedError(
                    f"Whoxy {field} must be a non-negative integer"
                ) from exc
            if parsed < 0:
                raise ProviderMalformedError(f"Whoxy {field} must be a non-negative integer")
        if "current_page" in data and int(data["current_page"]) != 1:
            raise ProviderMalformedError("Whoxy micro response unexpectedly returned another page")
        if "total_results" in data and int(data["total_results"]) < len(rows):
            raise ProviderMalformedError("Whoxy total_results is smaller than search_result")

        unique: list[str] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ProviderMalformedError(f"Whoxy search_result[{index}] must be an object")
            raw = str(row.get("domain_name") or "").strip()
            if not raw:
                continue
            try:
                domain = normalize_domain(raw)
            except Exception:
                continue
            if domain in seen:
                continue
            seen.add(domain)
            unique.append(domain)
            if len(unique) >= max_results:
                break
        return unique

    async def search(self, identifier: Identifier, max_results: int = 5) -> list[str]:
        field = _FIELD_FOR.get(identifier.id_type.lower().strip())
        value = identifier.value.strip()
        if not field or not value or max_results <= 0:
            return []
        if not self._api_key:
            raise ProviderAuthError("WHOXY_API_KEY is required")
        if len(value) > _MAX_IDENTIFIER_CHARS:
            raise ProviderInputError(
                f"Whoxy identifier exceeds {_MAX_IDENTIFIER_CHARS} characters"
            )

        params = {
            "key": self._api_key,
            "reverse": "whois",
            "mode": "micro",
            field: value,
        }
        data = await self._get_json(params)

        domains = self._parse_domains(data, max_results)
        logger.info("Whoxy reverse-WHOIS completed for pivot type %s", identifier.id_type)
        return domains

    async def balance(self) -> ProviderQuotaSnapshot:
        """Fetch the authoritative reverse-WHOIS credit balance after explicit CLI approval."""
        if not self._api_key:
            raise ProviderAuthError("WHOXY_API_KEY is required")
        data = await self._get_json({"key": self._api_key, "account": "balance"})
        status = data.get("status")
        if type(status) is not int or status not in {0, 1}:
            raise ProviderMalformedError("Whoxy balance status must be integer 0 or 1")
        if status == 0:
            reason = self._safe_reason(data.get("status_reason"))
            lowered = reason.lower()
            if "key" in lowered or "auth" in lowered or "unauthor" in lowered:
                raise ProviderAuthError(reason)
            if any(term in lowered for term in ("limit", "quota", "credit", "too many")):
                raise ProviderRateLimitError(reason)
            raise ProviderError(f"Whoxy balance API error: {reason}")
        value = data.get("reverse_whois_balance")
        if type(value) is not int:
            raise ProviderMalformedError("Whoxy reverse_whois_balance must be a non-negative integer")
        remaining = value
        if remaining < 0:
            raise ProviderMalformedError("Whoxy reverse_whois_balance must be a non-negative integer")
        return ProviderQuotaSnapshot(
            provider="whoxy",
            capability="reverse_whois",
            unit="credit",
            remaining=remaining,
            authoritative=True,
            billing_effect="unknown",
            checked_at=datetime.now(timezone.utc).isoformat(),
        )
