"""Bounded asynchronous provider execution with telemetry and circuit breaking."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

from reconrelate.core.errors import (
    ProviderAuthError,
    ProviderCapacityError,
    ProviderCircuitOpenError,
    ProviderError,
    ProviderMalformedError,
    ProviderInputError,
    ProviderRateLimitError,
    ProviderResponseLimitError,
    RunBudgetExceededError,
    ProviderTimeoutError,
    SecurityError,
)
from reconrelate.core.provider_budget import provider_budget

T = TypeVar("T")
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _oversized_collection(value: object, limit: int) -> int | None:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        if len(value) > limit:
            return len(value)
        for nested in value.values():
            oversized = _oversized_collection(nested, limit)
            if oversized is not None:
                return oversized
    elif isinstance(value, (list, tuple, set)):
        if len(value) > limit:
            return len(value)
        for nested in value:
            oversized = _oversized_collection(nested, limit)
            if oversized is not None:
                return oversized
    return None


@dataclass(frozen=True, slots=True)
class ProviderCallTelemetry:
    run_id: str | None
    provider: str
    capability: str
    operation: str
    status: str
    attempts: int
    latency_ms: int
    billable: bool
    units: float
    error_class: str | None
    error_message: str | None
    started_at: str
    completed_at: str
    upstream_requests: int = 0
    pages: int = 0


@dataclass(slots=True)
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


@dataclass(slots=True)
class ExecutionBudget:
    max_calls: int
    max_billable_units: float
    calls_reserved: int = 0
    billable_units_reserved: float = 0.0

    def reserve(self, *, billable: bool, worst_case_units: float) -> None:
        next_calls = self.calls_reserved + 1
        next_units = self.billable_units_reserved + (worst_case_units if billable else 0.0)
        if self.max_calls >= 0 and next_calls > self.max_calls:
            raise RunBudgetExceededError(
                f"run provider-call ceiling exceeded ({self.max_calls})"
            )
        if self.max_billable_units >= 0 and next_units > self.max_billable_units + 1e-9:
            raise RunBudgetExceededError(
                f"run billable-unit ceiling exceeded ({self.max_billable_units:g})"
            )
        self.calls_reserved = next_calls
        self.billable_units_reserved = next_units

    def snapshot(self) -> dict[str, float | int]:
        return {
            "max_calls": self.max_calls,
            "calls_reserved": self.calls_reserved,
            "max_billable_units": self.max_billable_units,
            "billable_units_reserved": self.billable_units_reserved,
        }


class ProviderExecutor:
    def __init__(
        self,
        *,
        timeout_sec: float,
        retry_count: int,
        circuit_failures: int = 3,
        circuit_cooldown_sec: float = 60.0,
        telemetry_sink: Callable[[ProviderCallTelemetry], None] | None = None,
        shared_circuit_check: Callable[[str], bool] | None = None,
        shared_success_sink: Callable[[str], None] | None = None,
        shared_failure_sink: Callable[[str, Exception, int, float], None] | None = None,
        permit_acquire: Callable[[str, str, int, int, float, str], str] | None = None,
        permit_release: Callable[[str], None] | None = None,
        permit_cancel: Callable[[str], None] | None = None,
        capacity_wait_sec: float = 5.0,
        execution_budget: ExecutionBudget | None = None,
    ) -> None:
        self.timeout_sec = max(float(timeout_sec), 0.01)
        self.retry_count = max(int(retry_count), 0)
        self.circuit_failures = max(int(circuit_failures), 1)
        self.circuit_cooldown_sec = max(float(circuit_cooldown_sec), 0.0)
        self.telemetry_sink = telemetry_sink
        self.shared_circuit_check = shared_circuit_check
        self.shared_success_sink = shared_success_sink
        self.shared_failure_sink = shared_failure_sink
        self.permit_acquire = permit_acquire
        self.permit_release = permit_release
        self.permit_cancel = permit_cancel
        self.capacity_wait_sec = max(0.0, float(capacity_wait_sec))
        self.execution_budget = execution_budget
        self.owner = uuid.uuid4().hex
        self._circuits: dict[str, _CircuitState] = {}
        self._bulkheads: dict[tuple[str, int], asyncio.Semaphore] = {}

    def _state(self, provider: str) -> _CircuitState:
        return self._circuits.setdefault(provider, _CircuitState())

    def _circuit_is_open(self, provider: str) -> bool:
        if self.shared_circuit_check:
            try:
                if self.shared_circuit_check(provider):
                    return True
            except Exception:
                logger.exception("shared provider circuit check failed")
        state = self._state(provider)
        if state.opened_at is None:
            return False
        if time.monotonic() - state.opened_at >= self.circuit_cooldown_sec:
            state.opened_at = None
            state.failures = 0
            return False
        return True

    def _mark_success(self, provider: str) -> None:
        self._state(provider).failures = 0
        if self.shared_success_sink:
            try:
                self.shared_success_sink(provider)
            except Exception:
                logger.exception("shared provider success state failed")

    def _mark_failure(self, provider: str, error: Exception) -> None:
        if isinstance(error, ProviderCapacityError):
            return
        state = self._state(provider)
        state.failures += 1
        effective_threshold = 1 if isinstance(error, ProviderAuthError) else self.circuit_failures
        if state.failures >= effective_threshold:
            state.opened_at = time.monotonic()
        if self.shared_failure_sink:
            try:
                self.shared_failure_sink(
                    provider, error, effective_threshold, self.circuit_cooldown_sec
                )
            except Exception:
                logger.exception("shared provider failure state failed")

    @staticmethod
    def _classify(exc: Exception) -> tuple[str, ProviderError, bool]:
        if isinstance(exc, SecurityError):
            return "error", ProviderError(f"security policy blocked provider request: {exc}"), False
        if isinstance(exc, ProviderAuthError):
            return "auth_error", exc, False
        if isinstance(exc, ProviderInputError):
            return "error", exc, False
        if isinstance(exc, ProviderMalformedError):
            return "malformed", exc, False
        if isinstance(exc, ProviderCapacityError):
            return "rate_limited", exc, False
        if isinstance(exc, ProviderRateLimitError):
            return "rate_limited", exc, True
        if isinstance(exc, (ProviderTimeoutError, asyncio.TimeoutError)):
            wrapped = exc if isinstance(exc, ProviderTimeoutError) else ProviderTimeoutError(
                "provider deadline exceeded"
            )
            return "timeout", wrapped, True
        status = getattr(exc, "status", None)
        if status in {401, 403}:
            return "auth_error", ProviderAuthError(str(exc)), False
        if status == 429:
            return "rate_limited", ProviderRateLimitError(str(exc)), True
        wrapped = exc if isinstance(exc, ProviderError) else ProviderError(str(exc))
        return "error", wrapped, True

    def _emit(self, telemetry: ProviderCallTelemetry) -> None:
        if self.telemetry_sink:
            try:
                self.telemetry_sink(telemetry)
            except Exception:
                logger.exception("provider telemetry sink failed")

    async def execute(
        self,
        *,
        run_id: str | None,
        provider: str,
        capability: str,
        operation: str,
        call: Callable[[], Awaitable[T]],
        validator: Callable[[T], bool] | None = None,
        billable: bool = False,
        units: float = 1.0,
        concurrency_limit: int = 4,
        rate_limit_per_minute: int = 60,
        max_response_bytes: int = 1_048_576,
        max_result_items: int = 1_000,
        max_requests_per_attempt: int = 1,
        max_pages_per_attempt: int = 1,
        timeout_sec: float | None = None,
    ) -> T:
        started_at = _now_iso()
        started = time.perf_counter()
        if self.execution_budget is not None:
            try:
                self.execution_budget.reserve(
                    billable=billable,
                    worst_case_units=max(0.0, float(units)) * (self.retry_count + 1),
                )
            except RunBudgetExceededError as exc:
                self._emit(ProviderCallTelemetry(
                    run_id, provider, capability, operation, "budget_exceeded", 0, 0,
                    billable, 0.0, type(exc).__name__, str(exc), started_at, _now_iso(),
                ))
                raise
        if self._circuit_is_open(provider):
            exc = ProviderCircuitOpenError(f"circuit open for provider {provider}")
            self._emit(ProviderCallTelemetry(
                run_id, provider, capability, operation, "circuit_open", 0, 0,
                billable, 0.0, type(exc).__name__, str(exc), started_at, _now_iso(),
            ))
            raise exc

        limit = max(1, int(concurrency_limit))
        call_timeout = self.timeout_sec if timeout_sec is None else max(0.01, float(timeout_sec))
        semaphore = self._bulkheads.setdefault((provider, limit), asyncio.Semaphore(limit))
        await semaphore.acquire()
        try:
            return await self._execute_acquired(
                run_id=run_id,
                provider=provider,
                capability=capability,
                operation=operation,
                call=call,
                validator=validator,
                billable=billable,
                units=units,
                concurrency_limit=limit,
                rate_limit_per_minute=max(1, int(rate_limit_per_minute)),
                max_response_bytes=max(1, int(max_response_bytes)),
                max_result_items=max(1, int(max_result_items)),
                max_requests_per_attempt=max(1, int(max_requests_per_attempt)),
                max_pages_per_attempt=max(1, int(max_pages_per_attempt)),
                timeout_sec=call_timeout,
                started_at=started_at,
                started=started,
            )
        finally:
            semaphore.release()

    async def _execute_acquired(
        self,
        *,
        run_id: str | None,
        provider: str,
        capability: str,
        operation: str,
        call: Callable[[], Awaitable[T]],
        validator: Callable[[T], bool] | None,
        billable: bool,
        units: float,
        concurrency_limit: int,
        rate_limit_per_minute: int,
        max_response_bytes: int,
        max_result_items: int,
        max_requests_per_attempt: int,
        max_pages_per_attempt: int,
        timeout_sec: float,
        started_at: str,
        started: float,
    ) -> T:
        tries = 0
        provider_attempts = 0
        upstream_requests = 0
        pages = 0
        final_status = "error"
        final_error: ProviderError | None = None
        while tries <= self.retry_count:
            tries += 1
            permit_id: str | None = None
            try:
                permit_id = await self._wait_for_permit(
                    provider, rate_limit_per_minute, concurrency_limit, timeout_sec + 5.0
                )
                provider_attempts += 1
                with provider_budget(
                    max_requests=max_requests_per_attempt,
                    max_pages=max_pages_per_attempt,
                ) as attempt_budget:
                    try:
                        result = await asyncio.wait_for(call(), timeout=timeout_sec)
                    finally:
                        upstream_requests += attempt_budget.requests
                        pages += attempt_budget.pages
                oversized_items = _oversized_collection(result, max_result_items)
                if oversized_items is not None:
                    raise ProviderResponseLimitError(
                        f"provider returned {oversized_items} items; limit is {max_result_items}"
                    )
                serializable = asdict(result) if is_dataclass(result) else result
                encoded = json.dumps(
                    serializable, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8")
                if len(encoded) > max_response_bytes:
                    raise ProviderResponseLimitError(
                        f"normalized provider result is {len(encoded)} bytes; limit is {max_response_bytes}"
                    )
                if validator is not None and not validator(result):
                    raise ProviderMalformedError("provider result failed contract validation")
                status = "empty" if result is None or result == [] else "success"
                self._mark_success(provider)
                self._emit(ProviderCallTelemetry(
                    run_id, provider, capability, operation, status, provider_attempts,
                    int((time.perf_counter() - started) * 1000), billable,
                    units * provider_attempts if billable else 0.0, None, None, started_at, _now_iso(),
                    upstream_requests, pages,
                ))
                return result
            except Exception as exc:
                final_status, final_error, retryable = self._classify(exc)
                if not retryable or tries > self.retry_count:
                    break
                await asyncio.sleep(min(0.1 * (2 ** (tries - 1)), 1.0))
            finally:
                if permit_id and self.permit_release:
                    try:
                        self.permit_release(permit_id)
                    except Exception:
                        logger.exception("provider permit release failed")

        assert final_error is not None
        if not isinstance(final_error, ProviderInputError):
            self._mark_failure(provider, final_error)
        self._emit(ProviderCallTelemetry(
            run_id, provider, capability, operation, final_status, provider_attempts,
            int((time.perf_counter() - started) * 1000), billable,
            units * provider_attempts if billable else 0.0, type(final_error).__name__,
            str(final_error)[:500], started_at, _now_iso(),
            upstream_requests, pages,
        ))
        raise final_error

    async def _wait_for_permit(
        self,
        provider: str,
        rate_limit_per_minute: int,
        concurrency_limit: int,
        lease_seconds: float,
    ) -> str | None:
        if self.permit_acquire is None:
            return None
        request_id = uuid.uuid4().hex
        deadline = time.monotonic() + self.capacity_wait_sec
        acquired = False
        try:
            while True:
                try:
                    permit_id = self.permit_acquire(
                        provider,
                        self.owner,
                        rate_limit_per_minute,
                        concurrency_limit,
                        lease_seconds,
                        request_id,
                    )
                    acquired = True
                    return permit_id
                except ProviderCapacityError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProviderCapacityError(
                            f"local capacity wait exceeded for {provider} "
                            f"({self.capacity_wait_sec:.2f}s)"
                        ) from exc
                    await asyncio.sleep(min(exc.retry_after, remaining, 0.25))
        finally:
            if not acquired and self.permit_cancel:
                try:
                    self.permit_cancel(request_id)
                except Exception:
                    logger.exception("provider waiter cancellation failed")
