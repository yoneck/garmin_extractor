"""Build one consolidated JSONL file with full per-activity detail + biometric context.

Reads every partitioned table under ``garmin_data`` and writes a single JSONL
file, one object per activity, where each object embeds its laps / splits /
power / HR arrays plus the primary summary fields. A companion ``.meta.json``
sidecar indexes the full biometric picture so an LLM / tooling can walk
activities and their detail plus the biometric context.

Output:
    garmin_data/full_context.jsonl   - one JSON object per activity
    garmin_data/full_context.meta.json - row/column counts + sample rows
"""

from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "garmin_data"
OUTPUT = DATA_DIR / "full_context.jsonl"
META = DATA_DIR / "full_context.meta.json"

# (table, embedded key in output, activity key column in the table)
DETAIL_TABLES = [
    ("activity_laps", "laps", "activityId"),
    ("activity_splits", "splits", "activityId"),
    ("activity_powers", "power_zones", "activityId"),
    ("activity_hr", "hr_zones", "activityId"),
]

BIOMETRIC_TABLES = ["sleep_summary", "hrv", "stress", "body_battery", "body_composition"]


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
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _clean(record: dict) -> dict:
    out: dict = {}
    for k, v in record.items():
        if isinstance(v, pd.Timestamp) and pd.isna(v):
            out[k] = None
        elif isinstance(v, float) and pd.isna(v):
            out[k] = None
        elif isinstance(v, (pd.Timedelta,)) and pd.isna(v):
            out[k] = None
        else:
            out[k] = v
    return out


def _group_by_activity(frame: pd.DataFrame, activity_key: str) -> dict[str, list[dict]]:
    if frame.shape[0] == 0 or activity_key not in frame.columns:
        return {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for group_id, sub in frame.groupby(activity_key):
        for rec in sub.to_dict(orient="records"):
            grouped[str(group_id)] = _clean(rec)
    return grouped


def build() -> tuple[Path, int, dict]:
    detail_frames = {t: _read_csv_folder(DATA_DIR / t) for t, _, _ in DETAIL_TABLES}
    detail_by_activity: dict[str, dict[str, list[dict]]] = {}
    for table, _, activity_key in DETAIL_TABLES:
        detail_by_activity[table] = _group_by_activity(detail_frames[table], activity_key)

    activities = _read_csv_folder(DATA_DIR / "activities")
    if activities.shape[0] == 0:
        return OUTPUT, 0, {}

    records: list[dict] = []
    for group_id, sub in activities.groupby("activityId"):
        rec: dict[str, object] = {}
        for col in ["timestamp", "sportType", "activityName", "locationName",
                    "distanceMeter", "durationSecond", "averageHeartRate",
                    "maxHeartRate", "averageSpeedKmh", "calories",
                    "aerobicTrainingEffect", "anaerobicTrainingEffect"]:
            if col in sub.columns:
                rec[col] = sub.iloc[0][col]
        rec["activityId"] = int(group_id)
        rec["type"] = "activity"
        details: dict[str, list[dict]] = {}
        for table, key, _ in DETAIL_TABLES:
            arr = detail_by_activity[table].get(str(rec["activityId"]))
            if arr:
                details[key] = arr
        if details:
            rec["details"] = details
        records.append(rec)

    for table in BIOMETRIC_TABLES:
        df = _read_csv_folder(DATA_DIR / table)
        if not df.empty:
            for _, row in df.iterrows():
                biometric_rec = {"type": "biometric", "table": table}
                biometric_rec.update(_clean(row.to_dict()))
                records.append(biometric_rec)

    OUTPUT.write_text("\n".join(json.dumps(r, default=str) for r in records) + "\n", encoding="utf-8")

    biometric = {}
    for table in BIOMETRIC_TABLES:
        frame = _read_csv_folder(DATA_DIR / table)
        if frame.shape[0]:
            biometric[table] = {
                "rows": int(frame.shape[0]),
                "columns": list(frame.columns),
                "sample": [_clean(r) for r in frame.head(3).to_dict(orient="records")],
            }

    activity_detail_counts = {
        key: sum(len(v) for v in detail_by_activity[table].values())
        for table, key, _ in DETAIL_TABLES
    }

    meta = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_dir": str(DATA_DIR),
        "output": str(OUTPUT),
        "activity_count": len(records),
        "activities_with_details": sum(1 for r in records if "details" in r),
        "activity_detail_counts": activity_detail_counts,
        "biometric": biometric,
    }
    META.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return OUTPUT, len(records), meta


def main() -> int:
    out, count, meta = build()
    print(f"Wrote {count} activity records to {out}")
    if count:
        print(f"  activities with detail: {meta['activities_with_details']}")
        print(f"  activity detail counts: {meta['activity_detail_counts']}")
    if meta["biometric"]:
        for table, info in meta["biometric"].items():
            print(f"  {table}: {info['rows']} rows")
    else:
        print("  (no biometric tables with data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
