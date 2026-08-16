import asyncio
import json
from pathlib import Path
import pytest

from reconrelate.core.types import Identifier
from reconrelate.core.errors import (
    ProviderAuthError, ProviderError, ProviderMalformedError, ProviderRateLimitError,
    ProviderResponseLimitError, ProviderInputError,
)
from reconrelate.data_gathering.registry import default_registry
from reconrelate.data_gathering.reverse_whois_provider import ReverseWhoisProvider
from reconrelate.data_gathering.whoxy_reverse_whois_provider import WhoxyReverseWhoisProvider

FIXTURES = Path(__file__).parents[1] / "fixtures"


class FakeWhoxy(WhoxyReverseWhoisProvider):
    """Whoxy provider with canned API responses (no network, no real key)."""

    def __init__(self, response: dict) -> None:
        super().__init__(api_key="TEST-KEY")
        self._response = response
        self.last_params: dict | None = None

    async def _get_json(self, params: dict) -> dict:
        self.last_params = params
        return self._response


def _ok(domains: list[str]) -> dict:
    return {"status": 1, "search_result": [{"domain_name": d} for d in domains]}


def test_org_pivot_uses_company_field_and_extracts_domains() -> None:
    provider = FakeWhoxy(_ok(["acme.com", "acme.io"]))
    out = asyncio.run(provider.search(Identifier(id_type="org", value="Acme Inc"), max_results=10))
    assert out == ["acme.com", "acme.io"]
    assert provider.last_params["company"] == "Acme Inc"
    assert provider.last_params["reverse"] == "whois"
    assert provider.last_params["mode"] == "micro"


def test_official_micro_fixture_contract() -> None:
    payload = json.loads((FIXTURES / "whoxy_reverse_micro_success.json").read_text(encoding="utf-8"))
    provider = FakeWhoxy(payload)
    result = asyncio.run(provider.search(Identifier(id_type="name", value="Example Owner"), 5))
    assert result == ["example.com", "example.net"]


def test_official_invalid_key_fixture_contract() -> None:
    payload = json.loads((FIXTURES / "whoxy_invalid_key.json").read_text(encoding="utf-8"))
    provider = FakeWhoxy(payload)
    with pytest.raises(ProviderAuthError):
        asyncio.run(provider.search(Identifier(id_type="name", value="Example Owner"), 5))


def test_official_account_balance_fixture_returns_only_reverse_whois_quota() -> None:
    payload = json.loads((FIXTURES / "whoxy_account_balance.json").read_text(encoding="utf-8"))
    provider = FakeWhoxy(payload)
    result = asyncio.run(provider.balance())
    assert result.provider == "whoxy"
    assert result.capability == "reverse_whois"
    assert result.remaining == 700
    assert result.unit == "credit"
    assert result.authoritative is True
    assert result.billing_effect == "unknown"
    assert provider.last_params == {"key": "TEST-KEY", "account": "balance"}


@pytest.mark.parametrize("payload", [
    {},
    {"status": True, "reverse_whois_balance": 1},
    {"status": 1},
    {"status": 1, "reverse_whois_balance": True},
    {"status": 1, "reverse_whois_balance": 1.5},
    {"status": 1, "reverse_whois_balance": "7"},
    {"status": 1, "reverse_whois_balance": "not-a-number"},
    {"status": 1, "reverse_whois_balance": -1},
])
def test_malformed_account_balance_is_rejected(payload) -> None:  # noqa: ANN001
    with pytest.raises(ProviderMalformedError):
        asyncio.run(FakeWhoxy(payload).balance())


def test_balance_errors_are_typed_and_secret_safe() -> None:
    with pytest.raises(ProviderAuthError) as auth_error:
        asyncio.run(FakeWhoxy({"status": 0, "status_reason": "Invalid API key TEST-KEY"}).balance())
    assert "TEST-KEY" not in str(auth_error.value)
    with pytest.raises(ProviderRateLimitError):
        asyncio.run(FakeWhoxy({"status": 0, "status_reason": "Credit limit reached"}).balance())


def test_email_pivot_uses_email_field() -> None:
    provider = FakeWhoxy(_ok(["corp.com"]))
    asyncio.run(provider.search(Identifier(id_type="email", value="admin@corp.com"), max_results=5))
    assert provider.last_params["email"] == "admin@corp.com"


def test_unsupported_identifier_types_return_empty_without_calling_api() -> None:
    for id_type in ("ns", "phone", "tracker"):
        provider = FakeWhoxy(_ok(["should-not-appear.com"]))
        assert asyncio.run(provider.search(Identifier(id_type=id_type, value="x"), max_results=5)) == []
        assert provider.last_params is None  # never hit the API


def test_invalid_key_raises_typed_auth_error() -> None:
    provider = FakeWhoxy({"status": 0, "status_reason": "Invalid API Key"})
    with pytest.raises(ProviderAuthError):
        asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=5))


def test_no_results_status_is_a_valid_empty_result() -> None:
    provider = FakeWhoxy({"status": 0, "status_reason": "No records found"})
    assert asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=5)) == []


def test_results_are_deduped_normalized_and_capped() -> None:
    provider = FakeWhoxy(_ok(["Acme.com", "acme.com", "acme.io", "acme.net"]))
    out = asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=2))
    assert out == ["acme.com", "acme.io"]  # dedup (case-insensitive) then cap at 2


def test_no_api_key_is_typed_auth_failure() -> None:
    provider = WhoxyReverseWhoisProvider(api_key="")
    with pytest.raises(ProviderAuthError, match="WHOXY_API_KEY"):
        asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=5))


