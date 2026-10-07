"""Partitioned CSV storage: merge+dedup of extracted records.

Each data type lives in its own folder partitioned by month, matching the
layout already produced by the first version of the extractor. De-duplication
keys are per type:

- activities -> ``activityId``
- everything else -> ``timestamp``
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DEDUP_KEYS = {"activities": "activityId", "sleep": "timestamp"}
PER_ID_TABLES = {
    "activity_laps": "activityId",
    "activity_splits": "activityId",
    "activity_hr": "activityId",
    "activity_powers": "activityId",
    "activity_power_metrics": "activityId",
}


class CsvStorage:
    """Append-merge storage for partitioned CSV files."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, data_type: str, period: datetime) -> Path:
        period_str = period.strftime("%Y_%m")
        folder = self.base_dir / data_type
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{period_str}.csv"

    def sync(self, data_type: str, records: list[dict]):
        new_df = pd.DataFrame(records)
        if "timestamp" not in new_df.columns:
            return
        if (new_df["timestamp"] > 1e12).any():
            new_df["timestamp"] = new_df["timestamp"] / 1000
        new_df["date"] = pd.to_datetime(new_df["timestamp"], unit="s", errors="coerce")
        new_df = new_df.dropna(subset=["date"])

        for period, group in new_df.groupby(new_df["date"].dt.to_period("M")):
            file_path = self._file_path(data_type, period.to_timestamp())
            group_to_merge = group.drop(columns=["date"])

            if file_path.exists():
                existing = self._load_existing(file_path, data_type)
                combined = pd.concat([existing, group_to_merge], ignore_index=True)
                combined.drop_duplicates(subset=[self._dedup_key(data_type)], keep="first", inplace=True)
                combined.to_csv(file_path, index=False, encoding="utf-8")
            else:
                group_to_merge.to_csv(file_path, index=False, encoding="utf-8")

    def has_activity(self, data_type: str, activity_id: str) -> bool:
        if data_type != "activities":
            return False
        activities_dir = self.base_dir / data_type
        pattern = activities_dir.glob("*.csv")
        for file_path in pattern:
            try:
                df = pd.read_csv(file_path, engine="pyarrow", usecols=["activityId"])
            except Exception:
                continue
            if any(value == activity_id for value in df["activityId"].tolist()):
                return True
        return False

    def last_period(self, data_type: str) -> tuple[datetime, datetime] | None:
        activities_dir = self.base_dir / data_type
        if not activities_dir.exists():
            return None
        oldest: datetime | None = None
        for file_path in activities_dir.glob("*.csv"):
            try:
                df = pd.read_csv(file_path, usecols=["timestamp"])
                smallest_value = df["timestamp"].min()
            except Exception:
                continue
            if smallest_value is None:
                continue
            candidate = datetime.fromtimestamp(float(smallest_value), tz=timezone.utc)
            if oldest is None or candidate < oldest:
                oldest = candidate
        if oldest is None:
            return None
        return (oldest - timedelta(days=1), oldest + timedelta(days=1))

    def _per_id_folder(self, data_type: str) -> Path:
        folder = self.base_dir / data_type
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def sync_id(self, data_type: str, records: list[dict]) -> int:
        """Store records keyed by activityId inside a single CSV per table."""
        if not records:
            return 0
        key = self._dedup_key(data_type)
        if data_type in PER_ID_TABLES:
            key = PER_ID_TABLES[data_type]
            folder = self._per_id_folder(data_type)
            file_path = folder / f"{data_type}.csv"
            existing = pd.read_csv(file_path) if file_path.exists() else pd.DataFrame()
            new_df = pd.DataFrame(records)
            if "timestamp" in new_df.columns:
                new_df["timestamp"] = pd.to_numeric(new_df["timestamp"], errors="coerce")
            combined = pd.concat([existing, new_df], ignore_index=True) if existing.shape[0] else new_df
            combined_keys = combined[key].astype(str)
            deduped = combined[~combined_keys.duplicated(keep="first")]
            deduped = deduped.reset_index(drop=True)
            deduped.to_csv(file_path, index=False, encoding="utf-8")
            return int(deduped.shape[0])
        self.sync(data_type, records)
        return len(records)

    @staticmethod
    def _dedup_key(data_type: str) -> str:
        return DEDUP_KEYS.get(data_type, "timestamp")

    @staticmethod
    def _load_existing(file_path, data_type: str) -> pd.DataFrame:
        existing = pd.read_csv(file_path)
        key = "activityId" if data_type == "activities" else "timestamp"
        existing[key] = existing[key].astype(str)
        return existing
