import asyncio

import pytest

from reconrelate.core.errors import ProviderBudgetExceededError, ProviderRateLimitError
from reconrelate.core.provider_budget import consume_page, consume_request, provider_budget
from reconrelate.core.provider_execution import ProviderCallTelemetry, ProviderExecutor


def test_request_and_page_limits_fail_before_overrun() -> None:
    with provider_budget(max_requests=2, max_pages=1) as budget:
        consume_request()
        consume_request()
        consume_page()
        with pytest.raises(ProviderBudgetExceededError, match="request budget"):
            consume_request()
        with pytest.raises(ProviderBudgetExceededError, match="page budget"):
            consume_page()
    assert (budget.requests, budget.pages) == (2, 1)


def test_executor_records_nested_usage_and_resets_budget_for_retry() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    calls = 0

    async def provider_call() -> list[str]:
        nonlocal calls
        calls += 1
        consume_request()
        if calls == 1:
            raise ProviderRateLimitError("retry")
        consume_page()
        return ["ok"]

    result = asyncio.run(ProviderExecutor(
        timeout_sec=1, retry_count=1, telemetry_sink=telemetry.append,
    ).execute(
        run_id="run", provider="nested", capability="test", operation="fetch",
        call=provider_call, max_requests_per_attempt=1, max_pages_per_attempt=1,
    ))
    assert result == ["ok"]
    assert telemetry[0].attempts == 2
    assert telemetry[0].upstream_requests == 2
    assert telemetry[0].pages == 1


def test_budget_overflow_is_non_retryable_and_reports_consumed_work() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    calls = 0

    async def provider_call() -> None:
        nonlocal calls
        calls += 1
        consume_request()
        consume_request()

    executor = ProviderExecutor(timeout_sec=1, retry_count=3, telemetry_sink=telemetry.append)
    with pytest.raises(ProviderBudgetExceededError):
        asyncio.run(executor.execute(
            run_id="run", provider="bounded", capability="test", operation="fetch",
            call=provider_call, billable=True, max_requests_per_attempt=1,
        ))
    assert calls == 1
    assert telemetry[0].status == "malformed"
    assert telemetry[0].attempts == 1
    assert telemetry[0].upstream_requests == 1


def test_concurrent_budgets_are_async_local() -> None:
    async def worker(requests: int) -> tuple[int, int]:
        with provider_budget(max_requests=requests, max_pages=1) as budget:
            for _ in range(requests):
                consume_request()
                await asyncio.sleep(0)
            consume_page()
            return budget.requests, budget.pages

    async def scenario() -> list[tuple[int, int]]:
        return await asyncio.gather(worker(1), worker(3))

    assert asyncio.run(scenario()) == [(1, 1), (3, 1)]
