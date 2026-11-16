"""Partitioned CSV storage with composite de-duplication keys.

Each data type lives in its own folder partitioned by month. De-duplication
uses natural keys per table; tables that hold multiple rows per activity use
composite keys (``activityId`` + ``lapIndex`` / ``zone``).

Guarantees:
- timestamps are normalised to seconds and must be positive,
- records dated too far in the future are rejected,
- writes are atomic (tmp file + ``os.replace``) and guarded by a file lock,
- schema drift is handled by column union; rows missing key columns are
  ignored (and can be quarantined) instead of silently collapsing data.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
from filelock import FileLock

SCHEMA_VERSION = "2"

# Natural keys per table. ``None`` means: require a timestamp column.
DEDUP_KEYS: dict[str, tuple[str, ...] | None] = {
    "activities": ("activityId",),
    "activity_laps": ("activityId", "lapIndex"),
    "activity_splits": ("activityId", "lapIndex"),
    "activity_hr": ("activityId", "zone"),
    "activity_powers": ("activityId", "zone"),
    "activity_power_metrics": ("activityId", "lapIndex"),
    "activity_zones": ("activityId", "zoneType", "zone"),
    "activity_streams": ("activityId", "sampleIndex"),
    "activity_stream_stats": ("activityId",),
    "sleep_summary": ("timestamp",),
    "sleep_stages": ("sleepId", "stageIndex"),
    "sleep_samples": ("sleepId", "kind", "timestamp"),
    "hrv": ("timestamp",),
    "hrv_nightly": ("hrvDate",),
    "stress": ("timestamp",),
    "stress_samples": ("timestamp",),
    "body_battery": ("timestamp",),
    "body_battery_samples": ("timestamp",),
    "body_battery_events": ("eventId",),
    "body_composition": ("timestamp",),
    "daily_summary": ("date",),
    "readiness": ("date",),
    "training_status": ("date", "deviceId"),
    "max_metrics": ("date",),
    "rhr": ("date",),
    "thresholds": ("date", "kind", "sport"),
    "zone_definitions": ("date", "sport", "zoneType", "zone"),
    "personal_records": ("recordId",),
    "race_predictions": ("date", "distance"),
    "scores": ("date", "scoreType"),
    "workouts": ("workoutId",),
    "workout_steps": ("workoutId", "stepIndex"),
    "workout_adherence": ("scheduledId",),
    "weather": ("activityId",),
    "strength_sets": ("activityId", "setIndex"),
    "gear": ("gearId",),
    "gear_usage": ("gearId", "activityId"),
    "goals": ("goalId",),
    "subjective": ("date",),
}

PER_ID_TABLES = {
    "activity_laps",
    "activity_splits",
    "activity_hr",
    "activity_powers",
    "activity_power_metrics",
    "activity_zones",
}

# Tables that are keyed only by timestamp but grouped by the same folder.
_TIME_SERIES_TABLES = {
    "sleep_summary",
    "sleep_stages",
    "sleep_samples",
    "hrv",
    "hrv_nightly",
    "stress",
    "stress_samples",
    "body_battery",
    "body_battery_samples",
    "body_composition",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_seconds(value) -> int | None:
    """Normalise a timestamp-ish value to positive Unix seconds or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number:  # NaN
            return None
        if number > 1e12:
            number /= 1000.0
        if number <= 0:
            return None
        return int(number)
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
        return _to_seconds(number)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


