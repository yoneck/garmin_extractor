"""Deep per-activity extraction: FIT/download, laps, splits, power/HR zones."""

from __future__ import annotations

import datetime
from pathlib import Path


def extract_activity_depth(client, storage, data_dir: Path, activity: dict) -> None:
    activity_id = str(activity.get("activityId"))
    if not activity_id:
        return

    split = client.get_activity_splits(activity_id) or {}
    lap_dtos = _as_list((split or {}).get("lapDTOs"))
    ftp = _get_ftp(client)
    _write_laps(split, lap_dtos, activity_id, storage, ftp)

    try:
        hr_list = client.get_activity_hr_in_timezones(activity_id) or []
        hr_records = [
            {
                "activityId": activity_id,
                "zone": item.get("zoneNumber"),
                "secondsInZone": item.get("secsInZone"),
                "zoneLowBoundary": item.get("zoneLowBoundary"),
            }
            for item in _as_list(hr_list)
        ]
        storage.sync_id("activity_hr", hr_records)
    except Exception as error:
        print(f"  HR error for {activity_id}: {error}")

    try:
        power_list = client.get_activity_power_in_timezones(activity_id) or []
        power_records = [
            {
                "activityId": activity_id,
                "zone": item.get("zoneNumber"),
                "secondsInZone": item.get("secsInZone"),
                "zoneLowBoundary": item.get("zoneLowBoundary"),
            }
            for item in _as_list(power_list)
        ]
        storage.sync_id("activity_powers", power_records)
    except Exception as error:
        print(f"  power error for {activity_id}: {error}")

    print(f"  wrote {len(lap_dtos)} laps, {len(hr_records)} HR zones, {len(power_records)} power zones for {activity_id}")


def _write_laps(split, lap_dtos, activity_id, storage, ftp):
    split_records = [
        _serialize_lap(activity_id, lap, ftp) for lap in lap_dtos
    ]
    storage.sync_id("activity_splits", split_records)

    laps_records = [
        {
            "activityId": activity_id,
            "lapIndex": lap.get("lapIndex"),
            "startTime": _parse_iso(lap.get("startTimeGMT")),
            "distanceKm": lap.get("distance"),
            "movingDurationSec": lap.get("movingDuration"),
            "movingSpeed": lap.get("averageMovingSpeed"),
            "avgPower": lap.get("averagePower"),
            "maxPower": lap.get("maxPower"),
            "normalizedPower": lap.get("normalizedPower"),
            "avgHR": lap.get("averageHR"),
            "maxHR": lap.get("maxHR"),
            "bikeCadence": lap.get("averageBikeCadence"),
        }
        for lap in lap_dtos
    ]
    storage.sync_id("activity_laps", laps_records)

    metrics_records = _compute_power_metrics(activity_id, lap_dtos, ftp)
    storage.sync_id("activity_power_metrics", metrics_records)


def _serialize_lap(activity_id, lap, ftp):
    return {
        "activityId": activity_id,
        "avgPower": lap.get("averagePower"),
        "minPower": lap.get("minPower"),
        "maxPower": lap.get("maxPower"),
        "normalizedPower": lap.get("normalizedPower"),
        "leftBalance": lap.get("leftBalance"),
        "rightBalance": lap.get("rightBalance"),
        "avgSeatedPower": lap.get("averageSeatedPower"),
        "avgStandingPower": lap.get("averageStandingPower"),
    }


def _compute_power_metrics(activity_id, lap_dtos, ftp):
    records = []
    if not ftp or not lap_dtos:
        return records
    for i, lap in enumerate(lap_dtos, start=1):
        ap = lap.get("averagePower") or 0
        np = lap.get("normalizedPower") or 0
        records.append({
            "activityId": activity_id,
            "lapIndex": lap.get("lapIndex", i),
            "ftp": ftp,
            "averagePower": ap,
            "normalizedPower": np,
            "intensityFactor": round(ap / ftp, 4) if ftp else None,
            "normalizedPowerFactor": round(np / ftp, 4) if ftp else None,
        })
    return records


def _get_ftp(client):
    try:
        ftp_dict = client.get_cycling_ftp()
        if isinstance(ftp_dict, list) and ftp_dict:
            ftp = ftp_dict[-1].get("functionalThresholdPower")
        elif isinstance(ftp_dict, dict):
            ftp = ftp_dict.get("functionalThresholdPower")
        else:
            ftp = None
    except Exception:
        ftp = None
    try:
        if not ftp:
            profile = client.get_user_profile()
            ftp = profile.get("cyclingFtp") or profile.get("ftp")
    except Exception:
        pass
    return ftp


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_iso(value):
    if not value:
        return None
    try:
        if "." in value:
            return value.split(".")[0] + "Z"
        return value
    except Exception:
        return None


def _download_fit(client, storage, data_dir: Path, activity_id: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    file_path = data_dir / f"{activity_id}.fit"
    if file_path.exists():
        return
    try:
        raw = client.download_activity(activity_id, dl_fmt=client.ActivityDownloadFormat.TCX)
        with open(file_path, "wb") as fh:
            fh.write(raw)
    except Exception as error:
        print(f"  FIT download failed for {activity_id}: {error}")
