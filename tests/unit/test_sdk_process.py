import asyncio
import time

import pytest

from reconrelate.core.errors import ProviderError, ProviderTimeoutError
from reconrelate.core.sdk_process import run_sdk_operation
from reconrelate.data_gathering.reverse_whois_provider import ReverseWhoisProvider
from reconrelate.data_gathering.whois_provider import WhoisProvider
from reconrelate.data_gathering.dns_provider import DNSProvider
from reconrelate.core.types import Identifier


def test_real_worker_protocol_uses_stdin_and_strips_sensitive_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-worker")
    result = asyncio.run(run_sdk_operation(
        "protocol_health", {"echo": "hello"}, timeout_sec=2
    ))
    assert result["echo"] == "hello"
    assert isinstance(result["pid"], int)
    assert result["sensitive_environment_present"] is False


def test_real_worker_is_terminated_at_deadline() -> None:
    started = time.monotonic()
    with pytest.raises(ProviderTimeoutError):
        asyncio.run(run_sdk_operation(
            "protocol_health", {"delay_ms": 2_000}, timeout_sec=0.05
        ))
    assert time.monotonic() - started < 1.5


def test_cancelled_worker_is_cleaned_up_promptly() -> None:
    async def scenario() -> None:
        task = asyncio.create_task(run_sdk_operation(
            "protocol_health", {"delay_ms": 2_000}, timeout_sec=5
        ))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(scenario())
    assert time.monotonic() - started < 1.5


def test_worker_rejects_unknown_operation_as_provider_error() -> None:
    with pytest.raises(ProviderError, match="unknown SDK operation"):
        asyncio.run(run_sdk_operation("not-allowed", {}, timeout_sec=2))


def test_whois_unavailable_does_not_fabricate_identifiers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(*args, **kwargs):
        return {"available": False, "record": {}}

    monkeypatch.setattr("reconrelate.data_gathering.whois_provider.run_sdk_operation", unavailable)
    result = asyncio.run(WhoisProvider().lookup("example.com"))
    assert result.registrant_email == ""
    assert result.nameservers == []
    assert result.raw == {"source": "python-whois", "available": False}


def test_whois_worker_record_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def record(*args, **kwargs):
        return {"available": True, "record": {
            "name": "Registrant", "org": "Acme Inc", "emails": ["ops@acme.example"],
            "name_servers": ["NS2.EXAMPLE.", "ns1.example"],
        }}

    monkeypatch.setattr("reconrelate.data_gathering.whois_provider.run_sdk_operation", record)
    result = asyncio.run(WhoisProvider().lookup("example.com"))
    assert result.registrant_org == "Acme Inc"
    assert result.registrant_email == "ops@acme.example"
    assert result.nameservers == ["ns1.example", "ns2.example"]


def test_duckduckgo_worker_results_are_normalized_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def results(*args, **kwargs):
        return {"available": True, "results": [
            {"href": "https://acme-assets.com/page", "body": "acme-assets.com", "title": ""},
        ]}

    monkeypatch.setattr(
        "reconrelate.data_gathering.reverse_whois_provider.run_sdk_operation", results
    )
    domains = asyncio.run(ReverseWhoisProvider().search(Identifier("org", "Acme"), 5))
    assert domains == ["acme-assets.com"]


def test_dns_worker_usage_and_record_are_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dns(*args, **kwargs):
        return {
            "requests": 2,
            "pages": 2,
            "record": {
                "domain": "example.com",
                "a_records": ["93.184.216.34"],
                "unexpected": "ignored",
            },
        }

    monkeypatch.setattr("reconrelate.data_gathering.dns_provider.run_sdk_operation", dns)
    result = asyncio.run(DNSProvider().lookup("example.com"))
    assert result.domain == "example.com"
    assert result.a_records == ["93.184.216.34"]
