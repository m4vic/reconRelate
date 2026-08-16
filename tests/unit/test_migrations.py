import sqlite3
from pathlib import Path

import pytest

from reconrelate.db.db import MigrationError, apply_migrations, get_connection, init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_database_applies_observation_ledger_migration() -> None:
    conn = get_connection(":memory:")
    init_db(conn)
    assert {"observations", "claims", "claim_evidence", "source_lineage"} <= _tables(conn)
    rows = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
    assert [tuple(row) for row in rows] == [
        (1, "observation_ledger"),
        (2, "cache_observations"),
        (3, "provider_calls"),
        (4, "run_tasks"),
        (5, "provider_state"),
        (6, "provider_permits"),
        (7, "provider_waiters"),
        (8, "provider_request_usage"),
        (9, "run_execution_budgets"),
        (10, "pivot_planner_decisions"),
        (11, "run_comparison_policy"),
        (12, "run_model_budgets"),
        (13, "model_calls"),
        (14, "model_budget_reservations"),
        (15, "model_request_idempotency"),
        (16, "model_egress_policy"),
        (17, "model_output_disposition"),
        (18, "run_model_routing"),
        (19, "cloud_model_cost_budget"),
        (20, "provider_data_policy"),
    ]
    columns = {row[1] for row in conn.execute("PRAGMA table_info(domain_cache)")}
    assert "observations_json" in columns
    pivot_columns = {row[1] for row in conn.execute("PRAGMA table_info(pivot_decisions)")}
    assert {"evidence_gap", "utility", "estimated_logical_calls", "policy_version"} <= pivot_columns
    run_columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert {"run_mode", "llm_model", "llm_policy_version", "cache_mode"} <= run_columns
    assert {"fast_model", "model_routing_policy"} <= run_columns
    assert {"max_cloud_cost_microusd", "model_price_catalog_version"} <= run_columns
    assert {"cloud_approved", "max_model_calls", "max_model_input_tokens",
            "max_model_output_tokens", "max_cloud_tokens"} <= run_columns
    assert "model_calls" in _tables(conn)
    assert "model_budget_reservations" in _tables(conn)
    reservation_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(model_budget_reservations)")
    }
    assert {"request_key", "cloud_cost_microusd"} <= reservation_columns
    model_call_columns = {row[1] for row in conn.execute("PRAGMA table_info(model_calls)")}
    assert {"request_key", "result_json", "egress_policy_version",
            "output_disposition"} <= model_call_columns
    assert {"reserved_cloud_cost_microusd", "price_catalog_version"} <= model_call_columns
    observation_columns = {row[1] for row in conn.execute("PRAGMA table_info(observations)")}
    assert {"data_policy_version", "cache_allowed", "export_scope", "raw_retention"} <= observation_columns


def test_legacy_schema_upgrades_without_losing_rows() -> None:
    conn = get_connection(":memory:")
    schema = Path(__file__).parents[2] / "src" / "reconrelate" / "db" / "schema.sql"
    conn.executescript(schema.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO runs (id, root_domain, status, max_depth, pivot_top_k, created_at) "
        "VALUES ('legacy', 'example.com', 'completed', 1, 3, '2026-01-01')"
    )
    conn.commit()
    init_db(conn)
    assert conn.execute("SELECT root_domain FROM runs WHERE id='legacy'").fetchone()[0] == "example.com"
    assert "observations" in _tables(conn)


def test_migration_replay_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "0001_create_widget.sql").write_text(
        "CREATE TABLE widgets (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )
    conn = get_connection(":memory:")
    apply_migrations(conn, tmp_path)
    apply_migrations(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1


def test_changed_applied_migration_is_rejected(tmp_path: Path) -> None:
    migration = tmp_path / "0001_create_widget.sql"
    migration.write_text("CREATE TABLE widgets (id TEXT);\n", encoding="utf-8")
    conn = get_connection(":memory:")
    apply_migrations(conn, tmp_path)
    migration.write_text("CREATE TABLE widgets (id INTEGER);\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed on disk"):
        apply_migrations(conn, tmp_path)


def test_failed_migration_rolls_back_all_its_statements(tmp_path: Path) -> None:
    (tmp_path / "0001_broken.sql").write_text(
        "CREATE TABLE partial_write (id TEXT);\nTHIS IS NOT SQL;\n", encoding="utf-8"
    )
    conn = get_connection(":memory:")
    with pytest.raises(MigrationError, match="failed migration"):
        apply_migrations(conn, tmp_path)
    assert "partial_write" not in _tables(conn)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
