"""``gar lake`` subcommands: build and query the DuckDB analytical store."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gar.lake.db import (
    connect,
    lake_path,
    row_count,
    schema_version,
    table_exists,
)
from gar.lake.derive import derive_all
from gar.lake.export import export_csv, export_parquet
from gar.lake.ingest import dataset_status, discover_datasets, ingest_all
from gar.lake.state import get_sync_state
from gar.lake.validate import quarantine_count, validate_all


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gar lake",
        description="Build and inspect the DuckDB lake (garmin.duckdb).",
    )
    parser.add_argument("--data-dir", help="Override GARMIN_DATA_DIR.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create the database and metadata tables.")

    ingest = sub.add_parser("ingest", help="Load CSV files into raw_* tables.")
    ingest.add_argument("--datasets", help="Comma-separated subset.")
    ingest.add_argument(
        "--full-refresh",
        dest="full_refresh",
        action="store_true",
        help="Re-read CSVs even when the file fingerprint is unchanged.",
    )
    ingest.add_argument("--json", dest="as_json", action="store_true")

    sub.add_parser("derive", help="Build curated fact_* tables and flags.")

    validate = sub.add_parser("validate", help="Run data-quality rules.")
    validate.add_argument("--json", dest="as_json", action="store_true")

    export = sub.add_parser("export", help="Write Parquet or CSV snapshots.")
    export.add_argument("--out", help="Output directory (default: <data>/parquet or <data>/csv).")
    export.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    export.add_argument("--include-raw", action="store_true")

    status = sub.add_parser("status", help="Show lake and dataset status.")
    status.add_argument("--json", dest="as_json", action="store_true")

    query = sub.add_parser("query", help="Run a read-only SQL query.")
    query.add_argument("sql")
    query.add_argument("--json", dest="as_json", action="store_true")

    build = sub.add_parser("build", help="init + ingest + derive + validate.")
    build.add_argument("--datasets", help="Comma-separated subset.")
    build.add_argument("--validate", dest="do_validate", action="store_true")
    build.add_argument("--export", dest="do_export", action="store_true")
    build.add_argument(
        "--full-refresh",
        dest="full_refresh",
        action="store_true",
        help="Re-read CSVs even when the file fingerprint is unchanged.",
    )

    return parser


def _connect(data_dir: Path | None):
    if data_dir is None:
        raise ValueError("data_dir required")
    return connect(Path(data_dir))


def _status(con, data_dir: Path) -> dict:
    datasets = dataset_status(con, data_dir)
    curated = {}
    for table in (
        "fact_daily",
        "fact_activity",
        "fact_lap",
        "fact_zone",
        "fact_sleep_session",
        "fact_stress_sample",
        "fact_body_battery_sample",
        "fact_readiness",
        "fact_training_status",
        "fact_threshold",
        "fact_race_prediction",
        "fact_score",
        "fact_workout_adherence",
        "fact_weather",
        "fact_strength_set",
        "fact_subjective",
        "fact_flag",
    ):
        curated[table] = row_count(con, table) if table_exists(con, table) else 0
    sync_rows = []
    for row in datasets:
        state = get_sync_state(con, row["dataset"])
        row["watermark_utc"] = state.get("watermark_utc") if state else None
        row["ingested_at_utc"] = state.get("last_run_utc") if state else None
        row["in_sync"] = bool(state and state.get("status") == "ok")
        sync_rows.append(row)
    return {
        "database": str(lake_path(data_dir)),
        "schema_version": schema_version(con) or "unknown",
        "raw": sync_rows,
        "curated": curated,
        "quarantine_rows": quarantine_count(con),
    }


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.data_dir:
        data_dir = Path(args.data_dir)
    if data_dir is None:
        import os

        data_dir = Path(os.environ.get("GARMIN_DATA_DIR") or Path("garmin_data"))
    data_dir = Path(data_dir)

    if args.command == "init":
        con = _connect(data_dir)
        print(f"lake ready: {lake_path(data_dir)}")
        con.close()
        return 0

    if args.command == "query":
        con = _connect(data_dir)
        try:
            result = con.execute(args.sql)
            frame = result.df()
        finally:
            con.close()
        if args.as_json:
            print(frame.to_json(orient="records", date_format="iso"))
        else:
            print(frame.to_string(index=False))
        return 0

    con = _connect(data_dir)
    try:
        if args.command == "ingest":
            selected = (
                [name.strip() for name in args.datasets.split(",") if name.strip()]
                if getattr(args, "datasets", None)
                else None
            )
            counts = ingest_all(
                con,
                data_dir,
                datasets=selected,
                full_refresh=getattr(args, "full_refresh", False),
            )
            if getattr(args, "as_json", False):
                print(json.dumps(counts, indent=2))
            return 0

        if args.command == "derive":
            counts = derive_all(con)
            print(json.dumps(counts, indent=2))
            return 0

        if args.command == "validate":
            results = validate_all(con)
            if getattr(args, "as_json", False):
                print(json.dumps(results, indent=2))
            return 0

        if args.command == "export":
            out = Path(args.out) if args.out else None
            if args.format == "csv":
                result = export_csv(con, data_dir, out, include_raw=args.include_raw)
            else:
                manifest = export_parquet(con, data_dir, out, include_raw=args.include_raw)
                result = manifest
            print(json.dumps(result, indent=2, default=str))
            return 0

        if args.command == "status":
            status = _status(con, data_dir)
            if args.as_json:
                print(json.dumps(status, indent=2, default=str))
            else:
                print(f"database: {status['database']}")
                print(f"schema version: {status['schema_version']}")
                print(f"quarantine rows: {status['quarantine_rows']}")
                print("\nRaw datasets:")
                for row in status["raw"]:
                    state = "ok" if row["ingested"] else "missing"
                    print(
                        f"  {row['dataset']:24s} {row['rows']:8d} rows  "
                        f"{row['columns']:3d} cols  {state}"
                    )
                print("\nCurated tables:")
                for name, rows in status["curated"].items():
                    print(f"  {name:26s} {rows:8d} rows")
            return 0

        if args.command == "build":
            selected = (
                [name.strip() for name in args.datasets.split(",") if name.strip()]
                if args.datasets
                else None
            )
            print(f"Lake: {lake_path(data_dir)}")
            print(f"Datasets found: {', '.join(discover_datasets(data_dir)) or 'none'}")
            ingest_all(
                con,
                data_dir,
                datasets=selected,
                full_refresh=getattr(args, "full_refresh", False),
            )
            derive_all(con)
            if args.do_validate:
                validate_all(con)
            if args.do_export:
                export_parquet(con, data_dir)
            print("Lake build complete.")
            return 0
    finally:
        con.close()
    return 1
