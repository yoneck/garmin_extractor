"""Activity extraction: fetch activities and write them to CSV storage."""

from __future__ import annotations

from datetime import datetime, timedelta

TYPE_KEY_MAP = {
    1: "RUNNING", 2: "CYCLING", 3: "TRAIL_RUN", 4: "WALKING",
    5: "HIKING", 6: "SWIMMING", 7: "INDOOR_RUN", 8: "YOGA",
    21: "GYM", 22: "MEDITATION", 146: "TRAIL_TRAIL",
}


def extract_activities(client, storage, start_date: datetime, end_date: datetime, already_ids: set | None = None) -> list[str]:
    records = []
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    print(f"Activities: {start_str} -> {end_str}")

    if already_ids is not None and len(already_ids) > 2000:
        batch_ids = _chunk(already_ids)
        acts = []
        for chunk in batch_ids:
            acts.extend(client.get_activities_by_date(start_str, end_str) or [])
    else:
        try:
            acts = client.get_activities_by_date(start_str, end_str) or []
        except Exception as error:
            print(f"  error: {error}")
            return []

    if not acts:
        print("  no activities.")
        return []

    seen = already_ids or set()
    new = []
    for act in acts:
        record = _build_record(act)
        if not record:
            continue
        activity_id = str(record["activityId"])
        if activity_id in seen:
            continue
        seen.add(activity_id)
        new.append(record)

    if new:
        storage.sync("activities", new)
        print(f"  saved {len(new)} activities.")
    return [r["activityId"] for r in new]


def _build_record(act: dict) -> dict | None:
    try:
        ts = int(act.get("beginTimestamp") or 0)
        if not ts:
            ts_str = act.get("startTimeGMT")
            if not ts_str:
                return None
            dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
            ts = int(dt.timestamp() * 1000)
        if ts > 1e12:
            ts = int(ts / 1000)
        ts = int(ts)
        dt = datetime.fromtimestamp(ts)
        act_type = act.get("activityType") or {}
        type_key = (
            act_type.get("typeKey", "").upper()
            or TYPE_KEY_MAP.get(act_type.get("typeId", 0), "UNKNOWN")
        )
        return {
            "activityId": str(act.get("activityId", "")),
            "timestamp": ts,
            "sportType": type_key,
            "activityName": act.get("activityName", ""),
            "locationName": act.get("locationName", ""),
            "distanceMeter": int(act.get("distance") or 0),
            "durationSecond": int(act.get("duration") or 0),
            "elevationGainMeter": int(act.get("elevationGain") or 0),
            "elevationLossMeter": int(act.get("elevationLoss") or 0),
            "averageHeartRate": int(act.get("averageHR") or 0),
            "maxHeartRate": int(act.get("maxHR") or 0),
            "averageSpeedKmh": round((act.get("averageSpeed") or 0) * 3.6, 1),
            "maxSpeedKmh": round((act.get("maxSpeed") or 0) * 3.6, 1),
            "calories": round(act.get("calories") or 0),
            "aerobicTrainingEffect": act.get("aerobicTrainingEffect", 0),
            "anaerobicTrainingEffect": act.get("anaerobicTrainingEffect", 0),
            "trainingEffectLabel": act.get("trainingEffectLabel", ""),
            "moderateIntensityMinutes": int(act.get("moderateIntensityMinutes") or 0),
            "vigorousIntensityMinutes": int(act.get("vigorousIntensityMinutes") or 0),
            "startLatitude": act.get("startLatitude"),
            "startLongitude": act.get("startLongitude"),
        }
    except Exception:
        return None


def _chunk(items: set, size: int = 1000) -> list[set]:
    items = sorted(items)
    return [set(items[i:i + size]) for i in range(0, len(items), size)]
