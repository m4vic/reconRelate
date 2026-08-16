"""Configuration-only and explicit free-provider live diagnostics."""

from __future__ import annotations

import asyncio
from typing import Any

from reconrelate.core.provider_execution import ProviderCallTelemetry, ProviderExecutor
from reconrelate.data_gathering.registry import PAID, ProviderInfo, ProviderRegistry
from reconrelate.security.safe_target import validate_scan_target


_LIVE_CAPABILITIES = {"whois", "basic_info", "dns", "subdomains"}


def configuration_diagnostics(registry: ProviderRegistry) -> list[dict[str, Any]]:
    return [
        info.diagnostic()
        for info in sorted(registry.infos(), key=lambda item: (item.capability, item.name))
    ]


def _result_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1


async def live_diagnostics(
    registry: ProviderRegistry,
    *,
    target: str,
    timeout_sec: float,
) -> dict[str, Any]:
    validate_scan_target(target)
    telemetry: list[ProviderCallTelemetry] = []
    executor = ProviderExecutor(timeout_sec=timeout_sec, retry_count=0, telemetry_sink=telemetry.append)

    async def probe(info: ProviderInfo) -> dict[str, Any]:
        base = info.diagnostic()
        if info.tier == PAID or info.billable:
            return {**base, "live_status": "skipped_paid", "network_tested": False}
        if not base["available"]:
            return {**base, "live_status": "skipped_unavailable", "network_tested": False}
        if info.capability not in _LIVE_CAPABILITIES:
            return {**base, "live_status": "skipped_requires_special_input", "network_tested": False}
        provider = registry.get(info.capability, name=info.name)
        if provider is None:
            return {**base, "live_status": "skipped_unavailable", "network_tested": False}

        if info.capability == "subdomains":
            operation = "search"
            call = lambda: provider.search(target, max_results=1)
            validator = lambda value: isinstance(value, list) and all(
                isinstance(item, str) or hasattr(item, "domain") for item in value
            )
        else:
            operation = "lookup"
            call = lambda: provider.lookup(target)
            validator = lambda value: value is not None and hasattr(value, "domain")
        telemetry_operation = f"doctor_{operation}"
        def call_telemetry() -> ProviderCallTelemetry | None:
            return next(
                (
                    item for item in reversed(telemetry)
                    if item.provider == info.name and item.operation == telemetry_operation
                ),
                None,
            )
        try:
            result = await executor.execute(
                run_id=None,
                provider=info.name,
                capability=info.capability,
                operation=telemetry_operation,
                call=call,
                validator=validator,
                billable=False,
                concurrency_limit=info.effective_concurrency_limit(),
                rate_limit_per_minute=info.effective_rate_limit(),
                max_response_bytes=info.max_response_bytes,
                max_result_items=info.max_result_items,
                max_requests_per_attempt=info.effective_request_limit(),
                max_pages_per_attempt=info.effective_page_limit(),
            )
            call_record = call_telemetry()
            return {
                **base,
                "live_status": "healthy" if _result_count(result) else "healthy_empty",
                "network_tested": True,
                "latency_ms": call_record.latency_ms if call_record else None,
                "result_count": _result_count(result),
            }
        except Exception as exc:
            call_record = call_telemetry()
            return {
                **base,
                "live_status": call_record.status if call_record else "error",
                "network_tested": True,
                "latency_ms": call_record.latency_ms if call_record else None,
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:300],
            }

    providers = await asyncio.gather(*[
        probe(info) for info in sorted(registry.infos(), key=lambda item: (item.capability, item.name))
    ])
    return {
        "mode": "live_free_only",
        "target": target,
        "network_calls": sum(item.upstream_requests for item in telemetry),
        "provider_attempts": sum(item.attempts for item in telemetry),
        "billable_calls": 0,
        "providers": providers,
    }
