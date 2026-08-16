import asyncio
import socket

import pytest

from reconrelate.core.errors import ProviderBudgetExceededError, SecurityError
from reconrelate.core.provider_budget import provider_budget
from reconrelate.security.safe_http import SafeResolver, safe_get, validate_http_url


def _info(address: str, port: int = 443):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (address, port))


def test_safe_resolver_returns_public_addresses_used_by_connector() -> None:
    resolver = SafeResolver(lambda host, port, family: [_info("93.184.216.34", port)])
    result = asyncio.run(resolver.resolve("example.com", 443, socket.AF_UNSPEC))
    assert result[0]["host"] == "93.184.216.34"
    assert result[0]["hostname"] == "example.com"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1"])
def test_safe_resolver_rejects_private_or_special_dns_answers(address: str) -> None:
    resolver = SafeResolver(lambda host, port, family: [_info(address, port)])
    with pytest.raises(SecurityError, match="non-public IP"):
        asyncio.run(resolver.resolve("public-looking.example", 443, socket.AF_UNSPEC))


def test_mixed_public_private_dns_answer_fails_closed() -> None:
    resolver = SafeResolver(lambda host, port, family: [
        _info("93.184.216.34", port), _info("127.0.0.1", port),
    ])
    with pytest.raises(SecurityError):
        asyncio.run(resolver.resolve("example.com", 443, socket.AF_UNSPEC))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://user:password@example.com/",
    "http://127.0.0.1/",
    "http://metadata.google.internal/latest",
])
def test_url_policy_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(SecurityError):
        validate_http_url(url)


class _Response:
    def __init__(self, status: int, url: str, location: str | None = None) -> None:
        self.status = status
        self.url = url
        self.headers = {} if location is None else {"Location": location}
        self.released = False

    def release(self) -> None:
        self.released = True


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs):
        self.calls.append(url)
        return self.responses.pop(0)


def test_redirect_target_is_validated_before_second_request() -> None:
    first = _Response(302, "https://example.com", "http://127.0.0.1/admin")
    session = _Session([first])

    async def run() -> None:
        async with safe_get(session, "https://example.com"):
            pass

    with pytest.raises(SecurityError, match="hostname denylist|non-public|downgrade"):
        asyncio.run(run())
    assert first.released
    assert session.calls == ["https://example.com"]


def test_relative_redirect_is_followed_with_bounded_manual_flow() -> None:
    first = _Response(302, "https://example.com/start", "/next")
    final = _Response(200, "https://example.com/next")
    session = _Session([first, final])

    async def run() -> None:
        async with safe_get(session, "https://example.com/start") as response:
            assert response is final

    asyncio.run(run())
    assert session.calls == ["https://example.com/start", "https://example.com/next"]
    assert first.released and final.released


def test_redirect_consumes_request_budget_before_next_network_call() -> None:
    first = _Response(302, "https://example.com/start", "/next")
    session = _Session([first, _Response(200, "https://example.com/next")])

    async def run() -> None:
        with provider_budget(max_requests=1, max_pages=1):
            async with safe_get(session, "https://example.com/start"):
                pass

    with pytest.raises(ProviderBudgetExceededError, match="request budget"):
        asyncio.run(run())
    assert session.calls == ["https://example.com/start"]
    assert first.released


def test_https_redirect_downgrade_is_rejected() -> None:
    response = _Response(301, "https://example.com", "http://example.com")
    session = _Session([response])

    async def run() -> None:
        async with safe_get(session, "https://example.com"):
            pass

    with pytest.raises(SecurityError, match="downgrade"):
        asyncio.run(run())


def test_redirect_loop_is_stopped_at_configured_limit() -> None:
    first = _Response(302, "https://example.com/one", "/two")
    second = _Response(302, "https://example.com/two", "/one")
    session = _Session([first, second])

    async def run() -> None:
        async with safe_get(session, "https://example.com/one", max_redirects=1):
            pass

    with pytest.raises(SecurityError, match="redirect limit"):
        asyncio.run(run())
    assert len(session.calls) == 2
    assert first.released and second.released
