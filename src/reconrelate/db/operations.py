"""Operational SQLite safety: integrity checks, backups, restores, and retention."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reconrelate.db.db import get_connection, init_db, restrict_sqlite_file_permissions


REQUIRED_TABLES = {
    "runs", "observations", "claims", "claim_evidence", "model_calls", "schema_migrations",
}


@dataclass(frozen=True, slots=True)
class DatabaseCheck:
    path: str
    integrity: str
    foreign_key_violations: int
    migration_versions: tuple[int, ...]
    required_tables_present: bool

    @property
    def ok(self) -> bool:
        return (
            self.integrity == "ok"
            and self.foreign_key_violations == 0
            and self.required_tables_present
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def check_database(path: str | Path) -> DatabaseCheck:
    db_path = _resolved(path)
    if not db_path.is_file():
        raise ValueError(f"database does not exist: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
        integrity = "ok" if integrity_rows == [("ok",)] else "; ".join(str(row[0]) for row in integrity_rows)
        foreign_key_violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions: tuple[int, ...] = ()
        if "schema_migrations" in tables:
            versions = tuple(
                int(row[0])
                for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")
            )
        return DatabaseCheck(
            path=str(db_path),
            integrity=integrity,
            foreign_key_violations=foreign_key_violations,
            migration_versions=versions,
            required_tables_present=REQUIRED_TABLES <= tables,
        )
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"cannot inspect SQLite database {db_path}: {exc}") from exc
    finally:
        conn.close()


def _backup_connection(source: sqlite3.Connection, destination: Path, *, overwrite: bool) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"backup already exists: {destination}; use --force to replace it")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    target = sqlite3.connect(str(temporary))
    try:
        source.backup(target)
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        target.close()
    result = check_database(temporary)
    if not result.ok:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"backup verification failed: {result.to_dict()}")
    os.replace(temporary, destination)
    restrict_sqlite_file_permissions(str(destination))
    return destination


def backup_database(
    source_path: str | Path,
    destination: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    source_file = _resolved(source_path)
    if not source_file.is_file():
        raise ValueError(f"database does not exist: {source_file}")
    if destination is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination_file = source_file.with_name(f"{source_file.name}.{stamp}.bak")
    else:
        destination_file = _resolved(destination)
    if source_file == destination_file:
        raise ValueError("backup destination must differ from the active database")
    source = get_connection(str(source_file))
    try:
        return _backup_connection(source, destination_file, overwrite=overwrite)
    finally:
        source.close()


@dataclass(frozen=True, slots=True)
class RestoreResult:
    restored_path: str
    source_backup: str
    safety_backup: str | None


def restore_database(source_backup: str | Path, target_path: str | Path) -> RestoreResult:
    source_file = _resolved(source_backup)
    target_file = _resolved(target_path)
    if source_file == target_file:
        raise ValueError("restore source must differ from the active database")
    candidate = check_database(source_file)
    if not candidate.ok:
        raise ValueError(f"restore source failed verification: {candidate.to_dict()}")

    safety_backup: Path | None = None
    if target_file.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safety_backup = target_file.with_name(f"{target_file.name}.pre-restore.{stamp}.bak")
        backup_database(target_file, safety_backup)

    target_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_file.with_name(f".{target_file.name}.{uuid.uuid4().hex}.restore")
    source = sqlite3.connect(f"file:{source_file.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(str(temporary))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    restored_check = check_database(temporary)
    if not restored_check.ok:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"restored database failed verification: {restored_check.to_dict()}")
    if safety_backup:
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{target_file}{suffix}")
            if sidecar.exists():
                sidecar_backup = Path(f"{safety_backup}{suffix}")
                os.replace(sidecar, sidecar_backup)
    os.replace(temporary, target_file)
    restrict_sqlite_file_permissions(str(target_file))
    return RestoreResult(str(target_file), str(source_file), str(safety_backup) if safety_backup else None)


def _cutoff(value: str) -> str:
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("cutoff must be ISO-8601, for example 2026-01-01 or 2026-01-01T00:00:00Z") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class RetentionResult:
    apply: bool
    run_cutoff: str
    cache_cutoff: str | None
    runs: int
    cache_entries: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_retention(
    db_path: str | Path,
    *,
    run_before: str,
    cache_before: str | None = None,
    apply: bool = False,
) -> RetentionResult:
    run_cutoff = _cutoff(run_before)
    cache_cutoff = _cutoff(cache_before) if cache_before else None
    conn = get_connection(str(_resolved(db_path)))
    init_db(conn)
    try:
        run_ids = [
            str(row[0])
            for row in conn.execute("SELECT id FROM runs WHERE created_at < ? ORDER BY created_at", (run_cutoff,))
        ]
        cache_count = 0
        if cache_cutoff:
            cache_count = int(
                conn.execute("SELECT COUNT(*) FROM domain_cache WHERE last_scraped < ?", (cache_cutoff,)).fetchone()[0]
            )
        result = RetentionResult(apply, run_cutoff, cache_cutoff, len(run_ids), cache_count)
        if not apply:
            return result
        conn.execute("BEGIN IMMEDIATE")
        try:
            for run_id in run_ids:
                conn.execute("DELETE FROM run_tasks WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM provider_calls WHERE run_id = ?", (run_id,))
                conn.execute(
                    "DELETE FROM claim_evidence WHERE claim_id IN (SELECT id FROM claims WHERE run_id = ?)",
                    (run_id,),
                )
                conn.execute("DELETE FROM claims WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM observations WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM processed_domains WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM pivot_decisions WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM lineage WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM edges WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM nodes WHERE run_id = ?", (run_id,))
                conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            if cache_cutoff:
                conn.execute("DELETE FROM domain_cache WHERE last_scraped < ?", (cache_cutoff,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return result
    finally:
        conn.close()
