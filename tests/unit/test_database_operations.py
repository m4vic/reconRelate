from pathlib import Path

import pytest

from reconrelate.core.evidence import Claim, Observation
from reconrelate.core.provider_execution import ProviderCallTelemetry
from reconrelate.db.db import get_connection, init_db
from reconrelate.db.operations import apply_retention, backup_database, check_database, restore_database
from reconrelate.db.repositories import GraphRepository


def _database(path: Path, root: str) -> tuple[str, str]:
    conn = get_connection(str(path))
    init_db(conn)
    repo = GraphRepository(conn)
    run_id = repo.create_run(root, 0, 1)
    observation_id = repo.add_observation(
        run_id,
        Observation.build(
            subject_type="domain", subject_value_norm=root, predicate="resolves_to",
            object_type="ip", object_value_norm="192.0.2.1", source="test-dns",
            idempotency_key=f"observation:{root}",
        ),
    )
    claim_id = repo.add_claim(
        run_id,
        Claim.build(
            claim_type="domain_has_ip", subject_type="domain", subject_value_norm=root,
            object_type="ip", object_value_norm="192.0.2.1", status="observed",
            confidence_class="verified", score=0.8, policy_version="relationship-v1",
        ),
    )
    repo.link_claim_evidence(claim_id, observation_id, "supports", 0.8, "test")
    repo.record_provider_call(ProviderCallTelemetry(
        run_id, "test-dns", "dns", "lookup", "success", 1, 2,
        False, 0.0, None, None, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
    ))
    conn.close()
    return run_id, root


def test_check_and_consistent_backup_roundtrip(tmp_path: Path) -> None:
    source = tmp_path / "active.sqlite"
    run_id, root = _database(source, "one.example")
    check = check_database(source)
    assert check.ok
    assert check.migration_versions == (
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
    )
    backup = backup_database(source, tmp_path / "snapshot.sqlite")
    assert check_database(backup).ok
    conn = get_connection(str(backup))
    assert conn.execute("SELECT root_domain FROM runs WHERE id = ?", (run_id,)).fetchone()[0] == root
    usage = conn.execute(
        "SELECT upstream_requests, pages FROM provider_calls WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert tuple(usage) == (0, 0)
    conn.close()
    with pytest.raises(FileExistsError):
        backup_database(source, backup)


def test_restore_verifies_candidate_and_preserves_safety_backup(tmp_path: Path) -> None:
    active = tmp_path / "active.sqlite"
    source = tmp_path / "source.sqlite"
    _database(active, "old.example")
    _database(source, "new.example")
    backup = backup_database(source, tmp_path / "source.backup.sqlite")
    result = restore_database(backup, active)
    assert result.safety_backup is not None
    assert Path(result.safety_backup).is_file()
    conn = get_connection(str(active))
    assert conn.execute("SELECT root_domain FROM runs").fetchone()[0] == "new.example"
    conn.close()
    old = get_connection(result.safety_backup)
    assert old.execute("SELECT root_domain FROM runs").fetchone()[0] == "old.example"
    old.close()


def test_restore_rejects_corrupt_file_without_changing_target(tmp_path: Path) -> None:
    active = tmp_path / "active.sqlite"
    _database(active, "safe.example")
    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises(ValueError, match="cannot inspect"):
        restore_database(corrupt, active)
    conn = get_connection(str(active))
    assert conn.execute("SELECT root_domain FROM runs").fetchone()[0] == "safe.example"
    conn.close()


def test_retention_previews_then_deletes_complete_old_run(tmp_path: Path) -> None:
    path = tmp_path / "retention.sqlite"
    old_id, _ = _database(path, "old.example")
    conn = get_connection(str(path))
    repo = GraphRepository(conn)
    new_id = repo.create_run("new.example", 0, 1)
    conn.execute("UPDATE runs SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (old_id,))
    repo.upsert_domain_cache("old.example", [])
    conn.execute("UPDATE domain_cache SET last_scraped = '2020-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()

    preview = apply_retention(
        path, run_before="2021-01-01", cache_before="2021-01-01", apply=False
    )
    assert (preview.runs, preview.cache_entries, preview.apply) == (1, 1, False)
    conn = get_connection(str(path))
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    conn.close()

    applied = apply_retention(
        path, run_before="2021-01-01", cache_before="2021-01-01", apply=True
    )
    assert applied.apply
    conn = get_connection(str(path))
    assert conn.execute("SELECT id FROM runs").fetchall()[0][0] == new_id
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE run_id = ?", (old_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM provider_calls WHERE run_id = ?", (old_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM domain_cache").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
