from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from reconrelate.core.evidence import Claim, EvidencePolarity, Observation
from reconrelate.core.graph_projection import project_claim_graph
from reconrelate.core.source_independence import source_family, summarize_source_families
from reconrelate.core.provider_execution import ProviderCallTelemetry
from reconrelate.core.errors import ProviderCapacityError
from reconrelate.core.errors import ModelBudgetExceededError, ModelDuplicateReservationError
from reconrelate.core.types import RunSummary
from reconrelate.llm_orchestration.model_telemetry import ModelCallTelemetry
from reconrelate.llm_orchestration.model_budget import ModelReservation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._batch_depth = 0

    def _commit(self) -> None:
        if self._batch_depth == 0:
            self.conn.commit()

    @contextmanager
    def batch(self):
        """Defer commits until the outermost batch exits (one SQLite transaction per domain hop)."""
        self._batch_depth += 1
        try:
            yield
        except Exception:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self.conn.rollback()
            raise
        else:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                self.conn.commit()

    def create_run(
        self, root_domain: str, max_depth: int, pivot_top_k: int, *,
        provider_profile: str = "free", max_provider_calls: int = 500,
        max_billable_units: float = 0.0,
        run_mode: str = "legacy", llm_model: str = "legacy",
        llm_policy_version: str = "legacy", cache_mode: str = "reuse",
        fast_model: str = "", model_routing_policy: str = "single-model-v1",
        cloud_approved: bool = False, max_model_calls: int = 50,
        max_model_input_tokens: int = 200_000, max_model_output_tokens: int = 25_600,
        max_cloud_tokens: int = 0,
        max_cloud_cost_microusd: int = 0, model_price_catalog_version: str = "legacy",
    ) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO runs (
              id, root_domain, status, max_depth, pivot_top_k, created_at,
              provider_profile, max_provider_calls, max_billable_units,
              run_mode, llm_model, llm_policy_version, cache_mode,
              fast_model, model_routing_policy,
              cloud_approved, max_model_calls, max_model_input_tokens,
              max_model_output_tokens, max_cloud_tokens, max_cloud_cost_microusd,
              model_price_catalog_version
            )
            VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, root_domain, max_depth, pivot_top_k, _now_iso(), provider_profile,
             max(0, int(max_provider_calls)), max(0.0, float(max_billable_units)),
             run_mode, llm_model, llm_policy_version, cache_mode, fast_model,
             model_routing_policy,
             int(bool(cloud_approved)), max(0, int(max_model_calls)),
             max(0, int(max_model_input_tokens)), max(0, int(max_model_output_tokens)),
             max(0, int(max_cloud_tokens)), max(0, int(max_cloud_cost_microusd)),
             model_price_catalog_version),
        )
        self._commit()
        return run_id

    def mark_run_completed(self, run_id: str) -> None:
        provider_degraded = self.conn.execute(
            """
            SELECT 1 FROM provider_calls
            WHERE run_id = ? AND status IN (
              'timeout', 'rate_limited', 'auth_error', 'malformed', 'error', 'circuit_open'
            ) LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        task_rows = self.get_run_task_summary(run_id)
        if task_rows["pending"] or task_rows["in_progress"]:
            status = "partial"
        elif task_rows["failed"] or provider_degraded:
            status = "completed_degraded"
        else:
            status = "completed"
        self.conn.execute(
            "UPDATE runs SET status = ?, completed_at = ? WHERE id = ?",
            (status, _now_iso(), run_id),
        )
        self._commit()

    def mark_run_failed(self, run_id: str, message: str) -> None:
        self.conn.execute(
            "UPDATE runs SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
            (message, _now_iso(), run_id),
        )
        self._commit()

    def mark_run_interrupted(self, run_id: str) -> None:
        """Mark a run as interrupted (Ctrl+C). It can be resumed later."""
        self.conn.execute(
            "UPDATE runs SET status = 'interrupted', completed_at = ? WHERE id = ?",
            (_now_iso(), run_id),
        )
        self._commit()

    def record_provider_call(self, telemetry: ProviderCallTelemetry) -> str:
        call_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO provider_calls (
              id, run_id, provider, capability, operation, status, attempts, latency_ms,
              billable, units, error_class, error_message, started_at, completed_at,
              upstream_requests, pages
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id, telemetry.run_id, telemetry.provider, telemetry.capability,
                telemetry.operation, telemetry.status, telemetry.attempts,
                telemetry.latency_ms, int(telemetry.billable), telemetry.units,
                telemetry.error_class, telemetry.error_message, telemetry.started_at,
                telemetry.completed_at,
                telemetry.upstream_requests, telemetry.pages,
            ),
        )
        self._commit()
        return call_id

    def enqueue_run_task(
        self,
        run_id: str,
        *,
        task_type: str,
        idempotency_key: str,
        payload: dict,
        priority: int = 0,
        max_attempts: int = 3,
    ) -> str:
        task_id = str(uuid.uuid4())
        now = _now_iso()
        inserted = self.conn.execute(
            """
            INSERT OR IGNORE INTO run_tasks (
              id, run_id, task_type, idempotency_key, payload_json, status,
              priority, attempts, max_attempts, available_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?, ?)
            """,
            (
                task_id, run_id, task_type, idempotency_key,
                json.dumps(payload, sort_keys=True), priority, max(1, max_attempts),
                now, now, now,
            ),
        )
        self._commit()
        if inserted.rowcount == 0:
            existing = self.conn.execute(
                "SELECT id FROM run_tasks WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing is None:
                raise RuntimeError("run task enqueue conflict could not be resolved")
            return str(existing["id"])
        return task_id

    def claim_run_task(self, run_id: str, *, lease_seconds: int = 120) -> dict | None:
        now = _now_iso()
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=max(1, lease_seconds))).isoformat()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                UPDATE run_tasks
                SET status = 'failed', lease_until = NULL,
                    last_error = COALESCE(last_error, 'lease expired after maximum attempts'),
                    updated_at = ?
                WHERE run_id = ? AND status = 'in_progress' AND lease_until < ?
                  AND attempts >= max_attempts
                """,
                (now, run_id, now),
            )
            self.conn.execute(
                """
                UPDATE run_tasks
                SET status = 'pending', lease_until = NULL, updated_at = ?
                WHERE run_id = ? AND status = 'in_progress' AND lease_until < ?
                  AND attempts < max_attempts
                """,
                (now, run_id, now),
            )
            row = self.conn.execute(
                """
                SELECT * FROM run_tasks
                WHERE run_id = ? AND status = 'pending' AND available_at <= ?
                  AND attempts < max_attempts
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (run_id, now),
            ).fetchone()
            if row is None:
                self.conn.commit()
                return None
            updated = self.conn.execute(
                """
                UPDATE run_tasks
                SET status = 'in_progress', attempts = attempts + 1,
                    lease_until = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (lease_until, now, row["id"]),
            )
            if updated.rowcount != 1:
                self.conn.rollback()
                return None
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        task = dict(row)
        task["status"] = "in_progress"
        task["attempts"] = int(task["attempts"]) + 1
        task["lease_until"] = lease_until
        try:
            task["payload"] = json.loads(task.pop("payload_json"))
        except (TypeError, json.JSONDecodeError) as exc:
            self.fail_run_task(str(task["id"]), f"invalid task payload: {exc}", retry=False)
            return None
        return task

    def complete_run_task(self, task_id: str) -> None:
        self.conn.execute(
            """
            UPDATE run_tasks SET status = 'succeeded', lease_until = NULL,
              last_error = NULL, updated_at = ?
            WHERE id = ? AND status = 'in_progress'
            """,
            (_now_iso(), task_id),
        )
        self._commit()

    def fail_run_task(
        self, task_id: str, error: str, *, retry: bool = True, delay_seconds: int = 0
    ) -> None:
        row = self.conn.execute(
            "SELECT attempts, max_attempts FROM run_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return
        should_retry = retry and int(row["attempts"]) < int(row["max_attempts"])
        available_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(0, delay_seconds))
        ).isoformat()
        self.conn.execute(
            """
            UPDATE run_tasks SET status = ?, available_at = ?, lease_until = NULL,
              last_error = ?, updated_at = ? WHERE id = ?
            """,
            (
                "pending" if should_retry else "failed", available_at,
                error[:500], _now_iso(), task_id,
            ),
        )
        self._commit()

    def requeue_in_progress_tasks(self, run_id: str) -> int:
        now = _now_iso()
        self.conn.execute(
            """
            UPDATE run_tasks SET status = 'failed', lease_until = NULL,
              last_error = COALESCE(last_error, 'run stopped after maximum attempts'),
              updated_at = ?
            WHERE run_id = ? AND status = 'in_progress' AND attempts >= max_attempts
            """,
            (now, run_id),
        )
        updated = self.conn.execute(
            """
            UPDATE run_tasks SET status = 'pending', lease_until = NULL, updated_at = ?
            WHERE run_id = ? AND status = 'in_progress' AND attempts < max_attempts
            """,
            (now, run_id),
        )
        self._commit()
        return int(updated.rowcount)

    def count_runnable_tasks(self, run_id: str) -> int:
        return int(self.conn.execute(
            """
            SELECT COUNT(*) FROM run_tasks
            WHERE run_id = ? AND status = 'pending' AND attempts < max_attempts
            """,
            (run_id,),
        ).fetchone()[0])

    def get_run_task_summary(self, run_id: str) -> dict[str, int]:
        summary = {"pending": 0, "in_progress": 0, "succeeded": 0, "failed": 0}
        for row in self.conn.execute(
            "SELECT status, COUNT(*) AS count FROM run_tasks WHERE run_id = ? GROUP BY status",
            (run_id,),
        ):
            summary[str(row["status"])] = int(row["count"])
        return summary

    def get_provider_usage(self, run_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT provider, capability, status, billable,
                       COUNT(*) AS calls, SUM(attempts) AS attempts,
                       SUM(upstream_requests) AS upstream_requests, SUM(pages) AS pages,
                       SUM(units) AS units, SUM(latency_ms) AS latency_ms
                FROM provider_calls
                WHERE run_id = ?
                GROUP BY provider, capability, status, billable
                ORDER BY provider, capability, status
                """,
                (run_id,),
            )
        ]

    def record_model_call(self, call: ModelCallTelemetry) -> None:
        self.conn.execute(
            """
            INSERT INTO model_calls (
              id, run_id, domain, model, task, policy_version, cloud, status,
              reserved_input_tokens, reserved_output_tokens, reserved_cloud_tokens,
              actual_input_tokens, actual_output_tokens, actual_total_tokens,
              provider_reported_cost_usd, latency_ms, error_class, error_message,
              started_at, completed_at, request_key, result_json, egress_policy_version,
              output_disposition, reserved_cloud_cost_microusd, price_catalog_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()), call.run_id, call.domain, call.model, call.task,
                call.policy_version, int(call.cloud), call.status,
                call.reserved_input_tokens, call.reserved_output_tokens,
                call.reserved_cloud_tokens, call.actual_input_tokens,
                call.actual_output_tokens, call.actual_total_tokens,
                call.provider_reported_cost_usd, call.latency_ms, call.error_class,
                (call.error_message or "")[:500] or None, call.started_at, call.completed_at,
                call.request_key or None, call.result_json, call.egress_policy_version,
                call.output_disposition,
                call.reserved_cloud_cost_microusd, call.price_catalog_version,
            ),
        )
        self._commit()

    def reserve_model_budget(
        self, run_id: str, model: str, domain: str, reservation: ModelReservation,
        request_key: str | None = None,
    ) -> ModelReservation:
        """Atomically reserve lifetime run quota across processes; reservations are never refunded."""
        owns_transaction = not self.conn.in_transaction
        if owns_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            run = self.conn.execute(
                """
                SELECT max_model_calls, max_model_input_tokens,
                       max_model_output_tokens, max_cloud_tokens, max_cloud_cost_microusd
                FROM runs WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"run not found: {run_id}")
            if request_key:
                duplicate = self.conn.execute(
                    "SELECT 1 FROM model_budget_reservations WHERE request_key = ?",
                    (request_key,),
                ).fetchone()
                if duplicate is not None:
                    raise ModelDuplicateReservationError(
                        "identical model request already has a durable reservation"
                    )
            used = self.conn.execute(
                """
                SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(cloud_tokens), 0) AS cloud_tokens,
                       COALESCE(SUM(cloud_cost_microusd), 0) AS cloud_cost_microusd
                FROM model_budget_reservations WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            checks = (
                (int(used["calls"]) + 1, int(run["max_model_calls"]), "model call"),
                (int(used["input_tokens"]) + reservation.input_tokens,
                 int(run["max_model_input_tokens"]), "model input-token"),
                (int(used["output_tokens"]) + reservation.output_tokens,
                 int(run["max_model_output_tokens"]), "model output-token"),
                (int(used["cloud_tokens"]) + reservation.cloud_tokens,
                 int(run["max_cloud_tokens"]), "cloud token"),
                (int(used["cloud_cost_microusd"]) + reservation.cloud_cost_microusd,
                 int(run["max_cloud_cost_microusd"]), "cloud cost-microdollar"),
            )
            for proposed, limit, label in checks:
                if proposed > limit:
                    raise ModelBudgetExceededError(f"{label} ceiling {limit} would be exceeded")
            self.conn.execute(
                """
                INSERT INTO model_budget_reservations
                  (id, run_id, model, domain, input_tokens, output_tokens, cloud_tokens,
                   cloud_cost_microusd,
                   created_at, request_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), run_id, model, domain, reservation.input_tokens,
                    reservation.output_tokens, reservation.cloud_tokens,
                    reservation.cloud_cost_microusd, _now_iso(),
                    request_key,
                ),
            )
            if owns_transaction:
                self.conn.commit()
            return reservation
        except Exception:
            if owns_transaction:
                self.conn.rollback()
            raise

    def get_cached_model_result(self, request_key: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT result_json FROM model_calls
            WHERE request_key = ? AND status = 'success' AND result_json IS NOT NULL
            ORDER BY completed_at DESC LIMIT 1
            """,
            (request_key,),
        ).fetchone()
        return str(row["result_json"]) if row is not None else None

    def get_model_budget_usage(self, run_id: str) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cloud_tokens), 0) AS cloud_tokens,
                   COALESCE(SUM(cloud_cost_microusd), 0) AS cloud_cost_microusd
            FROM model_budget_reservations WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        return {key: int(row[key]) for key in (
            "calls", "input_tokens", "output_tokens", "cloud_tokens", "cloud_cost_microusd"
        )}

    def get_model_usage(self, run_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT model, task, cloud, status, egress_policy_version, output_disposition,
                       COUNT(*) AS calls,
                       SUM(reserved_input_tokens) AS reserved_input_tokens,
                       SUM(reserved_output_tokens) AS reserved_output_tokens,
                       SUM(reserved_cloud_tokens) AS reserved_cloud_tokens,
                       SUM(reserved_cloud_cost_microusd) AS reserved_cloud_cost_microusd,
                       SUM(actual_input_tokens) AS actual_input_tokens,
                       SUM(actual_output_tokens) AS actual_output_tokens,
                       SUM(actual_total_tokens) AS actual_total_tokens,
                       SUM(provider_reported_cost_usd) AS provider_reported_cost_usd,
                       SUM(latency_ms) AS latency_ms
                FROM model_calls WHERE run_id = ?
                GROUP BY model, task, cloud, status, egress_policy_version, output_disposition
                ORDER BY model, task, status
                """,
                (run_id,),
            )
        ]

    def provider_circuit_is_open(self, provider: str) -> bool:
        row = self.conn.execute(
            "SELECT circuit_open_until FROM provider_state WHERE provider = ?", (provider,)
        ).fetchone()
        if row is None or not row["circuit_open_until"]:
            return False
        try:
            open_until = datetime.fromisoformat(str(row["circuit_open_until"]))
        except ValueError:
            return False
        if open_until.tzinfo is None:
            open_until = open_until.replace(tzinfo=timezone.utc)
        if open_until > datetime.now(timezone.utc):
            return True
        self.conn.execute(
            """
            UPDATE provider_state SET circuit_open_until = NULL,
              consecutive_failures = 0, updated_at = ? WHERE provider = ?
            """,
            (_now_iso(), provider),
        )
        self._commit()
        return False

    def record_provider_success(self, provider: str) -> None:
        now = _now_iso()
        self.conn.execute(
            """
            INSERT INTO provider_state (provider, consecutive_failures, updated_at)
            VALUES (?, 0, ?)
            ON CONFLICT(provider) DO UPDATE SET consecutive_failures = 0,
              circuit_open_until = NULL, last_error_class = NULL,
              last_error_message = NULL, updated_at = excluded.updated_at
            """,
            (provider, now),
        )
        self._commit()

    def record_provider_failure(
        self,
        provider: str,
        error: Exception,
        *,
        threshold: int,
        cooldown_seconds: float,
    ) -> None:
        now = _now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                """
                INSERT INTO provider_state (
                  provider, consecutive_failures, last_error_class,
                  last_error_message, updated_at
                ) VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                  consecutive_failures = provider_state.consecutive_failures + 1,
                  last_error_class = excluded.last_error_class,
                  last_error_message = excluded.last_error_message,
                  updated_at = excluded.updated_at
                """,
                (provider, type(error).__name__, str(error)[:500], now),
            )
            failures = int(self.conn.execute(
                "SELECT consecutive_failures FROM provider_state WHERE provider = ?", (provider,)
            ).fetchone()[0])
            if failures >= max(1, threshold):
                open_until = (
                    datetime.now(timezone.utc) + timedelta(seconds=max(0.0, cooldown_seconds))
                ).isoformat()
                self.conn.execute(
                    "UPDATE provider_state SET circuit_open_until = ? WHERE provider = ?",
                    (open_until, provider),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def get_provider_states(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM provider_state ORDER BY provider")]

    def acquire_provider_permit(
        self,
        provider: str,
        *,
        owner: str,
        rate_limit_per_minute: int,
        concurrency_limit: int,
        lease_seconds: float,
        request_id: str | None = None,
        waiter_ttl_seconds: float = 60.0,
    ) -> str:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        window = now_dt.replace(second=0, microsecond=0).isoformat()
        expires = (now_dt + timedelta(seconds=max(1.0, lease_seconds))).isoformat()
        old_window = (now_dt - timedelta(hours=2)).replace(second=0, microsecond=0).isoformat()
        permit_id = str(uuid.uuid4())
        waiter_expires = (
            now_dt + timedelta(seconds=max(1.0, waiter_ttl_seconds))
        ).isoformat()
        denial: ProviderCapacityError | None = None
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.execute(
                "DELETE FROM provider_concurrency_leases WHERE expires_at <= ?", (now,)
            )
            self.conn.execute("DELETE FROM provider_waiters WHERE expires_at <= ?", (now,))
            self.conn.execute(
                "DELETE FROM provider_rate_windows WHERE window_started_at < ?", (old_window,)
            )
            if request_id:
                self.conn.execute(
                    """
                    INSERT INTO provider_waiters (id, provider, owner, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET expires_at = excluded.expires_at
                    """,
                    (request_id, provider, owner, now, waiter_expires),
                )
                head = self.conn.execute(
                    """
                    SELECT id FROM provider_waiters
                    WHERE provider = ? ORDER BY created_at, id LIMIT 1
                    """,
                    (provider,),
                ).fetchone()
                if head is not None and str(head[0]) != request_id:
                    denial = ProviderCapacityError(
                        f"waiting for earlier queued request for {provider}", retry_after=0.05
                    )
            active = int(self.conn.execute(
                """
                SELECT COUNT(*) FROM provider_concurrency_leases
                WHERE provider = ? AND expires_at > ?
                """,
                (provider, now),
            ).fetchone()[0])
            if denial is None and active >= max(1, concurrency_limit):
                next_expiry = self.conn.execute(
                    """
                    SELECT MIN(expires_at) FROM provider_concurrency_leases
                    WHERE provider = ? AND expires_at > ?
                    """,
                    (provider, now),
                ).fetchone()[0]
                retry_after = 0.05
                if next_expiry:
                    retry_after = max(
                        0.01,
                        (datetime.fromisoformat(str(next_expiry)) - now_dt).total_seconds(),
                    )
                denial = ProviderCapacityError(
                    f"local concurrency ceiling reached for {provider} ({concurrency_limit})",
                    retry_after=min(retry_after, 0.25),
                )
            count_row = self.conn.execute(
                """
                SELECT request_count FROM provider_rate_windows
                WHERE provider = ? AND window_started_at = ?
                """,
                (provider, window),
            ).fetchone()
            used = int(count_row[0]) if count_row else 0
            if denial is None and used >= max(1, rate_limit_per_minute):
                next_window = now_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
                denial = ProviderCapacityError(
                    f"local rate ceiling reached for {provider} ({rate_limit_per_minute}/minute)",
                    retry_after=max(0.01, (next_window - now_dt).total_seconds()),
                )
            if denial is None:
                self.conn.execute(
                    """
                    INSERT INTO provider_rate_windows (provider, window_started_at, request_count, updated_at)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(provider, window_started_at) DO UPDATE SET
                      request_count = provider_rate_windows.request_count + 1,
                      updated_at = excluded.updated_at
                    """,
                    (provider, window, now),
                )
                self.conn.execute(
                    """
                    INSERT INTO provider_concurrency_leases (id, provider, owner, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (permit_id, provider, owner, expires, now),
                )
                if request_id:
                    self.conn.execute("DELETE FROM provider_waiters WHERE id = ?", (request_id,))
            self.conn.commit()
            if denial is not None:
                raise denial
            return permit_id
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

    def release_provider_permit(self, permit_id: str) -> None:
        self.conn.execute("DELETE FROM provider_concurrency_leases WHERE id = ?", (permit_id,))
        self._commit()

    def cancel_provider_waiter(self, request_id: str) -> None:
        self.conn.execute("DELETE FROM provider_waiters WHERE id = ?", (request_id,))
        self._commit()

    def add_observation(self, run_id: str, observation: Observation) -> str:
        """Persist an immutable source observation, returning the existing ID on replay."""
        existing = self.conn.execute(
            "SELECT id FROM observations WHERE run_id = ? AND dedup_key = ?",
            (run_id, observation.dedup_key),
        ).fetchone()
        if existing:
            return str(existing["id"])

        observation_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO observations (
              id, run_id, dedup_key, subject_type, subject_value_norm, predicate,
              object_type, object_value_norm, source, source_record_id, observed_at,
              valid_from, valid_to, confidence, normalized_json, raw_hash, created_at
              , data_policy_version, cache_allowed, export_scope, raw_retention
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_id, run_id, observation.dedup_key,
                observation.subject_type, observation.subject_value_norm,
                observation.predicate, observation.object_type,
                observation.object_value_norm, observation.source,
                observation.source_record_id, observation.observed_at,
                observation.valid_from, observation.valid_to, observation.confidence,
                observation.normalized_json(), observation.raw_hash, _now_iso(),
                observation.data_policy_version, int(observation.cache_allowed),
                observation.export_scope, observation.raw_retention,
            ),
        )
        self._commit()
        return observation_id

    def get_observations(
        self,
        run_id: str,
        *,
        subject_type: str | None = None,
        subject_value_norm: str | None = None,
    ) -> list[dict]:
        clauses = ["run_id = ?"]
        params: list[str] = [run_id]
        if subject_type is not None:
            clauses.append("subject_type = ?")
            params.append(subject_type)
        if subject_value_norm is not None:
            clauses.append("subject_value_norm = ?")
            params.append(subject_value_norm)
        rows = self.conn.execute(
            f"SELECT * FROM observations WHERE {' AND '.join(clauses)} "
            "ORDER BY observed_at ASC, created_at ASC",
            params,
        ).fetchall()
        observations = []
        for row in rows:
            item = dict(row)
            try:
                item["normalized"] = json.loads(item.pop("normalized_json"))
            except (TypeError, json.JSONDecodeError):
                item["normalized"] = {}
                item.pop("normalized_json", None)
            observations.append(item)
        return observations

    def find_observations_for_object(
        self,
        run_id: str,
        *,
        subject_value_norm: str,
        object_value_norm: str,
    ) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM observations
            WHERE run_id = ? AND subject_value_norm = ?
              AND lower(COALESCE(object_value_norm, '')) = lower(?)
            ORDER BY confidence DESC, observed_at ASC
            """,
            (run_id, subject_value_norm, object_value_norm),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_claim(self, run_id: str, claim: Claim) -> str:
        """Persist a policy-derived claim, returning the existing ID on replay."""
        existing = self.conn.execute(
            "SELECT id FROM claims WHERE run_id = ? AND claim_key = ?",
            (run_id, claim.claim_key),
        ).fetchone()
        if existing:
            return str(existing["id"])
        claim_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO claims (
              id, run_id, claim_key, claim_type, subject_type, subject_value_norm,
              object_type, object_value_norm, status, confidence_class, score,
              policy_version, valid_from, valid_to, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id, run_id, claim.claim_key, claim.claim_type,
                claim.subject_type, claim.subject_value_norm, claim.object_type,
                claim.object_value_norm, claim.status, claim.confidence_class,
                claim.score, claim.policy_version, claim.valid_from, claim.valid_to,
                _now_iso(),
            ),
        )
        self._commit()
        return claim_id

    def link_claim_evidence(
        self,
        claim_id: str,
        observation_id: str,
        polarity: EvidencePolarity,
        weight: float,
        reason: str,
    ) -> None:
        if polarity not in {"supports", "contradicts"}:
            raise ValueError("invalid evidence polarity")
        if not 0.0 <= weight <= 1.0:
            raise ValueError("evidence weight must be between 0 and 1")
        if not reason.strip():
            raise ValueError("evidence reason must be non-empty")
        self.conn.execute(
            """
            INSERT OR IGNORE INTO claim_evidence
              (claim_id, observation_id, polarity, weight, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (claim_id, observation_id, polarity, weight, reason, _now_iso()),
        )
        self._commit()

    def get_claims_with_evidence(self, run_id: str) -> list[dict]:
        claims = [
            dict(row)
            for row in self.conn.execute(
                "SELECT * FROM claims WHERE run_id = ? ORDER BY created_at ASC", (run_id,)
            )
        ]
        for claim in claims:
            claim["evidence"] = [
                dict(row)
                for row in self.conn.execute(
                    """
                    SELECT ce.*, o.source, o.predicate, o.subject_type,
                           o.subject_value_norm, o.object_type, o.object_value_norm,
                           o.observed_at, o.data_policy_version, o.export_scope,
                           o.raw_retention
                    FROM claim_evidence ce
                    JOIN observations o ON o.id = ce.observation_id
                    WHERE ce.claim_id = ?
                    ORDER BY ce.polarity ASC, ce.created_at ASC
                    """,
                    (claim["id"],),
                )
            ]
            for evidence in claim["evidence"]:
                evidence["source_family"] = source_family(str(evidence.get("source", "")))
            claim["evidence_independence"] = summarize_source_families(
                str(evidence.get("source", "")) for evidence in claim["evidence"]
            )
        return claims

    def get_latest_resumable_run(self, root_domain: str) -> dict | None:
        """Find the most recent interrupted or crash-abandoned running run."""
        row = self.conn.execute(
            """
            SELECT * FROM runs
            WHERE root_domain = ? AND status IN ('interrupted', 'running', 'partial')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (root_domain,),
        ).fetchone()
        return dict(row) if row else None

    def get_resumable_queue(self, run_id: str) -> list[tuple[str, int]]:
        """Return (domain, depth) pairs for domains discovered but not yet processed.

        These are domain nodes that exist in the nodes table but have no entry
        in processed_domains — i.e. they were queued but the run was interrupted
        before the orchestrator could process them.
        """
        rows = self.conn.execute(
            """
            SELECT n.value_norm AS domain,
                   COALESCE(json_extract(n.metadata_json, '$.first_seen_depth'), 0) AS depth
            FROM nodes n
            WHERE n.run_id = ? AND n.node_type = 'domain'
              AND NOT EXISTS (
                  SELECT 1 FROM processed_domains pd
                  WHERE pd.run_id = n.run_id AND pd.domain_node_id = n.id
              )
            ORDER BY depth ASC, n.created_at ASC
            """,
            (run_id,),
        ).fetchall()
        return [(str(row["domain"]), int(row["depth"])) for row in rows]

    def count_processed_domains(self, run_id: str) -> int:
        """Count how many domains have been fully processed in a run."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM processed_domains WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["c"])

    def get_run(self, run_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_or_create_node(
        self,
        run_id: str,
        node_type: str,
        value_norm: str,
        metadata: dict | None = None,
    ) -> str:
        row = self.conn.execute(
            """
            SELECT id FROM nodes
            WHERE run_id = ? AND node_type = ? AND value_norm = ?
            """,
            (run_id, node_type, value_norm),
        ).fetchone()
        if row:
            return str(row["id"])

        node_id = str(uuid.uuid4())
        value_hash = hashlib.sha256(value_norm.encode("utf-8")).hexdigest()
        self.conn.execute(
            """
            INSERT INTO nodes (id, run_id, node_type, value_norm, value_hash, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                run_id,
                node_type,
                value_norm,
                value_hash,
                json.dumps(metadata or {}, sort_keys=True),
                _now_iso(),
            ),
        )
        self._commit()
        return node_id

    def get_node(self, node_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return dict(row) if row else None

    def set_node_metadata(self, node_id: str, metadata: dict) -> None:
        """Merge `metadata` into an existing node's metadata_json (new keys win; no-op if node absent)."""
        row = self.conn.execute(
            "SELECT metadata_json FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return
        try:
            current = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        current.update(metadata)
        self.conn.execute(
            "UPDATE nodes SET metadata_json = ? WHERE id = ?",
            (json.dumps(current, sort_keys=True), node_id),
        )
        self._commit()

    def get_domain_node_id(self, run_id: str, domain: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT id FROM nodes
            WHERE run_id = ? AND node_type = 'domain' AND value_norm = ?
            """,
            (run_id, domain),
        ).fetchone()
        return str(row["id"]) if row else None

    def add_edge(
        self,
        run_id: str,
        from_node_id: str,
        to_node_id: str,
        relation_type: str,
        depth: int,
        source: str,
        confidence: float = 0.0,
    ) -> str:
        existing = self.conn.execute(
            """
            SELECT id FROM edges
            WHERE run_id = ? AND from_node_id = ? AND to_node_id = ?
              AND relation_type = ? AND depth = ? AND source = ?
            """,
            (run_id, from_node_id, to_node_id, relation_type, depth, source),
        ).fetchone()
        if existing:
            return str(existing["id"])

        edge_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO edges
            (id, run_id, from_node_id, to_node_id, relation_type, depth, source, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                run_id,
                from_node_id,
                to_node_id,
                relation_type,
                depth,
                source,
                confidence,
                _now_iso(),
            ),
        )
        self._commit()
        return edge_id

    def add_lineage(self, run_id: str, child_node_id: str, parent_node_id: str, depth: int) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO lineage (run_id, child_node_id, parent_node_id, depth)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, child_node_id, parent_node_id, depth),
        )
        self._commit()

    def add_pivot_decision(
        self,
        run_id: str,
        domain_node_id: str,
        identifier_value_norm: str,
        identifier_type: str,
        score: float,
        reason_short: str,
        evidence_gap: str = "asset_discovery",
        utility: float = 0.0,
        estimated_logical_calls: int = 1,
        policy_version: str = "legacy",
    ) -> None:
        existing = self.conn.execute(
            """
            SELECT 1 FROM pivot_decisions
            WHERE run_id = ? AND domain_node_id = ?
              AND identifier_value_norm = ? AND identifier_type = ?
            LIMIT 1
            """,
            (run_id, domain_node_id, identifier_value_norm, identifier_type),
        ).fetchone()
        if existing:
            return
        self.conn.execute(
            """
            INSERT INTO pivot_decisions
            (id, run_id, domain_node_id, identifier_value_norm, identifier_type, score, reason_short,
             evidence_gap, utility, estimated_logical_calls, policy_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                run_id,
                domain_node_id,
                identifier_value_norm,
                identifier_type,
                score,
                reason_short[:300],
                evidence_gap,
                utility,
                estimated_logical_calls,
                policy_version,
                _now_iso(),
            ),
        )
        self._commit()

    def is_domain_processed(self, run_id: str, domain_node_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM processed_domains
            WHERE run_id = ? AND domain_node_id = ?
            """,
            (run_id, domain_node_id),
        ).fetchone()
        return bool(row)

    def mark_domain_processed(self, run_id: str, domain_node_id: str, depth: int) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO processed_domains (run_id, domain_node_id, depth)
            VALUES (?, ?, ?)
            """,
            (run_id, domain_node_id, depth),
        )
        self._commit()

    def get_domain_cache(self, domain: str) -> dict | None:
        """Return cached child mappings and normalized observations, or None."""
        row = self.conn.execute(
            "SELECT last_scraped, children_json, observations_json FROM domain_cache WHERE domain = ?",
            (domain,),
        ).fetchone()
        if not row:
            return None
        try:
            children = json.loads(row["children_json"])
        except (TypeError, json.JSONDecodeError):
            children = []
        try:
            observations = json.loads(row["observations_json"])
        except (TypeError, json.JSONDecodeError):
            observations = []
        return {
            "last_scraped": str(row["last_scraped"]),
            "children": children if isinstance(children, list) else [],
            "observations": observations if isinstance(observations, list) else [],
        }

    def upsert_domain_cache(
        self, domain: str, children: list[dict], observations: list[dict] | None = None
    ) -> None:
        """Record (or refresh) what mapping `domain` produced, for cross-run reuse."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO domain_cache
              (domain, last_scraped, children_json, observations_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                domain,
                _now_iso(),
                json.dumps(children, sort_keys=True),
                json.dumps(observations or [], sort_keys=True),
            ),
        )
        self._commit()

    def count_nodes(self, run_id: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM nodes WHERE run_id = ?", (run_id,)).fetchone()
        return int(row["c"])

    def count_domain_nodes(self, run_id: str) -> int:
        """Domain nodes only — what a user means by "domains found".

        count_nodes() includes identifier and ip nodes too, so using it for a progress counter
        labelled "Domains Found" overstates the result (e.g. 1 domain + 1 identifier + 4 ips
        reported as 6). Mirrors the domains_count query in get_run_summary.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM nodes WHERE run_id = ? AND node_type = 'domain'",
            (run_id,),
        ).fetchone()
        return int(row["c"])

    def get_run_summary(self, run_id: str) -> RunSummary:
        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"run not found: {run_id}")

        domains_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM nodes WHERE run_id = ? AND node_type = 'domain'",
            (run_id,),
        ).fetchone()["c"]
        identifiers_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM nodes WHERE run_id = ? AND node_type = 'identifier'",
            (run_id,),
        ).fetchone()["c"]
        edges_count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE run_id = ?",
            (run_id,),
        ).fetchone()["c"]

        return RunSummary(
            run_id=run_id,
            status=str(run["status"]),
            root_domain=str(run["root_domain"]),
            domains_count=int(domains_count),
            identifiers_count=int(identifiers_count),
            edges_count=int(edges_count),
        )

    def get_run_graph(self, run_id: str) -> dict:
        run = self.get_run(run_id)
        if not run:
            raise ValueError(f"run not found: {run_id}")

        nodes = [dict(row) for row in self.conn.execute("SELECT * FROM nodes WHERE run_id = ?", (run_id,))]
        edges = [dict(row) for row in self.conn.execute("SELECT * FROM edges WHERE run_id = ?", (run_id,))]
        lineage = [dict(row) for row in self.conn.execute("SELECT * FROM lineage WHERE run_id = ?", (run_id,))]
        pivots = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT * FROM pivot_decisions
                WHERE run_id = ?
                ORDER BY score DESC, created_at ASC
                """,
                (run_id,),
            )
        ]
        observations = self.get_observations(run_id)
        claims = self.get_claims_with_evidence(run_id)
        provider_usage = self.get_provider_usage(run_id)
        model_usage = self.get_model_usage(run_id)
        model_budget_usage = self.get_model_budget_usage(run_id)
        task_summary = self.get_run_task_summary(run_id)
        return {
            "run": run,
            "nodes": nodes,
            "edges": edges,
            "lineage": lineage,
            "pivot_decisions": pivots,
            "observations": observations,
            "claims": claims,
            "claim_projection": project_claim_graph(claims),
            "provider_usage": provider_usage,
            "model_usage": model_usage,
            "model_budget_usage": model_budget_usage,
            "task_summary": task_summary,
        }
