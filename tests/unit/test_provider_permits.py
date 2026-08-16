import asyncio
from pathlib import Path

import pytest

from reconrelate.core.errors import ProviderCapacityError
from reconrelate.core.provider_execution import ProviderCallTelemetry, ProviderExecutor
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.repositories import GraphRepository


def _repos(path: Path) -> tuple[GraphRepository, GraphRepository]:
    first = get_connection(str(path))
    init_db(first)
    second = get_connection(str(path))
    init_db(second)
    return GraphRepository(first), GraphRepository(second)


def test_cross_process_concurrency_lease_blocks_then_releases(tmp_path: Path) -> None:
    first, second = _repos(tmp_path / "permits.sqlite")
    permit = first.acquire_provider_permit(
        "whoxy", owner="one", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )
    with pytest.raises(ProviderCapacityError, match="concurrency"):
        second.acquire_provider_permit(
            "whoxy", owner="two", rate_limit_per_minute=100,
            concurrency_limit=1, lease_seconds=60,
        )
    first.release_provider_permit(permit)
    second_permit = second.acquire_provider_permit(
        "whoxy", owner="two", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )
    assert second_permit


def test_expired_concurrency_lease_is_reclaimed(tmp_path: Path) -> None:
    first, second = _repos(tmp_path / "expired.sqlite")
    permit = first.acquire_provider_permit(
        "crtsh", owner="crashed", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )
    first.conn.execute(
        "UPDATE provider_concurrency_leases SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (permit,),
    )
    first.conn.commit()
    assert second.acquire_provider_permit(
        "crtsh", owner="recovery", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )


def test_shared_rate_window_rejects_calls_over_ceiling(tmp_path: Path) -> None:
    first, second = _repos(tmp_path / "rate.sqlite")
    for index, repo in enumerate((first, second)):
        permit = repo.acquire_provider_permit(
            "limited", owner=str(index), rate_limit_per_minute=2,
            concurrency_limit=2, lease_seconds=60,
        )
        repo.release_provider_permit(permit)
    with pytest.raises(ProviderCapacityError, match="rate"):
        first.acquire_provider_permit(
            "limited", owner="third", rate_limit_per_minute=2,
            concurrency_limit=2, lease_seconds=60,
        )


def test_waiter_queue_grants_capacity_in_fifo_order(tmp_path: Path) -> None:
    blocker, second = _repos(tmp_path / "fifo.sqlite")
    third = GraphRepository(get_connection(str(tmp_path / "fifo.sqlite")))
    held = blocker.acquire_provider_permit(
        "shared", owner="holder", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )
    with pytest.raises(ProviderCapacityError):
        second.acquire_provider_permit(
            "shared", owner="second", rate_limit_per_minute=100,
            concurrency_limit=1, lease_seconds=60, request_id="request-b",
        )
    with pytest.raises(ProviderCapacityError):
        third.acquire_provider_permit(
            "shared", owner="third", rate_limit_per_minute=100,
            concurrency_limit=1, lease_seconds=60, request_id="request-c",
        )

    blocker.release_provider_permit(held)
    with pytest.raises(ProviderCapacityError, match="earlier queued"):
        third.acquire_provider_permit(
            "shared", owner="third", rate_limit_per_minute=100,
            concurrency_limit=1, lease_seconds=60, request_id="request-c",
        )
    second_permit = second.acquire_provider_permit(
        "shared", owner="second", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60, request_id="request-b",
    )
    second.release_provider_permit(second_permit)
    third_permit = third.acquire_provider_permit(
        "shared", owner="third", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60, request_id="request-c",
    )
    third.release_provider_permit(third_permit)
    assert third.conn.execute("SELECT COUNT(*) FROM provider_waiters").fetchone()[0] == 0


def test_expired_waiter_does_not_block_queue(tmp_path: Path) -> None:
    first, second = _repos(tmp_path / "waiter-expiry.sqlite")
    with pytest.raises(ProviderCapacityError):
        # A rate limit of one is consumed before these queued requests arrive.
        permit = first.acquire_provider_permit(
            "limited", owner="seed", rate_limit_per_minute=1,
            concurrency_limit=1, lease_seconds=60,
        )
        first.release_provider_permit(permit)
        first.acquire_provider_permit(
            "limited", owner="stale", rate_limit_per_minute=1,
            concurrency_limit=1, lease_seconds=60, request_id="stale",
        )
    first.conn.execute(
        "UPDATE provider_waiters SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = 'stale'"
    )
    first.conn.execute("DELETE FROM provider_rate_windows WHERE provider = 'limited'")
    first.conn.commit()
    permit = second.acquire_provider_permit(
        "limited", owner="next", rate_limit_per_minute=1,
        concurrency_limit=1, lease_seconds=60, request_id="next",
    )
    assert permit


