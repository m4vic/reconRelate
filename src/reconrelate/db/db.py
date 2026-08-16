from __future__ import annotations

import os
import hashlib
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path


_MIGRATION_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """Raised when a database migration is invalid, changed, or cannot be applied."""


def restrict_sqlite_file_permissions(db_path: str) -> None:
    """Best-effort user-only permissions on the DB file (Unix). No-op on Windows or :memory:."""
    if db_path == ":memory:" or os.name == "nt":
        return
    p = Path(db_path)
    if not p.is_file():
        return
    try:
        os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def get_connection(db_path: str) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()
    apply_migrations(conn)


def _migration_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError("migration ends with an incomplete SQL statement")
    return statements


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path | None = None) -> None:
    """Apply immutable numbered migrations atomically and verify applied checksums."""
    directory = migrations_dir or Path(__file__).with_name("migrations")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          checksum TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()

    migrations: list[tuple[int, str, Path]] = []
    if directory.exists():
        for path in directory.iterdir():
            match = _MIGRATION_RE.fullmatch(path.name)
            if match:
                migrations.append((int(match.group("version")), match.group("name"), path))
    migrations.sort(key=lambda item: item[0])
    if len({version for version, _, _ in migrations}) != len(migrations):
        raise MigrationError("duplicate migration version")

    applied = {
        int(row["version"]): (str(row["name"]), str(row["checksum"]))
        for row in conn.execute("SELECT version, name, checksum FROM schema_migrations")
    }
    for version, name, path in migrations:
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        previous = applied.get(version)
        if previous:
            if previous != (name, checksum):
                raise MigrationError(
                    f"applied migration {version:04d}_{previous[0]} has changed on disk"
                )
            continue

        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _migration_statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (version, name, checksum, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(f"failed migration {version:04d}_{name}: {exc}") from exc
