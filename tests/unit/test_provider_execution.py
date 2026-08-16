import asyncio

import pytest

from reconrelate.core.errors import (
    ProviderAuthError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderMalformedError,
    ProviderInputError,
    ProviderTimeoutError,
    ProviderResponseLimitError,
    RunBudgetExceededError,
    SecurityError,
)
from reconrelate.core.provider_execution import ExecutionBudget, ProviderCallTelemetry, ProviderExecutor
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository


def test_success_records_attempts_latency_and_billable_units() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    attempts = 0

    async def flaky() -> list[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ProviderError("temporary")
        return ["example.com"]

    executor = ProviderExecutor(timeout_sec=1, retry_count=1, telemetry_sink=telemetry.append)
    result = asyncio.run(executor.execute(
        run_id="run", provider="paid", capability="reverse_whois", operation="search",
        call=flaky, validator=lambda value: isinstance(value, list), billable=True,
    ))
    assert result == ["example.com"]
    assert telemetry[0].status == "success"
    assert telemetry[0].attempts == 2
    assert telemetry[0].units == 2.0


def test_run_call_ceiling_rejects_before_provider_code() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    called = False

    async def provider() -> list[str]:
        nonlocal called
        called = True
        return []

    executor = ProviderExecutor(
        timeout_sec=1, retry_count=0, telemetry_sink=telemetry.append,
        execution_budget=ExecutionBudget(max_calls=0, max_billable_units=0),
    )
    with pytest.raises(RunBudgetExceededError, match="provider-call ceiling"):
        asyncio.run(executor.execute(
            run_id="run", provider="free", capability="x", operation="read", call=provider,
        ))
    assert called is False
    assert telemetry[0].status == "budget_exceeded"
    assert telemetry[0].attempts == 0 and telemetry[0].units == 0


def test_local_provider_input_error_is_not_retried_or_counted_as_circuit_failure() -> None:
    attempts = 0

    async def invalid() -> list[str]:
        nonlocal attempts
        attempts += 1
        raise ProviderInputError("invalid local input")

    executor = ProviderExecutor(timeout_sec=1, retry_count=3, circuit_failures=1)
    with pytest.raises(ProviderInputError):
        asyncio.run(executor.execute(
            run_id="run", provider="paid", capability="reverse_whois", operation="search",
            call=invalid, billable=True,
        ))
    assert attempts == 1
    assert executor._state("paid").failures == 0


def test_billable_ceiling_reserves_retry_worst_case_before_network() -> None:
    called = False

    async def paid() -> list[str]:
        nonlocal called
        called = True
        return []

    budget = ExecutionBudget(max_calls=10, max_billable_units=1.5)
    executor = ProviderExecutor(timeout_sec=1, retry_count=1, execution_budget=budget)
    with pytest.raises(RunBudgetExceededError, match="billable-unit ceiling"):
        asyncio.run(executor.execute(
            run_id="run", provider="paid", capability="x", operation="read",
            call=paid, billable=True, units=1,
        ))
    assert called is False
    assert budget.calls_reserved == 0
    assert budget.billable_units_reserved == 0


def test_hanging_provider_times_out_with_bounded_retries() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    calls = 0

    async def hangs() -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    executor = ProviderExecutor(timeout_sec=0.01, retry_count=1, telemetry_sink=telemetry.append)
    with pytest.raises(ProviderTimeoutError):
        asyncio.run(executor.execute(
            run_id="run", provider="slow", capability="whois", operation="lookup", call=hangs,
        ))
    assert calls == 2
    assert telemetry[0].status == "timeout"
    assert telemetry[0].attempts == 2


def test_per_call_timeout_can_extend_manifest_specific_deadline() -> None:
    async def slower_provider() -> list[str]:
        await asyncio.sleep(0.03)
        return ["ok"]

    executor = ProviderExecutor(timeout_sec=0.01, retry_count=0)
    result = asyncio.run(executor.execute(
        run_id="run", provider="slow-by-design", capability="subdomains",
        operation="search", call=slower_provider, timeout_sec=0.1,
    ))
    assert result == ["ok"]


def test_malformed_result_is_not_retried() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    calls = 0

    async def malformed() -> dict:
        nonlocal calls
        calls += 1
        return {"unexpected": True}

    executor = ProviderExecutor(timeout_sec=1, retry_count=5, telemetry_sink=telemetry.append)
    with pytest.raises(ProviderMalformedError):
        asyncio.run(executor.execute(
            run_id=None, provider="bad", capability="subdomains", operation="search",
            call=malformed, validator=lambda value: isinstance(value, list),
        ))
    assert calls == 1
    assert telemetry[0].status == "malformed"


def test_circuit_opens_after_repeated_failures_without_calling_provider() -> None:
    calls = 0

    async def fails() -> None:
        nonlocal calls
        calls += 1
        raise ProviderError("down")

    executor = ProviderExecutor(
        timeout_sec=1, retry_count=0, circuit_failures=2, circuit_cooldown_sec=60,
    )
    for _ in range(2):
        with pytest.raises(ProviderError):
            asyncio.run(executor.execute(
                run_id="run", provider="down", capability="dns", operation="lookup", call=fails,
            ))
    with pytest.raises(ProviderCircuitOpenError):
        asyncio.run(executor.execute(
            run_id="run", provider="down", capability="dns", operation="lookup", call=fails,
        ))
    assert calls == 2


def test_telemetry_failure_never_retries_successful_provider_call() -> None:
    calls = 0

    async def succeeds() -> list[str]:
        nonlocal calls
        calls += 1
        return []

    def broken_sink(_: ProviderCallTelemetry) -> None:
        raise RuntimeError("storage unavailable")

    executor = ProviderExecutor(timeout_sec=1, retry_count=2, telemetry_sink=broken_sink)
    assert asyncio.run(executor.execute(
        run_id="run", provider="free", capability="subdomains", operation="search", call=succeeds,
    )) == []
    assert calls == 1


def test_http_429_is_classified_and_retried_with_bounded_attempts() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    calls = 0

    class TooManyRequests(Exception):
        status = 429

    async def limited() -> None:
        nonlocal calls
        calls += 1
        raise TooManyRequests("quota")

    executor = ProviderExecutor(timeout_sec=1, retry_count=2, telemetry_sink=telemetry.append)
    with pytest.raises(ProviderError):
        asyncio.run(executor.execute(
            run_id="run", provider="limited", capability="reverse_whois",
            operation="search", call=limited, billable=True,
        ))
    assert calls == 3
    assert telemetry[0].status == "rate_limited"
    assert telemetry[0].attempts == 3
    assert telemetry[0].units == 3.0


def test_bulkhead_enforces_provider_concurrency_limit() -> None:
    active = 0
    maximum = 0

    async def run_calls() -> None:
        nonlocal active, maximum
        executor = ProviderExecutor(timeout_sec=1, retry_count=0)

        async def call() -> list[str]:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1
            return []

        await asyncio.gather(*[
            executor.execute(
                run_id="run", provider="bounded", capability="search", operation="search",
                call=call, concurrency_limit=2,
            )
            for _ in range(6)
        ])

    asyncio.run(run_calls())
    assert maximum == 2


def test_persisted_circuit_is_shared_between_executors(tmp_path) -> None:
    path = tmp_path / "circuits.sqlite"
    first_conn = get_connection(str(path))
    init_db(first_conn)
    first_repo = GraphRepository(first_conn)
    second_conn = get_connection(str(path))
    init_db(second_conn)
    second_repo = GraphRepository(second_conn)

    def executor(repo: GraphRepository) -> ProviderExecutor:
        return ProviderExecutor(
            timeout_sec=1, retry_count=0, circuit_failures=2, circuit_cooldown_sec=60,
            shared_circuit_check=repo.provider_circuit_is_open,
            shared_success_sink=repo.record_provider_success,
            shared_failure_sink=lambda provider, error, threshold, cooldown: repo.record_provider_failure(
                provider, error, threshold=threshold, cooldown_seconds=cooldown,
            ),
        )

    calls = 0

    async def fails() -> None:
        nonlocal calls
        calls += 1
        raise ProviderError("down")

    first = executor(first_repo)
    for _ in range(2):
        with pytest.raises(ProviderError):
            asyncio.run(first.execute(
                run_id="run", provider="shared", capability="whois", operation="lookup", call=fails,
            ))
    assert first_repo.provider_circuit_is_open("shared")
    with pytest.raises(ProviderCircuitOpenError):
        asyncio.run(executor(second_repo).execute(
            run_id="run", provider="shared", capability="whois", operation="lookup", call=fails,
        ))
    assert calls == 2
    first_conn.close()
    second_conn.close()


def test_auth_failure_opens_circuit_immediately_to_protect_paid_usage() -> None:
    calls = 0

    async def unauthorized() -> None:
        nonlocal calls
        calls += 1
        raise ProviderAuthError("bad key")

    executor = ProviderExecutor(timeout_sec=1, retry_count=3, circuit_failures=5)
    with pytest.raises(ProviderAuthError):
        asyncio.run(executor.execute(
            run_id="run", provider="paid", capability="reverse_whois",
            operation="search", call=unauthorized, billable=True,
        ))
    with pytest.raises(ProviderCircuitOpenError):
        asyncio.run(executor.execute(
            run_id="run", provider="paid", capability="reverse_whois",
            operation="search", call=unauthorized, billable=True,
        ))
    assert calls == 1


def test_oversized_result_is_rejected_without_retry() -> None:
    telemetry: list[ProviderCallTelemetry] = []
    calls = 0

    async def too_many() -> list[str]:
        nonlocal calls
        calls += 1
        return ["one", "two", "three"]

    executor = ProviderExecutor(timeout_sec=1, retry_count=3, telemetry_sink=telemetry.append)
    with pytest.raises(ProviderResponseLimitError, match="3 items"):
        asyncio.run(executor.execute(
            run_id="run", provider="oversized", capability="search", operation="search",
            call=too_many, max_result_items=2,
        ))
    assert calls == 1
    assert telemetry[0].status == "malformed"
    assert telemetry[0].attempts == 1


def test_normalized_result_byte_limit_is_enforced() -> None:
    async def too_large() -> dict[str, str]:
        return {"body": "x" * 200}

    executor = ProviderExecutor(timeout_sec=1, retry_count=0)
    with pytest.raises(ProviderResponseLimitError, match="normalized provider result"):
        asyncio.run(executor.execute(
            run_id="run", provider="oversized", capability="lookup", operation="lookup",
            call=too_large, max_response_bytes=50,
        ))


def test_security_policy_failure_is_never_retried() -> None:
    calls = 0

    async def blocked() -> None:
        nonlocal calls
        calls += 1
        raise SecurityError("private DNS answer")

    executor = ProviderExecutor(timeout_sec=1, retry_count=5)
    with pytest.raises(ProviderError, match="security policy"):
        asyncio.run(executor.execute(
            run_id="run", provider="blocked", capability="http", operation="lookup", call=blocked,
        ))
    assert calls == 1
