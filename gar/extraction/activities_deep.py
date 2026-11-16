"""Deep per-activity extraction: FIT download, laps, splits, power/HR zones."""

from __future__ import annotations

from pathlib import Path

_FTP_CACHE: dict[int, float | None] = {}


def extract_activity_depth(
    client,
    storage,
    data_dir: Path,
    activity: dict,
    ftp: float | None = None,
    download_fit: bool = False,
    fit_format: str = "fit",
) -> None:
    activity_id = str(activity.get("activityId"))
    if not activity_id:
        return

    if ftp is None:
        ftp = _get_ftp(client)

    split = client.get_activity_splits(activity_id) or {}
    lap_dtos = _as_list((split or {}).get("lapDTOs"))
    _write_laps(split, lap_dtos, activity_id, storage, ftp)

    hr_records: list[dict] = []
    power_records: list[dict] = []
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
            if isinstance(item, dict)
        ]
        if hr_records:
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
            if isinstance(item, dict)
        ]
        if power_records:
            storage.sync_id("activity_powers", power_records)
    except Exception as error:
        print(f"  power error for {activity_id}: {error}")

    if download_fit:
        _download_fit(client, storage, data_dir, activity_id, fit_format)

    print(
        f"  wrote {len(lap_dtos)} laps, {len(hr_records)} HR zones, "
        f"{len(power_records)} power zones for {activity_id}"
    )


def _write_laps(split, lap_dtos, activity_id, storage, ftp):
    split_records = [_serialize_lap(activity_id, index, lap, ftp) for index, lap in enumerate(lap_dtos, start=1)]
    if split_records:
        storage.sync_id("activity_splits", split_records)

    laps_records = [
        {
            "activityId": activity_id,
            "lapIndex": lap.get("lapIndex", index),
            "startTime": _parse_iso(lap.get("startTimeGMT")),
            "distance_m": lap.get("distance"),
            "duration_s": lap.get("duration"),
            "movingDuration_s": lap.get("movingDuration"),
            "speed_mps": lap.get("averageMovingSpeed"),
            "avgPower": lap.get("averagePower"),
            "maxPower": lap.get("maxPower"),
            "normalizedPower": lap.get("normalizedPower"),
            "avgHR": lap.get("averageHR"),
            "maxHR": lap.get("maxHR"),
            "bikeCadence": lap.get("averageBikeCadence"),
        }
        for index, lap in enumerate(lap_dtos, start=1)
    ]
    if laps_records:
        storage.sync_id("activity_laps", laps_records)

    metrics_records = _compute_power_metrics(activity_id, lap_dtos, ftp)
    if metrics_records:
        storage.sync_id("activity_power_metrics", metrics_records)


def _serialize_lap(activity_id, index, lap, ftp):
    return {
        "activityId": activity_id,
        "lapIndex": lap.get("lapIndex", index),
        "splitType": lap.get("splitType") or lap.get("lapType"),
        "distance_m": lap.get("distance"),
        "duration_s": lap.get("duration"),
        "avgSpeed_mps": lap.get("averageSpeed") or lap.get("averageMovingSpeed"),
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
    for index, lap in enumerate(lap_dtos, start=1):
        ap = lap.get("averagePower") or 0
        np = lap.get("normalizedPower") or 0
        if not np:
            continue
        intensity_factor = np / ftp
        duration = lap.get("duration") or lap.get("movingDuration") or 0
        tss = (duration * np * intensity_factor) / (ftp * 3600) * 100 if duration else None
        records.append(
            {
                "activityId": activity_id,
                "lapIndex": lap.get("lapIndex", index),
                "ftp": ftp,
                "averagePower": ap,
                "normalizedPower": np,
                "intensityFactor": round(intensity_factor, 4),
                "normalizedPowerFactor": round(intensity_factor, 4),
                "tss": round(tss, 2) if tss is not None else None,
            }
        )
    return records


def _get_ftp(client):
    cache_key = id(client)
    if cache_key in _FTP_CACHE:
        return _FTP_CACHE[cache_key]
    ftp = None
    try:
        ftp_dict = client.get_cycling_ftp()
        if isinstance(ftp_dict, list) and ftp_dict:
            ftp = ftp_dict[-1].get("functionalThresholdPower")
        elif isinstance(ftp_dict, dict):
            ftp = ftp_dict.get("functionalThresholdPower")
    except Exception:
        ftp = None
    if not ftp:
        try:
            profile = client.get_user_profile()
            ftp = profile.get("cyclingFtp") or profile.get("ftp")
        except Exception:
            pass
    _FTP_CACHE[cache_key] = ftp
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


def _download_fit(
    client,
    storage,
    data_dir: Path,
    activity_id: str,
    fit_format: str = "fit",
) -> Path | None:
    formats = getattr(client, "ActivityDownloadFormat", None)
    if formats is None:
        return None
    requested = (fit_format or "fit").lower()
    if requested == "fit":
        dl_fmt = formats.ORIGINAL
        extension = ".zip"  # ORIGINAL returns a zip archive containing the FIT
    elif requested == "tcx":
        dl_fmt = formats.TCX
        extension = ".tcx"
    elif requested == "gpx":
        dl_fmt = formats.GPX
        extension = ".gpx"
    elif requested == "original":
        dl_fmt = formats.ORIGINAL
        extension = ".zip"
    else:
        raise ValueError(f"unsupported fit format: {fit_format}")

    folder = Path(data_dir) / "fit"
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{activity_id}{extension}"
    if file_path.exists():
        return file_path
    try:
        raw = client.download_activity(activity_id, dl_fmt=dl_fmt)
        file_path.write_bytes(raw)
        return file_path
    except Exception as error:
        print(f"  FIT download failed for {activity_id}: {error}")
        return None