def test_local_capacity_rejection_has_zero_paid_units_and_no_circuit_failure(tmp_path: Path) -> None:
    repo, blocker = _repos(tmp_path / "billing.sqlite")
    held = blocker.acquire_provider_permit(
        "paid", owner="other-process", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )
    telemetry: list[ProviderCallTelemetry] = []
    network_calls = 0

    async def network() -> list[str]:
        nonlocal network_calls
        network_calls += 1
        return ["example.com"]

    executor = ProviderExecutor(
        timeout_sec=1,
        retry_count=0,
        telemetry_sink=telemetry.append,
        permit_acquire=lambda provider, owner, rate, concurrency, lease, request_id: repo.acquire_provider_permit(
            provider, owner=owner, rate_limit_per_minute=rate,
            concurrency_limit=concurrency, lease_seconds=lease, request_id=request_id,
        ),
        permit_release=repo.release_provider_permit,
        permit_cancel=repo.cancel_provider_waiter,
        capacity_wait_sec=0,
        shared_failure_sink=lambda provider, error, threshold, cooldown: repo.record_provider_failure(
            provider, error, threshold=threshold, cooldown_seconds=cooldown,
        ),
    )
    with pytest.raises(ProviderCapacityError):
        asyncio.run(executor.execute(
            run_id="run", provider="paid", capability="reverse_whois", operation="search",
            call=network, billable=True, concurrency_limit=1, rate_limit_per_minute=100,
        ))
    assert network_calls == 0
    assert telemetry[0].attempts == 0
    assert telemetry[0].units == 0.0
    assert repo.get_provider_states() == []
    assert repo.conn.execute("SELECT COUNT(*) FROM provider_waiters").fetchone()[0] == 0
    blocker.release_provider_permit(held)


def test_executor_waits_for_shared_capacity_without_charging_an_attempt(tmp_path: Path) -> None:
    repo, blocker = _repos(tmp_path / "wait.sqlite")
    held = blocker.acquire_provider_permit(
        "paid", owner="holder", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )
    telemetry: list[ProviderCallTelemetry] = []
    calls = 0

    async def network() -> list[str]:
        nonlocal calls
        calls += 1
        return ["example.com"]

    executor = ProviderExecutor(
        timeout_sec=1, retry_count=0, capacity_wait_sec=1,
        telemetry_sink=telemetry.append,
        permit_acquire=lambda provider, owner, rate, concurrency, lease, request_id:
            repo.acquire_provider_permit(
                provider, owner=owner, rate_limit_per_minute=rate,
                concurrency_limit=concurrency, lease_seconds=lease, request_id=request_id,
            ),
        permit_release=repo.release_provider_permit,
        permit_cancel=repo.cancel_provider_waiter,
    )

    async def scenario() -> list[str]:
        task = asyncio.create_task(executor.execute(
            run_id="run", provider="paid", capability="reverse_whois", operation="search",
            call=network, billable=True, concurrency_limit=1, rate_limit_per_minute=100,
        ))
        await asyncio.sleep(0.08)
        blocker.release_provider_permit(held)
        return await task

    assert asyncio.run(scenario()) == ["example.com"]
    assert calls == 1
    assert telemetry[0].attempts == 1
    assert telemetry[0].units == 1.0


def test_cancelled_executor_removes_durable_waiter(tmp_path: Path) -> None:
    repo, blocker = _repos(tmp_path / "cancel.sqlite")
    held = blocker.acquire_provider_permit(
        "busy", owner="holder", rate_limit_per_minute=100,
        concurrency_limit=1, lease_seconds=60,
    )
    executor = ProviderExecutor(
        timeout_sec=1, retry_count=0, capacity_wait_sec=5,
        permit_acquire=lambda provider, owner, rate, concurrency, lease, request_id:
            repo.acquire_provider_permit(
                provider, owner=owner, rate_limit_per_minute=rate,
                concurrency_limit=concurrency, lease_seconds=lease, request_id=request_id,
            ),
        permit_release=repo.release_provider_permit,
        permit_cancel=repo.cancel_provider_waiter,
    )

    async def scenario() -> None:
        task = asyncio.create_task(executor.execute(
            run_id="run", provider="busy", capability="test", operation="wait",
            call=lambda: asyncio.sleep(0, result=[]), concurrency_limit=1,
            rate_limit_per_minute=100,
        ))
        await asyncio.sleep(0.08)
        assert repo.conn.execute("SELECT COUNT(*) FROM provider_waiters").fetchone()[0] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert repo.conn.execute("SELECT COUNT(*) FROM provider_waiters").fetchone()[0] == 0
    blocker.release_provider_permit(held)
