"""Data coverage reports for the local CSV lake."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DATASETS = [
    "activities",
    "sleep_summary",
    "sleep_stages",
    "hrv",
    "hrv_nightly",
    "stress",
    "stress_samples",
    "body_battery",
    "body_battery_samples",
    "body_composition",
    "daily_summary",
    "readiness",
    "training_status",
    "thresholds",
    "zone_definitions",
    "personal_records",
    "race_predictions",
    "scores",
    "workout_adherence",
    "weather",
    "strength_sets",
    "activity_stream_stats",
    "subjective",
]


def read_folder(folder: Path) -> pd.DataFrame:
    files = sorted(folder.glob("*.csv"))
    frames = []
    for file_path in files:
        try:
            frames.append(pd.read_csv(file_path))
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _date_series(frame: pd.DataFrame) -> pd.Series:
    if "date_local" in frame.columns:
        return pd.to_datetime(frame["date_local"], errors="coerce").dt.date
    if "calendarDate" in frame.columns:
        return pd.to_datetime(frame["calendarDate"], errors="coerce").dt.date
    if "date" in frame.columns:
        return pd.to_datetime(frame["date"], errors="coerce").dt.date
    if "hrvDate" in frame.columns:
        return pd.to_datetime(
            pd.to_numeric(frame["hrvDate"], errors="coerce"), unit="s", errors="coerce"
        ).dt.date
    if "timestamp" in frame.columns:
        return pd.to_datetime(
            pd.to_numeric(frame["timestamp"], errors="coerce"), unit="s", errors="coerce"
        ).dt.date
    return pd.Series([], dtype=object)


def dataset_coverage(
    data_dir: Path,
    dataset: str,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    frame = read_folder(Path(data_dir) / dataset)
    if frame.empty:
        return {
            "dataset": dataset,
            "rows": 0,
            "days": 0,
            "expected_days": 0,
            "coverage_pct": 0.0,
            "first": None,
            "last": None,
            "missing": [],
        }
    dates = _date_series(frame).dropna()
    unique = sorted(set(dates))
    first = unique[0] if unique else None
    last = unique[-1] if unique else None
    if start is None:
        start = first
    if end is None:
        end = last or date.today()
    expected = 0
    missing: list[str] = []
    if start and end:
        expected = (end - start).days + 1
        present = set(unique)
        cursor = start
        while cursor <= end:
            if cursor not in present:
                missing.append(cursor.isoformat())
            cursor += timedelta(days=1)
    return {
        "dataset": dataset,
        "rows": int(len(frame)),
        "days": len(unique),
        "expected_days": expected,
        "coverage_pct": round(100 * len(unique) / expected, 1) if expected else None,
        "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None,
        "missing": missing[:31],
        "missing_count": len(missing),
    }


def coverage_report(data_dir: Path, days: int = 90) -> dict:
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = [dataset_coverage(data_dir, name, start, end) for name in DATASETS]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "datasets": rows,
    }


def render_coverage(report: dict) -> str:
    lines = [
        f"# Data coverage ({report['window']['start']} .. {report['window']['end']})",
        "",
        "| Dataset | Rows | Days | Coverage | First | Last |",
        "| --- | ---: | ---: | ---: | --- | --- |",
    ]
    for row in report["datasets"]:
        pct = f"{row['coverage_pct']}%" if row["coverage_pct"] is not None else "n/a"
        lines.append(
            f"| {row['dataset']} | {row['rows']} | {row['days']} | {pct} | "
            f"{row['first'] or '-'} | {row['last'] or '-'} |"
        )
    return "\n".join(lines) + "\n"