class CsvStorage:
    """Append-merge storage for partitioned CSV files."""

    def __init__(
        self,
        base_dir: Path,
        timezone_name: str = "UTC",
        max_future_days: int = 1,
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.max_future_days = int(max_future_days)
        try:
            self.tz = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            self.tz = ZoneInfo("UTC")
        self.timezone_name = timezone_name

    # -- public API --------------------------------------------------------
    def sync(
        self,
        data_type: str,
        records: list[dict],
        allow_future: bool = False,
    ) -> int:
        """Merge records partitioned by month (UTC legacy layout).

        ``allow_future`` is for planned/future-dated tables (scheduled
        workouts) where a future timestamp is legitimate.
        """
        frame = self._prepare(data_type, records, allow_future=allow_future)
        if frame.empty:
            return 0
        written = 0
        periods = frame["_date_utc"].dt.tz_localize(None).dt.to_period("M")
        for period, group in frame.groupby(periods):
            file_path = self._file_path(data_type, period.to_timestamp())
            target = group.drop(columns=[c for c in ("_date_utc",) if c in group])
            self._merge_write(file_path, target, data_type)
            written += len(target)
        return written

    def sync_id(self, data_type: str, records: list[dict]) -> int:
        """Store rows keyed by natural key inside a single CSV per table."""
        if not records:
            return 0
        frame = self._prepare(data_type, records)
        if frame.empty:
            return 0
        folder = self.base_dir / data_type
        folder.mkdir(parents=True, exist_ok=True)
        file_path = folder / f"{data_type}.csv"
        target = frame.drop(columns=[c for c in ("_date_utc",) if c in frame])
        return self._merge_write(file_path, target, data_type)

    def has_activity(self, data_type: str, activity_id: str) -> bool:
        if data_type != "activities":
            return False
        activities_dir = self.base_dir / data_type
        for file_path in activities_dir.glob("*.csv"):
            try:
                df = pd.read_csv(file_path, usecols=["activityId"])
            except Exception:
                continue
            if str(activity_id) in df["activityId"].astype(str).tolist():
                return True
        return False

    def latest_timestamp(self, data_type: str) -> datetime | None:
        """Return the newest timestamp present in a table's CSV files."""
        folder = self.base_dir / data_type
        if not folder.exists():
            return None
        newest: float | None = None
        for file_path in folder.glob("*.csv"):
            try:
                df = pd.read_csv(file_path, usecols=["timestamp"])
            except Exception:
                continue
            series = pd.to_numeric(df["timestamp"], errors="coerce").dropna()
            if series.empty:
                continue
            value = float(series.max())
            if newest is None or value > newest:
                newest = value
        if newest is None:
            return None
        return datetime.fromtimestamp(newest, tz=timezone.utc)

    def last_period(self, data_type: str) -> tuple[datetime, datetime] | None:
        """Backward-compatible helper: newest timestamp ± 1 day."""
        newest = self.latest_timestamp(data_type)
        if newest is None:
            return None
        return (newest - timedelta(days=1), newest + timedelta(days=1))

    def quarantine(self, data_type: str, records: list[dict], reason: str) -> None:
        """Append rejected records to a JSONL quarantine file (never drop silently)."""
        if not records:
            return
        folder = self.base_dir / "quarantine"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{data_type}.jsonl"
        now = _utcnow().isoformat()
        with FileLock(str(path) + ".lock"):
            with path.open("a", encoding="utf-8") as handle:
                for record in records:
                    handle.write(
                        json.dumps(
                            {
                                "quarantined_at": now,
                                "dataset": data_type,
                                "reason": reason,
                                "record": record,
                            },
                            default=str,
                        )
                        + "\n"
                    )

    # -- internals ---------------------------------------------------------
    def _file_path(self, data_type: str, period: datetime) -> Path:
        period_str = period.strftime("%Y_%m")
        folder = self.base_dir / data_type
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{period_str}.csv"

    def _prepare(
        self,
        data_type: str,
        records: list[dict],
        allow_future: bool = False,
    ) -> pd.DataFrame:
        if not records:
            return pd.DataFrame()
        frame = pd.DataFrame(records)
        keys = self._dedup_key(data_type)

        if "timestamp" in frame.columns:
            normalised = frame["timestamp"].map(_to_seconds)
            invalid = normalised.isna()
            if invalid.any():
                self.quarantine(
                    data_type,
                    frame[invalid].to_dict(orient="records"),
                    "invalid or non-positive timestamp",
                )
            frame = frame.loc[~invalid].copy()
            frame["timestamp"] = normalised.loc[~invalid].astype("int64")
            if frame.empty:
                return frame
            dates_utc = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
            future_limit = _utcnow() + timedelta(days=self.max_future_days)
            future = pd.Series(False, index=frame.index)
            if not allow_future:
                future = dates_utc > future_limit
            if future.any():
                self.quarantine(
                    data_type,
                    frame[future].to_dict(orient="records"),
                    "timestamp too far in the future",
                )
                frame = frame.loc[~future].copy()
                dates_utc = dates_utc.loc[~future]
            if frame.empty:
                return frame
            frame["_date_utc"] = dates_utc
            frame["date_local"] = dates_utc.dt.tz_convert(self.tz).dt.date.astype(str)

        if keys is not None:
            missing = [key for key in keys if key not in frame.columns]
            if missing:
                self.quarantine(
                    data_type,
                    frame.to_dict(orient="records"),
                    "missing key columns: " + ",".join(missing),
                )
                return pd.DataFrame()
            # Canonical string form prevents int/str mismatches during dedup.
            for key in keys:
                frame[key] = frame[key].astype(str)
        return frame

    def _merge_write(self, file_path: Path, new_df: pd.DataFrame, data_type: str) -> int:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = str(file_path) + ".lock"
        with FileLock(lock_path):
            existing = self._load_existing(file_path, data_type)
            if existing is not None and not existing.empty:
                combined = pd.concat([existing, new_df], ignore_index=True, sort=False)
            else:
                combined = new_df.reset_index(drop=True)
            keys = self._dedup_key(data_type)
            if keys is not None:
                missing = [key for key in keys if key not in combined.columns]
                if missing:
                    raise ValueError(
                        f"{data_type}: combined frame missing key columns {missing}"
                    )
                key_series = combined[list(keys)].astype(str).agg("|".join, axis=1)
                combined = combined.loc[~key_series.duplicated(keep="last")]
            combined = combined.reset_index(drop=True)
            self._atomic_write_csv(combined, file_path)
            return int(combined.shape[0])

    def _atomic_write_csv(self, frame: pd.DataFrame, file_path: Path) -> None:
        tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        frame.to_csv(tmp_path, index=False, encoding="utf-8")
        os.replace(tmp_path, file_path)

    def _dedup_key(self, data_type: str) -> tuple[str, ...] | None:
        if data_type in DEDUP_KEYS:
            return DEDUP_KEYS[data_type]
        return ("timestamp",)

    def _load_existing(self, file_path: Path, data_type: str) -> pd.DataFrame | None:
        if not file_path.exists():
            return None
        try:
            existing = pd.read_csv(file_path)
        except Exception:
            return None
        keys = self._dedup_key(data_type)
        if keys is not None:
            missing = [key for key in keys if key not in existing.columns]
            if missing:
                # Old/incompatible layout: ignore those rows and rebuild from API.
                return pd.DataFrame()
            for key in keys:
                existing[key] = existing[key].astype(str)
        return existing
