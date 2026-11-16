"""Parquet export for portable, columnar snapshots of the lake."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from gar.lake.db import quote_identifier, table_exists


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _tables(con: duckdb.DuckDBPyConnection, prefix: str | None = None) -> list[str]:
    rows = con.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    ).fetchall()
    names = [row[0] for row in rows]
    if prefix:
        names = [name for name in names if name.startswith(prefix)]
    return names


def export_parquet(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path,
    out_dir: Path | None = None,
    include_raw: bool = False,
    verbose: bool = True,
) -> dict:
    """Write each curated table to ``<out>/<table>.parquet``.

    ``fact_daily`` is additionally partitioned by year/month for cheap scans.
    """
    data_dir = Path(data_dir)
    out_dir = Path(out_dir) if out_dir else data_dir / "parquet"
    out_dir.mkdir(parents=True, exist_ok=True)

    tables = [
        name
        for name in _tables(con)
        if include_raw or not name.startswith("raw_")
    ]
    tables = [name for name in tables if table_exists(con, name)]
    exported = []
    for table in tables:
        target = out_dir / f"{table}.parquet"
        con.execute(
            f"COPY (SELECT * FROM {quote_identifier(table)}) "
            f"TO {_literal(target)} (FORMAT PARQUET)"
        )
        exported.append({"table": table, "path": str(target), "bytes": target.stat().st_size})

    partitioned = None
    if table_exists(con, "fact_daily"):
        partition_dir = out_dir / "fact_daily_by_month"
        partition_dir.mkdir(parents=True, exist_ok=True)
        for child in partition_dir.rglob("*.parquet"):
            child.unlink()
        con.execute(
            f"""
            COPY (
                SELECT *, year(date_local) AS year, month(date_local) AS month
                FROM fact_daily
                WHERE date_local IS NOT NULL
            ) TO {_literal(partition_dir)} (FORMAT PARQUET, PARTITION_BY (year, month))
            """
        )
        partitioned = str(partition_dir)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(out_dir),
        "tables": exported,
        "partitioned_fact_daily": partitioned,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )
    if verbose:
        print(f"  exported {len(exported)} table(s) to {out_dir}")
        if partitioned:
            print(f"  fact_daily partitioned by month -> {partitioned}")
    return manifest
