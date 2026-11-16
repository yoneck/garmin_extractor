"""Incremental ingest state and watermarks.

The CSV mirror is the source of truth; this module remembers which files were
already loaded so a repeated ``gar lake ingest`` is cheap. A dataset is
re-ingested when the fingerprint (path, size and mtime) of any of its CSV files
changes, or when ``--full-refresh`` is requested.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from gar.lake.db import fingerprint as value_fingerprint
from gar.lake.db import table_exists


def files_fingerprint(files: list[Path]) -> str:
    listing = []
    for path in sorted(Path(item) for item in files):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        listing.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return value_fingerprint(listing)


def get_sync_state(con: duckdb.DuckDBPyConnection, dataset: str) -> dict | None:
    if not table_exists(con, "sync_state"):
        return None
    rows = con.execute(
        """
        SELECT dataset, watermark_utc, last_run_utc, status, rows, error,
               duration_s, fingerprint
        FROM sync_state WHERE dataset = ?
        """,
        [dataset],
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return {
        "dataset": row[0],
        "watermark_utc": row[1],
        "last_run_utc": row[2],
        "status": row[3],
        "rows": row[4],
        "error": row[5],
        "duration_s": row[6],
        "fingerprint": row[7],
    }


def is_current(
    con: duckdb.DuckDBPyConnection,
    dataset: str,
    fingerprint: str,
    table: str,
) -> bool:
    state = get_sync_state(con, dataset)
    if state is None or not table_exists(con, table):
        return False
    return (
        state.get("fingerprint") == fingerprint
        and state.get("status") == "ok"
        and state.get("error") is None
    )


def record_sync_state(
    con: duckdb.DuckDBPyConnection,
    dataset: str,
    status: str,
    rows: int,
    fingerprint: str | None = None,
    error: str | None = None,
    duration_s: float | None = None,
    watermark_utc: datetime | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO sync_state
            (dataset, watermark_utc, last_run_utc, status, rows, error,
             duration_s, fingerprint)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (dataset) DO UPDATE SET
            watermark_utc = coalesce(excluded.watermark_utc, sync_state.watermark_utc),
            last_run_utc = excluded.last_run_utc,
            status = excluded.status,
            rows = excluded.rows,
            error = excluded.error,
            duration_s = excluded.duration_s,
            fingerprint = excluded.fingerprint
        """,
        [
            dataset,
            watermark_utc,
            datetime.now(timezone.utc).replace(tzinfo=None),
            status,
            int(rows),
            error,
            duration_s,
            fingerprint,
        ],
    )


def set_watermark(
    con: duckdb.DuckDBPyConnection,
    dataset: str,
    watermark_utc: datetime,
    rows: int = 0,
) -> None:
    record_sync_state(
        con,
        dataset,
        status="ok",
        rows=rows,
        watermark_utc=watermark_utc,
    )


def watermark(con: duckdb.DuckDBPyConnection, dataset: str) -> datetime | None:
    state = get_sync_state(con, dataset)
    return state.get("watermark_utc") if state else None


def clear_watermark(con: duckdb.DuckDBPyConnection, dataset: str) -> None:
    con.execute("DELETE FROM sync_state WHERE dataset = ?", [dataset])


def state_json(con: duckdb.DuckDBPyConnection) -> str:
    if not table_exists(con, "sync_state"):
        return "[]"
    rows = con.execute(
        """
        SELECT dataset, watermark_utc, last_run_utc, status, rows, error,
               duration_s, fingerprint
        FROM sync_state ORDER BY dataset
        """
    ).fetchall()
    keys = (
        "dataset",
        "watermark_utc",
        "last_run_utc",
        "status",
        "rows",
        "error",
        "duration_s",
        "fingerprint",
    )
    return json.dumps([dict(zip(keys, row)) for row in rows], indent=2, default=str)


def fingerprint_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
