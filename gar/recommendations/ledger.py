"""Recommendation ledger: store, review and score training recommendations."""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from gar.lake.db import connect, table_exists


def _con(data_dir: Path):
    return connect(data_dir)


def log_recommendation(
    data_dir: Path,
    text: str,
    horizon_days: int = 7,
    evidence: dict | None = None,
    status: str = "open",
) -> str:
    rec_id = uuid.uuid4().hex[:16]
    con = _con(data_dir)
    try:
        con.execute(
            """
            INSERT INTO fact_recommendation
                (rec_id, created_utc, horizon_days, recommendation, evidence_json, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                rec_id,
                datetime.now(timezone.utc).replace(tzinfo=None),
                int(horizon_days),
                text,
                json.dumps(evidence or {}, default=str),
                status,
            ],
        )
    finally:
        con.close()
    return rec_id


def list_recommendations(data_dir: Path, limit: int = 20) -> list[dict]:
    con = _con(data_dir)
    try:
        if not table_exists(con, "fact_recommendation"):
            return []
        rows = con.execute(
            """
            SELECT rec_id, created_utc, horizon_days, recommendation, status,
                   outcome, adhered, reviewed_utc
            FROM fact_recommendation
            ORDER BY created_utc DESC LIMIT ?
            """,
            [limit],
        ).fetchall()
        keys = [
            "rec_id",
            "created_utc",
            "horizon_days",
            "recommendation",
            "status",
            "outcome",
            "adhered",
            "reviewed_utc",
        ]
        return [dict(zip(keys, row)) for row in rows]
    finally:
        con.close()


def record_outcome(
    data_dir: Path,
    rec_id: str,
    outcome: str,
    adhered: bool | None = None,
    status: str = "reviewed",
) -> bool:
    con = _con(data_dir)
    try:
        con.execute(
            """
            UPDATE fact_recommendation
            SET outcome = ?, adhered = ?, status = ?, reviewed_utc = ?
            WHERE rec_id = ?
            """,
            [
                outcome,
                adhered,
                status,
                datetime.now(timezone.utc).replace(tzinfo=None),
                rec_id,
            ],
        )
        changed = con.execute(
            "SELECT count(*) FROM fact_recommendation WHERE rec_id = ?", [rec_id]
        ).fetchall()[0][0]
    finally:
        con.close()
    return bool(changed)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gar rec", description="Recommendation ledger.")
    parser.add_argument("--data-dir", help="Override GARMIN_DATA_DIR.")
    sub = parser.add_subparsers(dest="command", required=True)

    log = sub.add_parser("log", help="Store a recommendation.")
    log.add_argument("--text", required=True)
    log.add_argument("--horizon-days", type=int, default=7)
    log.add_argument("--evidence", help="JSON evidence blob.")
    log.add_argument("--json", dest="as_json", action="store_true")

    lst = sub.add_parser("list", help="List recommendations.")
    lst.add_argument("--limit", type=int, default=20)
    lst.add_argument("--json", dest="as_json", action="store_true")

    outcome = sub.add_parser("outcome", help="Record an outcome.")
    outcome.add_argument("--id", required=True, dest="rec_id")
    outcome.add_argument("--outcome", required=True)
    outcome.add_argument("--adhered", choices=["yes", "no"])
    outcome.add_argument("--json", dest="as_json", action="store_true")
    return parser


def main(argv: list[str] | None = None, data_dir: Path | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.data_dir:
        data_dir = Path(args.data_dir)
    if data_dir is None:
        import os

        data_dir = Path(os.environ.get("GARMIN_DATA_DIR") or Path("garmin_data"))
    data_dir = Path(data_dir)

    if args.command == "log":
        evidence = json.loads(args.evidence) if args.evidence else None
        rec_id = log_recommendation(
            data_dir, args.text, horizon_days=args.horizon_days, evidence=evidence
        )
        if args.as_json:
            print(json.dumps({"rec_id": rec_id}))
        else:
            print(f"logged recommendation {rec_id}")
        return 0

    if args.command == "list":
        rows = list_recommendations(data_dir, limit=args.limit)
        if args.as_json:
            print(json.dumps(rows, indent=2, default=str))
        else:
            for row in rows:
                print(
                    f"{row['rec_id']}  {str(row['created_utc'])[:16]}  "
                    f"{row['status']}  {row['recommendation'][:80]}"
                )
        return 0

    if args.command == "outcome":
        adhered = None
        if args.adhered == "yes":
            adhered = True
        elif args.adhered == "no":
            adhered = False
        ok = record_outcome(data_dir, args.rec_id, args.outcome, adhered=adhered)
        if args.as_json:
            print(json.dumps({"updated": ok}))
        else:
            print("updated" if ok else "not found")
        return 0 if ok else 1
    return 1
