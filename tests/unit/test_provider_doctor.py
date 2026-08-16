import asyncio

import pytest

from reconrelate.core.types import BasicIntelRecord, WhoisRecord
from reconrelate.core.provider_budget import consume_page, consume_request
from reconrelate.data_gathering.doctor import live_diagnostics
from reconrelate.data_gathering.registry import PAID, ProviderInfo, ProviderRegistry
from reconrelate.security.safe_target import SecurityError
from reconrelate.core.provider_data_policy import WHOXY_DATA_POLICY


def test_live_doctor_runs_supported_free_probes_concurrently_and_skips_paid() -> None:
    registry = ProviderRegistry()
    active = 0
    maximum = 0
    paid_constructed = False

    class Whois:
        async def lookup(self, domain: str) -> WhoisRecord:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            consume_request()
            consume_page()
            await asyncio.sleep(0.02)
            active -= 1
            return WhoisRecord(domain=domain)

    class Basic:
        async def lookup(self, domain: str) -> BasicIntelRecord:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            consume_request()
            consume_page()
            await asyncio.sleep(0.02)
            active -= 1
            return BasicIntelRecord(domain=domain)

    def paid_factory():
        nonlocal paid_constructed
        paid_constructed = True
        raise AssertionError("paid provider must not be instantiated")

    registry.register(ProviderInfo(
        "whois", "free-whois", Whois, operations=("lookup",), result_contract="WhoisRecord",
    ))
    registry.register(ProviderInfo(
        "basic_info", "free-basic", Basic, operations=("lookup",),
        result_contract="BasicIntelRecord",
    ))
    registry.register(ProviderInfo(
        "reverse_whois", "paid-reverse", paid_factory, tier=PAID, billable=True,
        operations=("search",), result_contract="list[domain]",
        data_policy=WHOXY_DATA_POLICY,
    ))

    result = asyncio.run(live_diagnostics(registry, target="example.com", timeout_sec=1))
    assert result["billable_calls"] == 0
    assert result["network_calls"] == 2
    assert maximum == 2
    assert not paid_constructed
    statuses = {item["name"]: item["live_status"] for item in result["providers"]}
    assert statuses == {
        "free-basic": "healthy",
        "free-whois": "healthy",
        "paid-reverse": "skipped_paid",
    }


def test_live_doctor_reports_empty_error_and_special_input_skip() -> None:
    registry = ProviderRegistry()

    class EmptySubdomains:
        async def search(self, domain: str, max_results: int = 1) -> list[str]:
            consume_request()
            consume_page()
            return []

    class BrokenDNS:
        async def lookup(self, domain: str):
            consume_request()
            raise RuntimeError("resolver down")

    class Acquisition:
        pass

    registry.register(ProviderInfo("subdomains", "empty", EmptySubdomains))
    registry.register(ProviderInfo("dns", "broken", BrokenDNS))
    registry.register(ProviderInfo("acquisitions", "needs-org", Acquisition))
    result = asyncio.run(live_diagnostics(registry, target="example.com", timeout_sec=1))
    statuses = {item["name"]: item["live_status"] for item in result["providers"]}
    assert statuses["empty"] == "healthy_empty"
    assert statuses["broken"] == "error"
    assert statuses["needs-org"] == "skipped_requires_special_input"
    assert result["network_calls"] == 2


def test_live_doctor_rejects_unsafe_target_before_constructing_providers() -> None:
    registry = ProviderRegistry()
    with pytest.raises(SecurityError):
        asyncio.run(live_diagnostics(registry, target="localhost", timeout_sec=1))
