"""CSV -> DuckDB ingestion.

Every CSV table under ``garmin_data/<dataset>`` becomes ``raw_<dataset>``. The
raw tables keep the original column names (no silent renaming) and are rebuilt
from the current files, which keeps ingestion idempotent.

Curated, unit-normalised tables are produced by :mod:`gar.lake.derive`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from gar.lake.db import (
    columns,
    fingerprint,
    log_quality,
    quote_identifier,
    row_count,
    safe_name,
    set_meta,
    table_exists,
)

# Folders that are not plain CSV datasets.
EXCLUDED_DIRS = {
    "raw",
    "context",
    "state",
    "quarantine",
    "fit",
    "lake",
    "parquet",
    "logs",
    ".gar_logs",
}


def discover_datasets(data_dir: Path) -> list[str]:
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return []
    found = []
    for folder in sorted(data_dir.iterdir()):
        if not folder.is_dir() or folder.name in EXCLUDED_DIRS:
            continue
        if folder.name.startswith("."):
            continue
        if any(folder.glob("*.csv")):
            found.append(folder.name)
    return found


def csv_files(data_dir: Path, dataset: str) -> list[Path]:
    return sorted((Path(data_dir) / dataset).glob("*.csv"))


def raw_table(dataset: str) -> str:
    return "raw_" + safe_name(dataset)


def _sql_list(paths: list[Path]) -> str:
    escaped = []
    for path in paths:
        text = str(path.resolve()).replace("'", "''")
        escaped.append("'" + text + "'")
    return "[" + ", ".join(escaped) + "]"


def ingest_dataset(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path,
    dataset: str,
    verbose: bool = False,
) -> int:
    files = csv_files(data_dir, dataset)
    if not files:
        return 0
    table = raw_table(dataset)
    source = _sql_list(files)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {quote_identifier(table)} AS
        SELECT
            *,
            filename AS _source_file,
            current_timestamp AS _ingested_at_utc
        FROM read_csv_auto(
            {source},
            union_by_name = true,
            filename = true,
            sample_size = -1
        )
        """
    )
    rows = row_count(con, table)
    schema = columns(con, table)
    set_meta(con, f"ingest.{dataset}.rows", str(rows))
    set_meta(con, f"ingest.{dataset}.files", str(len(files)))
    set_meta(con, f"ingest.{dataset}.columns", schema)
    set_meta(con, f"ingest.{dataset}.schema_hash", fingerprint(schema))
    set_meta(con, f"ingest.{dataset}.ingested_at_utc", datetime.now(timezone.utc).isoformat())
    log_quality(con, dataset, "ingest", "info", rows, f"{len(files)} file(s)")
    if verbose:
        print(f"  {dataset:24s} {rows:8d} rows -> {table}")
    return rows


def ingest_all(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path,
    datasets: list[str] | None = None,
    verbose: bool = True,
) -> dict[str, int]:
    available = discover_datasets(data_dir)
    selected = datasets or available
    counts: dict[str, int] = {}
    for dataset in selected:
        if dataset not in available:
            print(f"  skip {dataset}: no CSV files")
            continue
        counts[dataset] = ingest_dataset(con, data_dir, dataset, verbose=verbose)
    set_meta(con, "ingest.last_run_utc", datetime.now(timezone.utc).isoformat())
    set_meta(con, "ingest.datasets", sorted(counts))
    if verbose:
        total = sum(counts.values())
        print(f"  ingested {len(counts)} dataset(s), {total} rows")
    return counts


def dataset_status(con: duckdb.DuckDBPyConnection, data_dir: Path) -> list[dict]:
    rows = []
    for dataset in discover_datasets(data_dir):
        table = raw_table(dataset)
        exists = table_exists(con, table)
        rows.append(
            {
                "dataset": dataset,
                "table": table,
                "rows": row_count(con, table) if exists else 0,
                "columns": len(columns(con, table)) if exists else 0,
                "ingested": exists,
            }
        )
    return rows


def status_json(con: duckdb.DuckDBPyConnection, data_dir: Path) -> str:
    return json.dumps(dataset_status(con, data_dir), indent=2, default=str)
