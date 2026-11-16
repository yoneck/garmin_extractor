"""Build one consolidated JSONL file with full per-activity detail + biometric context.

Reads every partitioned table under ``garmin_data`` and writes a single JSONL
file. Each activity is one JSON object with nested **lists** of laps, splits,
HR zones and power zones. Biometric records are emitted as separate objects
with a stable ``type``/``table`` discriminator.

Output:
    garmin_data/full_context.jsonl     - activity objects + biometric objects
    garmin_data/full_context.meta.json - counts, columns, samples, hash
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("GARMIN_DATA_DIR") or PROJECT_ROOT / "garmin_data")
SCHEMA_VERSION = "2"

# (table, embedded key in output, activity key column, natural key)
DETAIL_TABLES = [
    ("activity_laps", "laps", "activityId", ("activityId", "lapIndex")),
    ("activity_splits", "splits", "activityId", ("activityId", "lapIndex")),
    ("activity_powers", "power_zones", "activityId", ("activityId", "zone")),
    ("activity_hr", "hr_zones", "activityId", ("activityId", "zone")),
    ("activity_zones", "zones", "activityId", ("activityId", "zoneType", "zone")),
]

BIOMETRIC_TABLES = [
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
]


def _read_csv_folder(folder: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(folder / "*.csv")))
    if not files:
        return pd.DataFrame()
    frames = []
    for file_path in files:
        try:
            frames.append(pd.read_csv(file_path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _dedupe(frame: pd.DataFrame, keys: tuple[str, ...]) -> pd.DataFrame:
    if frame.empty or any(key not in frame.columns for key in keys):
        return frame
    work = frame.copy()
    for key in keys:
        work[key] = work[key].astype(str)
    key_series = work[list(keys)].agg("|".join, axis=1)
    return work.loc[~key_series.duplicated(keep="last")].reset_index(drop=True)


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp,)) and pd.isna(value):
        return None
    if isinstance(value, (pd.Timedelta,)) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def _clean_record(record: dict) -> dict:
    return {key: _clean(value) for key, value in record.items()}


def _group_by_activity(frame: pd.DataFrame, activity_key: str) -> dict[str, list[dict]]:
    """Group detail rows per activity. Appends every row (never overwrites)."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    if frame.empty or activity_key not in frame.columns:
        return {}
    for group_id, sub in frame.groupby(activity_key):
        for record in sub.to_dict(orient="records"):
            grouped[str(group_id)].append(_clean_record(record))
    return dict(grouped)


def _activity_records(activities: pd.DataFrame) -> list[dict]:
    if activities.empty or "activityId" not in activities.columns:
        return []
    activities = _dedupe(activities, ("activityId",))
    records: list[dict] = []
    for group_id, sub in activities.groupby("activityId"):
        row = sub.iloc[-1].to_dict()
        record = _clean_record(row)
        try:
            record["activityId"] = int(float(group_id))
        except (TypeError, ValueError):
            record["activityId"] = str(group_id)
        record["type"] = "activity"
        records.append(record)
    return records


def build(data_dir: Path | None = None) -> tuple[Path, int, dict]:
    data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
    output = data_dir / "full_context.jsonl"
    meta_path = data_dir / "full_context.meta.json"

    detail_frames: dict[str, pd.DataFrame] = {}
    detail_by_activity: dict[str, dict[str, list[dict]]] = {}
    for table, _, activity_key, keys in DETAIL_TABLES:
        frame = _dedupe(_read_csv_folder(data_dir / table), keys)
        detail_frames[table] = frame
        detail_by_activity[table] = _group_by_activity(frame, activity_key)

    activities = _read_csv_folder(data_dir / "activities")
    activity_records = _activity_records(activities)
    if not activity_records:
        return output, 0, {}

    for record in activity_records:
        details: dict[str, list[dict]] = {}
        for table, key, _, _ in DETAIL_TABLES:
            rows = detail_by_activity[table].get(str(record["activityId"]))
            if rows:
                details[key] = rows
        if details:
            record["details"] = details

    biometric_records: list[dict] = []
    biometric_meta: dict[str, dict] = {}
    for table in BIOMETRIC_TABLES:
        frame = _read_csv_folder(data_dir / table)
        if frame.empty:
            continue
        biometric_meta[table] = {
            "rows": int(frame.shape[0]),
            "columns": list(frame.columns),
            "sample": [_clean_record(r) for r in frame.head(3).to_dict(orient="records")],
        }
        for row in frame.to_dict(orient="records"):
            biometric = {"type": "biometric", "table": table}
            biometric.update(_clean_record(row))
            biometric_records.append(biometric)

    records = activity_records + biometric_records
    payload = "\n".join(json.dumps(r, default=str) for r in records) + "\n"
    output.write_text(payload, encoding="utf-8")
    context_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    detail_counts = {
        key: sum(len(rows) for rows in detail_by_activity[table].values())
        for table, key, _, _ in DETAIL_TABLES
    }

    meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "schema_version": SCHEMA_VERSION,
        "source_dir": str(data_dir),
        "output": str(output),
        "activity_count": len(activity_records),
        "biometric_count": len(biometric_records),
        "total_records": len(records),
        "activities_with_details": sum(1 for r in activity_records if "details" in r),
        "activity_detail_counts": detail_counts,
        "biometric": biometric_meta,
        "context_hash": context_hash,
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return output, len(activity_records), meta


def main() -> int:
    out, count, meta = build()
    print(f"Wrote {count} activity records to {out}")
    if count:
        print(f"  activities with detail: {meta['activities_with_details']}")
        print(f"  activity detail counts: {meta['activity_detail_counts']}")
        print(f"  biometric records: {meta['biometric_count']}")
    else:
        print("  no activities found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
