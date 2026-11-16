"""Activity extraction: fetch activities and write them to CSV storage."""

from __future__ import annotations

from datetime import datetime, timezone

TYPE_KEY_MAP = {
    1: "RUNNING",
    2: "CYCLING",
    3: "TRAIL_RUN",
    4: "WALKING",
    5: "HIKING",
    6: "SWIMMING",
    7: "INDOOR_RUN",
    8: "YOGA",
    21: "GYM",
    22: "MEDITATION",
    146: "TRAIL_TRAIL",
}


def extract_activities(
    client,
    storage,
    start_date: datetime,
    end_date: datetime,
    already_ids: set | None = None,
) -> list[str]:
    records: list[dict] = []
    zones: list[dict] = []
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = min(end_date, datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    print(f"Activities: {start_str} -> {end_str}")

    try:
        acts = client.get_activities_by_date(start_str, end_str) or []
    except Exception as error:
        print(f"  error: {error}")
        return []

    if not acts:
        print("  no activities.")
        return []

    seen = already_ids or set()
    new: list[dict] = []
    for act in acts:
        record = _build_record(act)
        if not record:
            continue
        activity_id = str(record["activityId"])
        if activity_id in seen:
            continue
        seen.add(activity_id)
        new.append(record)
        zones.extend(_zone_records(activity_id, act))

    if new:
        storage.sync("activities", new)
        print(f"  saved {len(new)} activities.")
    if zones:
        storage.sync_id("activity_zones", zones)
    return [r["activityId"] for r in new]


def _zone_records(activity_id: str, act: dict) -> list[dict]:
    records: list[dict] = []
    for zone in range(1, 6):
        hr_seconds = act.get(f"hrTimeInZone_{zone}")
        if hr_seconds is not None:
            records.append(
                {
                    "activityId": activity_id,
                    "zoneType": "hr",
                    "zone": zone,
                    "seconds": hr_seconds,
                }
            )
        power_seconds = act.get(f"powerTimeInZone_{zone}")
        if power_seconds is not None:
            records.append(
                {
                    "activityId": activity_id,
                    "zoneType": "power",
                    "zone": zone,
                    "seconds": power_seconds,
                }
            )
    return records


def _build_record(act: dict) -> dict | None:
    try:
        ts = int(act.get("beginTimestamp") or 0)
        if not ts:
            ts_str = act.get("startTimeGMT")
            if not ts_str:
                return None
            dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            ts = int(dt.timestamp() * 1000)
        if ts > 1e12:
            ts = int(ts / 1000)
        ts = int(ts)
        act_type = act.get("activityType") or {}
        type_key = (
            act_type.get("typeKey", "").upper()
            or TYPE_KEY_MAP.get(act_type.get("typeId", 0), "UNKNOWN")
        )
        return {
            "activityId": str(act.get("activityId", "")),
            "timestamp": ts,
            "sportType": type_key,
            "activityTypeKey": act_type.get("typeKey", ""),
            "activityName": act.get("activityName", ""),
            "locationName": act.get("locationName", ""),
            "distanceMeter": int(act.get("distance") or 0),
            "durationSecond": int(act.get("duration") or 0),
            "movingDurationSecond": int(act.get("movingDuration") or 0),
            "elapsedDurationSecond": int(act.get("elapsedDuration") or 0),
            "elevationGainMeter": int(act.get("elevationGain") or 0),
            "elevationLossMeter": int(act.get("elevationLoss") or 0),
            "averageHeartRate": int(act.get("averageHR") or 0),
            "maxHeartRate": int(act.get("maxHR") or 0),
            "averageSpeedKmh": round((act.get("averageSpeed") or 0) * 3.6, 1),
            "maxSpeedKmh": round((act.get("maxSpeed") or 0) * 3.6, 1),
            "avgPower": act.get("avgPower"),
            "normPower": act.get("normPower"),
            "maxPower": act.get("maxPower"),
            "averageCadence": act.get("averageRunningCadenceInStepsPerMinute")
            or act.get("averageBikeCadence"),
            "maxCadence": act.get("maxRunningCadenceInStepsPerMinute")
            or act.get("maxBikeCadence"),
            "avgStrideLength": act.get("avgStrideLength"),
            "avgGroundContactTime": act.get("avgGroundContactTime"),
            "avgVerticalOscillation": act.get("avgVerticalOscillation"),
            "avgVerticalRatio": act.get("avgVerticalRatio"),
            "calories": round(act.get("calories") or 0),
            "steps": act.get("steps"),
            "differenceBodyBattery": act.get("differenceBodyBattery"),
            "aerobicTrainingEffect": act.get("aerobicTrainingEffect", 0),
            "anaerobicTrainingEffect": act.get("anaerobicTrainingEffect", 0),
            "trainingEffectLabel": act.get("trainingEffectLabel", ""),
            "trainingLoad": act.get("activityTrainingLoad"),
            "vo2max": act.get("vO2MaxValue"),
            "moderateIntensityMinutes": int(act.get("moderateIntensityMinutes") or 0),
            "vigorousIntensityMinutes": int(act.get("vigorousIntensityMinutes") or 0),
            "isPR": act.get("isPR"),
            "workoutId": act.get("workoutId"),
            "deviceId": act.get("deviceId"),
            "lapCount": act.get("lapCount"),
            "timeZoneId": act.get("timeZoneId"),
            "startTimeLocal": act.get("startTimeLocal"),
            "startLatitude": act.get("startLatitude"),
            "startLongitude": act.get("startLongitude"),
        }
    except Exception:
        return None
