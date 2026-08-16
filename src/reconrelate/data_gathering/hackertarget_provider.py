"""Fallback subdomain enumeration through HackerTarget's free API."""

from __future__ import annotations

import logging

import aiohttp

from reconrelate.core.errors import ProviderRateLimitError
from reconrelate.core.http import read_limited_text
from reconrelate.core.provider_budget import consume_page
from reconrelate.security.safe_target import validate_scan_target
from reconrelate.security.safe_http import safe_client_session, safe_get

logger = logging.getLogger(__name__)


class HackerTargetProvider:
    URL = "https://api.hackertarget.com/hostsearch/"

    async def search(self, domain: str, max_results: int = 15) -> list[str]:
        validate_scan_target(domain)
        timeout = aiohttp.ClientTimeout(total=10)
        async with safe_client_session(timeout) as session:
            async with safe_get(session, self.URL, params={"q": domain}) as resp:
                resp.raise_for_status()
                consume_page()
                text = (await read_limited_text(resp, max_bytes=1_048_576)).strip()

        if text.startswith("error") or not text:
            logger.warning("HackerTarget returned: %s", text[:100])
            if "limit" in text.lower() or "quota" in text.lower():
                raise ProviderRateLimitError(text[:200])
            return []

        subdomains: set[str] = set()
        for line in text.splitlines():
            parts = line.split(",")
            if parts:
                name = parts[0].strip().lower()
                if name and not name.startswith("*"):
                    subdomains.add(name)
        return sorted(subdomains, key=len)[:max_results]
