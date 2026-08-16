from __future__ import annotations

import json
import logging

import aiohttp

from reconrelate.core.errors import ProviderMalformedError
from reconrelate.core.http import read_limited_bytes
from reconrelate.core.provider_budget import consume_page
from reconrelate.security.safe_target import validate_scan_target
from reconrelate.security.safe_http import safe_client_session, safe_get

logger = logging.getLogger(__name__)


class CrtshProvider:
    """Queries Certificate Transparency logs via asynchronous HTTP."""

    async def search(self, root_domain: str, max_results: int = 15) -> list[str]:
        validate_scan_target(root_domain)
        url = f"https://crt.sh/?q=%.{root_domain}&output=json"
        headers = {"User-Agent": "ReconRelate/0.1 (open-source OSINT recon tool)"}
        max_response_bytes = 4_194_304
        timeout = aiohttp.ClientTimeout(total=10)
        async with safe_client_session(timeout) as session:
            async with safe_get(session, url, headers=headers) as resp:
                resp.raise_for_status()
                consume_page()
                raw_body = await read_limited_bytes(resp, max_bytes=max_response_bytes)

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ProviderMalformedError(f"crt.sh returned malformed JSON for {root_domain}") from exc

        if not isinstance(data, list):
            raise ProviderMalformedError("crt.sh response must be a JSON array")
        subdomains: set[str] = set()
        for row in data:
            if not isinstance(row, dict):
                raise ProviderMalformedError("crt.sh array entries must be objects")
            for name in str(row.get("name_value", "")).split("\n"):
                name = name.strip()
                if name and not name.startswith("*"):
                    subdomains.add(name.lower())
        return sorted(subdomains, key=len)[:max_results]
