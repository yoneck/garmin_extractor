"""Performance metrics: thresholds, zones, records, race predictions and scores.

All Garmin endpoints used here are account-level (not activity-level):

===============  =========================================  ====================
Table            Endpoint                                   Natural key
===============  =========================================  ====================
thresholds       FTP, lactate threshold, LTHR               date + kind + sport
zone_definitions power/HR zone floors per sport              date + sport + zone
personal_records get_personal_record                       recordId
race_predictions get_race_predictions                       date + distance
scores           fitness age, endurance and hill scores      date + scoreType
===============  =========================================  ====================
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from gar.extraction.physiology import parse_ts

_RACE_DISTANCES = {
    "time5K": ("5K", 5.0),
    "time10K": ("10K", 10.0),
    "timeHalfMarathon": ("HM", 21.0975),
    "timeMarathon": ("M", 42.195),
}


def _call(fetcher, client, dataset: str, method: str, *args):
    if fetcher is not None:
        return fetcher.call(dataset, method, *args)
    return getattr(client, method)(*args)


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

def _ftp_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        entries = [entry for entry in payload if isinstance(entry, dict)]
    elif isinstance(payload, dict):
        entries = [payload]
    else:
        entries = []
    rows = []
    for entry in entries:
        power = _num(entry.get("functionalThresholdPower"))
        if power is None:
            continue
        when = entry.get("calendarDate") or entry.get("ftpCreateTime")
        rows.append(
            {
                "timestamp": parse_ts(when),
                "date": str(when)[:10],
                "kind": "ftp",
                "sport": entry.get("sport") or "CYCLING",
                "value": power,
                "unit": "W",
                "isStale": entry.get("isStale"),
                "sourceType": entry.get("biometricSourceType"),
            }
        )
    return rows


def _lactate_rows(payload: dict) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(payload, dict):
        return rows
    speed = payload.get("speed_and_heart_rate") or {}
    if speed:
        when = speed.get("calendarDate")
        rows.append(
            {
                "timestamp": parse_ts(when),
                "date": str(when)[:10] if when else None,
                "kind": "lactate_threshold_pace",
                "sport": "RUNNING",
                "value": _num(speed.get("speed")),
                "valueSecondary": _num(speed.get("heartRate")),
                "unit": "m/s",
                "isStale": speed.get("isStale"),
            }
        )
        rows.append(
            {
                "timestamp": parse_ts(speed.get("calendarDate")),
                "date": str(when)[:10] if when else None,
                "kind": "lactate_threshold_hr",
                "sport": "RUNNING",
                "value": _num(speed.get("heartRate")),
                "valueSecondary": _num(speed.get("heartRateCycling")),
                "unit": "bpm",
                "isStale": speed.get("isStale"),
            }
        )
    power = payload.get("power") or {}
    if power:
        when = power.get("calendarDate")
        rows.append(
            {
                "timestamp": parse_ts(when),
                "date": str(when)[:10] if when else None,
                "kind": "running_ftp",
                "sport": power.get("sport") or "RUNNING",
                "value": _num(power.get("functionalThresholdPower")),
                "valueSecondary": _num(power.get("powerToWeight")),
                "unit": "W",
                "isStale": power.get("isStale"),
            }
        )
    return [row for row in rows if row.get("value") is not None]


def extract_thresholds(client, storage, when: str | None = None, fetcher=None) -> int:
    when = when or _today_str()
    rows: list[dict] = []
    try:
        rows += _ftp_rows(_call(fetcher, client, "ftp", "get_cycling_ftp"))
    except Exception as error:
        print(f"  ftp error: {error}")
    try:
        rows += _lactate_rows(_call(fetcher, client, "lactate", "get_lactate_threshold"))
    except Exception as error:
        print(f"  lactate threshold error: {error}")
    rows = [row for row in rows if row.get("date")]
    if rows:
        storage.sync_id("thresholds", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Zone definitions
# ---------------------------------------------------------------------------

def _zone_rows(payload, sport_key: str, zone_type: str, when: str) -> list[dict]:
    entries = payload if isinstance(payload, list) else [payload]
    rows: list[dict] = []
    timestamp = parse_ts(when)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sport = entry.get(sport_key) or "DEFAULT"
        floors: list[tuple[int, float]] = []
        for zone in range(1, 8):
            lower = _num(entry.get(f"zone{zone}Floor"))
            if lower is None:
                continue
            floors.append((zone, lower))
        for index, (zone, lower) in enumerate(floors):
            upper = floors[index + 1][1] - 1 if index + 1 < len(floors) else None
            rows.append(
                {
                    "timestamp": timestamp,
                    "date": when,
                    "sport": sport,
                    "zoneType": zone_type,
                    "zone": zone,
                    "lowerBound": lower,
                    "upperBound": upper,
                    "ftp": _num(entry.get("functionalThresholdPower")),
                    "lthr": _num(entry.get("lactateThresholdHeartRateUsed")),
                    "maxHr": _num(entry.get("maxHeartRateUsed")),
                    "method": entry.get("trainingMethod"),
                }
            )
    return rows


def extract_zone_definitions(client, storage, when: str | None = None, fetcher=None) -> int:
    when = when or _today_str()
    rows: list[dict] = []
    try:
        rows += _zone_rows(
            _call(fetcher, client, "power_zones", "get_power_zones"),
            sport_key="sport",
            zone_type="power",
            when=when,
        )
    except Exception as error:
        print(f"  power zones error: {error}")
    try:
        rows += _zone_rows(
            _call(fetcher, client, "heart_rate_zones", "get_heart_rate_zones"),
            sport_key="sport",
            zone_type="hr",
            when=when,
        )
    except Exception as error:
        print(f"  heart rate zones error: {error}")
    if rows:
        storage.sync_id("zone_definitions", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Personal records
# ---------------------------------------------------------------------------

def extract_personal_records(client, storage, fetcher=None) -> int:
    try:
        payload = _call(fetcher, client, "personal_records", "get_personal_record")
    except Exception as error:
        print(f"  personal records error: {error}")
        return 0
    entries = payload if isinstance(payload, list) else []
    rows = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        rows.append(
            {
                "recordId": entry.get("id"),
                "typeId": entry.get("typeId"),
                "status": entry.get("status"),
                "activityId": entry.get("activityId"),
                "activityName": entry.get("activityName"),
                "activityType": entry.get("activityType"),
                "sport": entry.get("activityType"),
                "value": _num(entry.get("value")),
                "achievedAt": parse_ts(entry.get("prStartTimeGmt")),
                "activityStart": parse_ts(entry.get("activityStartDateTimeInGMT")),
            }
        )
    if rows:
        storage.sync_id("personal_records", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Race predictions
# ---------------------------------------------------------------------------

def extract_race_predictions(
    client,
    storage,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    fetcher=None,
) -> int:
    start = (start_date or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    end = (end_date or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    try:
        payload = _call(
            fetcher,
            client,
            "race_predictions",
            "get_race_predictions",
            start,
            end,
        )
    except TypeError:
        payload = _call(fetcher, client, "race_predictions", "get_race_predictions")
    except Exception as error:
        print(f"  race predictions error: {error}")
        return 0
    if isinstance(payload, list):
        entries = [entry for entry in payload if isinstance(entry, dict)]
    elif isinstance(payload, dict):
        entries = [payload]
    else:
        entries = []
    rows = []
    for entry in entries:
        when = entry.get("calendarDate") or end
        timestamp = parse_ts(when)
        for field, (label, km) in _RACE_DISTANCES.items():
            seconds = _num(entry.get(field))
            if not seconds:
                continue
            rows.append(
                {
                    "timestamp": timestamp,
                    "date": str(when)[:10],
                    "distance": label,
                    "distanceKm": km,
                    "predictedSeconds": seconds,
                    "predictedPaceSecPerKm": round(seconds / km, 1),
                    "predictedSpeedKmh": round(3600 / (seconds / km), 2),
                }
            )
    if rows:
        storage.sync("race_predictions", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Scores: fitness age, endurance score, hill score
# ---------------------------------------------------------------------------

def _fitness_age_rows(payload: dict, when: str) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    fitness_age = _num(payload.get("fitnessAge"))
    if fitness_age is None:
        return []
    timestamp = parse_ts(when)
    return [
        {
            "timestamp": timestamp,
            "date": when,
            "scoreType": "fitness_age",
            "value": fitness_age,
            "valueSecondary": _num(payload.get("achievableFitnessAge")),
            "chronologicalAge": _num(payload.get("chronologicalAge")),
            "updatedAt": payload.get("lastUpdated"),
        }
    ]


def _endurance_rows(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = []
    for when, group in (payload.get("groupMap") or {}).items():
        if not isinstance(group, dict):
            continue
        timestamp = parse_ts(when)
        if not timestamp:
            continue
        rows.append(
            {
                "timestamp": timestamp,
                "date": str(when)[:10],
                "scoreType": "endurance_score",
                "value": _num(group.get("groupMax")),
                "valueSecondary": _num(group.get("groupAverage")),
                "periodAvg": _num(payload.get("avg")),
                "periodMax": _num(payload.get("max")),
            }
        )
    return rows


def _hill_rows(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    rows = []
    for entry in payload.get("hillScoreDTOList") or []:
        if not isinstance(entry, dict):
            continue
        when = entry.get("calendarDate")
        timestamp = parse_ts(when)
        if not timestamp:
            continue
        rows.append(
            {
                "timestamp": timestamp,
                "date": str(when)[:10],
                "scoreType": "hill_score",
                "value": _num(entry.get("overallScore")),
                "valueSecondary": _num(entry.get("strengthScore")),
                "enduranceComponent": _num(entry.get("enduranceScore")),
                "classificationId": entry.get("hillScoreClassificationId"),
                "deviceId": entry.get("deviceId"),
                "periodMax": _num(payload.get("maxScore")),
            }
        )
    return rows


def extract_scores(
    client,
    storage,
    start_date: datetime,
    end_date: datetime,
    fetcher=None,
) -> int:
    start = start_date.strftime("%Y-%m-%d")
    end = min(end_date, datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    rows: list[dict] = []
    try:
        rows += _fitness_age_rows(
            _call(fetcher, client, "fitness_age", "get_fitnessage_data", end), end
        )
    except Exception as error:
        print(f"  fitness age error: {error}")
    try:
        rows += _endurance_rows(
            _call(fetcher, client, "endurance_score", "get_endurance_score", start, end)
        )
    except Exception as error:
        print(f"  endurance score error: {error}")
    try:
        rows += _hill_rows(
            _call(fetcher, client, "hill_score", "get_hill_score", start, end)
        )
    except Exception as error:
        print(f"  hill score error: {error}")
    if rows:
        storage.sync("scores", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def extract_performance(
    client,
    storage,
    start_date: datetime,
    end_date: datetime,
    fetcher=None,
    today: datetime | None = None,
) -> dict[str, int]:
    """Fetch all performance tables. Returns per-table row counts."""
    today = today or datetime.now(timezone.utc)
    when = today.strftime("%Y-%m-%d")
    counts = {
        "thresholds": extract_thresholds(client, storage, when, fetcher=fetcher),
        "zone_definitions": extract_zone_definitions(
            client, storage, when, fetcher=fetcher
        ),
        "personal_records": extract_personal_records(client, storage, fetcher=fetcher),
        "race_predictions": extract_race_predictions(
            client, storage, start_date, end_date, fetcher=fetcher
        ),
        "scores": extract_scores(client, storage, start_date, end_date, fetcher=fetcher),
    }
    print(
        "  performance: "
        + ", ".join(f"{name}={count}" for name, count in counts.items())
    )
    return counts
