"""SSRF-resistant aiohttp sessions with validating DNS and redirect handling."""

from __future__ import annotations

import asyncio
import inspect
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from reconrelate.core.errors import SecurityError
from reconrelate.core.provider_budget import consume_request
from reconrelate.security.safe_target import validate_resolved_ip, validate_scan_target


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def validate_http_url(url: str) -> None:
    if len(url) > 4096:
        raise SecurityError("outbound URL exceeds 4096 characters")
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SecurityError(f"outbound URL scheme is not allowed: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SecurityError("outbound URL requires a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise SecurityError("credentials in outbound URLs are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise SecurityError("outbound URL has an invalid port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise SecurityError("outbound URL port is outside 1..65535")
    validate_scan_target(parsed.hostname)


class SafeResolver(aiohttp.abc.AbstractResolver):
    """Resolve and validate the exact addresses returned to aiohttp's connector."""

    def __init__(self, lookup: Callable[..., Any] | None = None) -> None:
        self._lookup = lookup

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[dict[str, Any]]:
        validate_scan_target(host)
        if self._lookup is None:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM, family=family
            )
        else:
            infos = self._lookup(host, port, family)
            if inspect.isawaitable(infos):
                infos = await infos
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for resolved_family, _, proto, _, sockaddr in infos:
            address = str(sockaddr[0])
            resolved_port = int(sockaddr[1]) if len(sockaddr) > 1 else port
            validate_resolved_ip(address)
            key = (address, resolved_port)
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "hostname": host,
                "host": address,
                "port": resolved_port,
                "family": resolved_family,
                "proto": proto,
                "flags": 0,
            })
        if not results:
            raise OSError(f"no public DNS addresses returned for {host}")
        return results

    async def close(self) -> None:
        return None


@asynccontextmanager
async def safe_client_session(timeout: aiohttp.ClientTimeout) -> AsyncIterator[aiohttp.ClientSession]:
    connector = aiohttp.TCPConnector(resolver=SafeResolver())
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        yield session


@asynccontextmanager
async def safe_get(
    session: Any,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_redirects: int = 5,
) -> AsyncIterator[Any]:
    current = url
    current_params = params
    response = None
    for redirect_count in range(max(0, max_redirects) + 1):
        validate_http_url(current)
        consume_request()
        response = await session.get(
            current,
            params=current_params,
            headers=headers,
            allow_redirects=False,
        )
        current_params = None
        if response.status not in _REDIRECT_STATUSES:
            try:
                yield response
            finally:
                response.release()
            return
        location = response.headers.get("Location")
        base_url = str(getattr(response, "url", current))
        response.release()
        if not location:
            raise SecurityError("redirect response is missing Location")
        if redirect_count >= max_redirects:
            raise SecurityError(f"outbound redirect limit exceeded ({max_redirects})")
        next_url = urljoin(base_url, location)
        validate_http_url(next_url)
        previous_scheme = urlsplit(current).scheme.lower()
        next_scheme = urlsplit(next_url).scheme.lower()
        if previous_scheme == "https" and next_scheme == "http":
            raise SecurityError("HTTPS-to-HTTP redirect downgrade is not allowed")
        current = next_url
    raise SecurityError("outbound redirect handling failed")
