"""DuckDB connection, schema bootstrap and small helpers."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import duckdb

SCHEMA_VERSION = "3.0.0"
LAKE_FILENAME = "garmin.duckdb"

_SAFE = re.compile(r"[^A-Za-z0-9_]+")
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def lake_path(data_dir: Path) -> Path:
    return Path(data_dir) / LAKE_FILENAME


def connect(
    data_dir: Path | None = None,
    path: Path | None = None,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Open (and initialise) the lake database."""
    if path is None:
        if data_dir is None:
            raise ValueError("provide data_dir or path")
        path = lake_path(Path(data_dir))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        init_schema(con)
    return con


def init_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    con.execute(
        "INSERT OR IGNORE INTO schema_migration (version, description) VALUES (?, ?)",
        [SCHEMA_VERSION, "core lake schema"],
    )


def safe_name(value: str) -> str:
    return _SAFE.sub("_", str(value)).strip("_") or "unnamed"


def table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).fetchall()
    return bool(row and row[0][0])


def columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    if not table_exists(con, table):
        return []
    rows = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [row[1] for row in rows]


def row_count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    if not table_exists(con, table):
        return 0
    return int(con.execute(f'SELECT count(*) FROM "{table}"').fetchall()[0][0])


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value) -> None:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    con.execute(
        """
        INSERT INTO meta (key, value, updated_at_utc)
        VALUES (?, ?, current_timestamp)
        ON CONFLICT (key) DO UPDATE SET
            value = excluded.value,
            updated_at_utc = now()
        """,
        [key, text],
    )


def get_meta(con: duckdb.DuckDBPyConnection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchall()
    return row[0][0] if row else None


def fingerprint(values) -> str:
    payload = json.dumps(values, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def log_quality(
    con: duckdb.DuckDBPyConnection,
    dataset: str,
    rule: str,
    severity: str,
    rows: int,
    details: str | None = None,
) -> None:
    log_id = fingerprint(
        [
            dataset,
            rule,
            severity,
            rows,
            datetime.now(timezone.utc).isoformat(),
        ]
    )[:32]
    con.execute(
        """
        INSERT INTO data_quality_log
            (log_id, dataset, rule, severity, rows_affected, details)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [log_id, dataset, rule, severity, int(rows), details],
    )


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'