def test_zero_requested_results_is_zero_call_even_without_key() -> None:
    provider = WhoxyReverseWhoisProvider(api_key="")
    assert asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=0)) == []


@pytest.mark.parametrize("payload", [
    {},
    {"status": True, "search_result": []},
    {"status": 1, "search_result": {}},
    {"status": 1, "search_result": ["not-an-object"]},
    {"status": 1, "search_result": [], "api_query": "whois"},
    {"status": 1, "search_result": [{}], "total_results": 0},
])
def test_malformed_success_response_fails_the_complete_result(payload) -> None:  # noqa: ANN001
    provider = FakeWhoxy(payload)
    with pytest.raises(ProviderMalformedError):
        asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=5))


def test_micro_row_ceiling_is_enforced() -> None:
    provider = FakeWhoxy({
        "status": 1,
        "search_result": [{"domain_name": f"d{i}.example"} for i in range(2501)],
    })
    with pytest.raises(ProviderResponseLimitError, match="2,500"):
        asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=5))


def test_unknown_api_error_is_not_misreported_as_empty() -> None:
    provider = FakeWhoxy({"status": 0, "status_reason": "Temporary backend failure"})
    with pytest.raises(ProviderError, match="backend failure"):
        asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=5))


def test_quota_error_is_typed_and_api_key_is_redacted() -> None:
    provider = FakeWhoxy({"status": 0, "status_reason": "Credit limit for TEST-KEY"})
    with pytest.raises(ProviderRateLimitError) as error:
        asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), max_results=5))
    assert "TEST-KEY" not in str(error.value)


def test_oversized_identifier_fails_before_api_call() -> None:
    provider = FakeWhoxy(_ok([]))
    with pytest.raises(ProviderInputError, match="320"):
        asyncio.run(provider.search(Identifier(id_type="org", value="x" * 321), max_results=5))
    assert provider.last_params is None


class _AsyncContext:
    def __init__(self, value) -> None:  # noqa: ANN001
        self.value = value

    async def __aenter__(self):  # noqa: ANN204
        return self.value

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


class _HttpResponse:
    def __init__(self, status: int) -> None:
        self.status = status


@pytest.mark.parametrize("status,error_type", [
    (401, ProviderAuthError), (403, ProviderAuthError),
    (429, ProviderRateLimitError), (503, ProviderError),
])
def test_http_failures_are_typed_without_leaking_query_key(monkeypatch, status, error_type) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        "reconrelate.data_gathering.whoxy_reverse_whois_provider.safe_client_session",
        lambda timeout: _AsyncContext(object()),
    )
    monkeypatch.setattr(
        "reconrelate.data_gathering.whoxy_reverse_whois_provider.safe_get",
        lambda session, url, **kwargs: _AsyncContext(_HttpResponse(status)),
    )
    provider = WhoxyReverseWhoisProvider(api_key="SECRET-QUERY-KEY")
    with pytest.raises(error_type) as error:
        asyncio.run(provider.search(Identifier(id_type="org", value="Acme"), 5))
    assert "SECRET-QUERY-KEY" not in str(error.value)


def test_registry_prefers_whoxy_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHOXY_API_KEY", "TEST-KEY")
    assert isinstance(default_registry().get("reverse_whois"), WhoxyReverseWhoisProvider)


def test_whoxy_registry_exposes_fail_closed_data_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHOXY_API_KEY", "TEST-KEY")
    registry = default_registry()
    info = next(item for item in registry.infos() if item.name == "whoxy")
    assert info.diagnostic()["data_policy"] == {
        "raw_retention": "hash_only",
        "normalized_retention": "run",
        "cross_run_cache": False,
        "export_scope": "derived_only",
        "version": "provider-data-use-v1",
        "terms_url": "https://www.whoxy.com/terms.php",
        "reviewed_at": "2026-08-14",
    }
    assert getattr(registry.get("reverse_whois", "whoxy"), "__reconrelate_data_policy__") == info.data_policy


def test_registry_falls_back_to_free_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHOXY_API_KEY", raising=False)
    assert isinstance(default_registry().get("reverse_whois"), ReverseWhoisProvider)


def test_provider_kill_switch_removes_source_and_doctor_reports_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHOXY_API_KEY", "TEST-KEY")
    monkeypatch.setenv("RECONRELATE_DISABLE_PROVIDERS", "whoxy")
    registry = default_registry()
    assert isinstance(registry.get("reverse_whois"), ReverseWhoisProvider)
    diagnostic = next(info.diagnostic() for info in registry.infos() if info.name == "whoxy")
    assert diagnostic["disabled"] is True
    assert diagnostic["status"] == "disabled"


def test_provider_safety_ceiling_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONRELATE_PROVIDER_WHOXY_CONCURRENCY", "2")
    monkeypatch.setenv("RECONRELATE_PROVIDER_WHOXY_RATE_PER_MINUTE", "17")
    monkeypatch.setenv("RECONRELATE_PROVIDER_WHOXY_MAX_REQUESTS", "3")
    monkeypatch.setenv("RECONRELATE_PROVIDER_WHOXY_MAX_PAGES", "2")
    info = next(item for item in default_registry().infos() if item.name == "whoxy")
    diagnostic = info.diagnostic()
    assert diagnostic["concurrency_limit"] == 2
    assert diagnostic["rate_limit_per_minute"] == 17
    assert diagnostic["max_requests_per_attempt"] == 3
    assert diagnostic["max_pages_per_attempt"] == 2
