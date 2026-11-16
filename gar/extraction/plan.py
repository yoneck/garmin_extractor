"""Training plan, gear, weather and strength-set extraction.

This module covers the "why" around activities:

- ``workouts`` / ``workout_steps`` — workout templates and their step structure,
- ``workout_adherence`` — scheduled workouts vs. what was actually done,
- ``gear`` / ``gear_usage`` — equipment and the activities that used it,
- ``weather`` — conditions during each activity,
- ``strength_sets`` — exercise sets, reps, weight and detected movement.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from gar.extraction.physiology import parse_ts

# Activity type keys that satisfy a planned sport.
SPORT_ALIASES: dict[str, set[str]] = {
    "cycling": {
        "cycling",
        "road_biking",
        "mountain_biking",
        "gravel_cycling",
        "indoor_cycling",
        "virtual_ride",
        "cyclocross",
    },
    "running": {"running", "trail_running", "treadmill_running", "indoor_running"},
    "strength_training": {"strength_training", "indoor_cardio"},
    "swimming": {"swimming", "lap_swimming", "open_water_swimming"},
    "walking": {"walking", "hiking"},
}


def _call(fetcher, client, dataset: str, method: str, *args, **kwargs):
    if fetcher is not None:
        return fetcher.call(dataset, method, *args, **kwargs)
    return getattr(client, method)(*args, **kwargs)


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


# ---------------------------------------------------------------------------
# Workout templates
# ---------------------------------------------------------------------------

def _step_rows(
    workout_id: str,
    steps: list,
    parent_order: int | None = None,
    counter: list[int] | None = None,
) -> list[dict]:
    if counter is None:
        counter = [0]
    rows: list[dict] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        order = step.get("stepOrder") or counter[0] + 1
        step_type = (step.get("stepType") or {}).get("stepTypeKey") or step.get("type")
        target = step.get("targetType") or {}
        end_condition = step.get("endCondition") or {}
        row = {
            "workoutId": workout_id,
            "stepIndex": counter[0],
            "stepOrder": order,
            "parentStepOrder": parent_order,
            "fragmentType": step.get("type"),
            "stepType": step_type,
            "description": step.get("description"),
            "endCondition": end_condition.get("conditionTypeKey"),
            "endConditionValue": _num(step.get("endConditionValue")),
            "targetType": target.get("workoutTargetTypeKey"),
            "targetValueLow": _num(step.get("targetValueOne")),
            "targetValueHigh": _num(step.get("targetValueTwo")),
            "zoneNumber": step.get("zoneNumber"),
            "numberOfIterations": step.get("numberOfIterations"),
            "category": step.get("category"),
            "exerciseName": step.get("exerciseName"),
        }
        rows.append(row)
        counter[0] += 1
        children = step.get("workoutSteps")
        if children:
            rows.extend(
                _step_rows(workout_id, children, parent_order=order, counter=counter)
            )
    return rows


def extract_workouts(client, storage, limit: int = 100, fetcher=None, details: bool = True) -> int:
    try:
        workouts = _call(fetcher, client, "workouts", "get_workouts", 0, limit)
    except Exception as error:
        print(f"  workouts error: {error}")
        return 0
    rows = []
    all_steps: list[dict] = []
    for workout in workouts or []:
        if not isinstance(workout, dict) or workout.get("workoutId") is None:
            continue
        workout_id = str(workout.get("workoutId"))
        sport = (workout.get("sportType") or {}).get("sportTypeKey")
        rows.append(
            {
                "workoutId": workout_id,
                "workoutName": workout.get("workoutName"),
                "description": workout.get("description"),
                "sport": sport,
                "trainingPlanId": workout.get("trainingPlanId"),
                "estimatedDurationSeconds": workout.get("estimatedDurationInSecs"),
                "estimatedDistanceMeters": workout.get("estimatedDistanceInMeters"),
                "createdDate": workout.get("createdDate"),
                "updatedDate": workout.get("updateDate") or workout.get("updatedDate"),
                "shared": workout.get("shared"),
            }
        )
        if details:
            try:
                payload = _call(
                    fetcher, client, "workout_details", "get_workout_by_id", workout_id
                )
                for segment in (payload or {}).get("workoutSegments") or []:
                    all_steps.extend(
                        _step_rows(workout_id, segment.get("workoutSteps") or [])
                    )
            except Exception as error:
                print(f"  workout {workout_id} details error: {error}")
    if rows:
        storage.sync_id("workouts", rows)
    if all_steps:
        storage.sync_id("workout_steps", all_steps)
    return len(rows)


# ---------------------------------------------------------------------------
# Scheduled workouts and adherence
# ---------------------------------------------------------------------------

def _activity_index(activities: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for activity in activities or []:
        if not isinstance(activity, dict):
            continue
        sport = (activity.get("activityType") or {}).get("typeKey") or activity.get(
            "sportType"
        )
        start = activity.get("startTimeLocal") or activity.get("startTimeGMT")
        when = str(start)[:10] if start else ""
        if not when:
            continue
        index.setdefault(when, []).append(
            {
                "activityId": str(activity.get("activityId")),
                "sport": (sport or "").lower(),
                "workoutId": str(activity.get("workoutId") or ""),
            }
        )
    return index


def _matches(planned_sport: str | None, activity: dict) -> bool:
    if not planned_sport:
        return True
    planned = planned_sport.lower()
    aliases = SPORT_ALIASES.get(planned, {planned})
    actual = activity.get("sport") or ""
    return actual in aliases or planned in actual or actual in planned


def _adherence_rows(
    year: int,
    month: int,
    payload: dict,
    activity_index: dict[str, list[dict]],
    today: str,
) -> list[dict]:
    rows = []
    for item in (payload or {}).get("calendarItems") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("itemType")
        if item_type not in ("workout", "fbtAdaptiveWorkout"):
            continue
        day = item.get("date")
        if not day:
            continue
        sport = item.get("sportTypeKey")
        candidates = activity_index.get(day, [])
        match = next((a for a in candidates if _matches(sport, a)), None)
        if match is not None:
            status = "completed"
        elif day >= today:
            status = "upcoming"
        else:
            status = "missed"
        workout_id = item.get("workoutId")
        rows.append(
            {
                "scheduledId": str(item.get("id")),
                "timestamp": parse_ts(day),
                "date": day,
                "year": year,
                "month": month,
                "itemType": item_type,
                "title": item.get("title"),
                "sport": sport,
                "workoutId": str(workout_id) if workout_id else None,
                "trainingPlanId": item.get("trainingPlanId"),
                "plannedDurationSeconds": item.get("duration"),
                "plannedDistanceMeters": item.get("distance"),
                "status": status,
                "matchedActivityId": match["activityId"] if match else None,
            }
        )
    return rows


def extract_adherence(
    client,
    storage,
    start_date: datetime,
    end_date: datetime,
    activities: list[dict] | None = None,
    fetcher=None,
) -> int:
    start = start_date.date() if isinstance(start_date, datetime) else start_date
    end = end_date.date() if isinstance(end_date, datetime) else end_date
    today = date.today().isoformat()
    if activities is None:
        try:
            activities = _call(
                fetcher,
                client,
                "activities",
                "get_activities_by_date",
                start.isoformat(),
                end.isoformat(),
            )
        except Exception as error:
            print(f"  adherence activities error: {error}")
            activities = []
    index = _activity_index(activities or [])

    rows: list[dict] = []
    for year, month in _months_between(start, end):
        try:
            payload = _call(
                fetcher,
                client,
                "scheduled_workouts",
                "get_scheduled_workouts",
                year,
                month,
            )
        except Exception as error:
            print(f"  scheduled workouts error {year}-{month:02d}: {error}")
            continue
        rows.extend(_adherence_rows(year, month, payload, index, today))
    if rows:
        storage.sync("workout_adherence", rows, allow_future=True)
    return len(rows)


# ---------------------------------------------------------------------------
# Gear
# ---------------------------------------------------------------------------

def extract_gear(
    client,
    storage,
    user_profile_pk: str | int,
    with_usage: bool = True,
    usage_limit: int = 1000,
    fetcher=None,
) -> int:
    try:
        payload = _call(fetcher, client, "gear", "get_gear", str(user_profile_pk))
    except Exception as error:
        print(f"  gear error: {error}")
        return 0
    entries = payload if isinstance(payload, list) else (payload or {}).get("gear") or []
    rows = []
    usage = []
    for gear in entries:
        if not isinstance(gear, dict):
            continue
        gear_id = gear.get("uuid") or gear.get("gearPk")
        if not gear_id:
            continue
        gear_id = str(gear_id)
        rows.append(
            {
                "gearId": gear_id,
                "gearPk": gear.get("gearPk"),
                "name": gear.get("displayName"),
                "type": gear.get("gearTypeName"),
                "status": gear.get("gearStatusName"),
                "make": gear.get("gearMakeName"),
                "model": gear.get("gearModelName"),
                "customMakeModel": gear.get("customMakeModel"),
                "maxMeters": _num(gear.get("maximumMeters")),
                "notified": gear.get("notified"),
                "dateBegin": gear.get("dateBegin"),
                "dateEnd": gear.get("dateEnd"),
            }
        )
        if with_usage:
            try:
                used = _call(
                    fetcher,
                    client,
                    "gear_usage",
                    "get_gear_activities",
                    gear_id,
                    usage_limit,
                )
            except Exception as error:
                print(f"  gear usage error for {gear_id}: {error}")
                continue
            for activity in used or []:
                if not isinstance(activity, dict) or not activity.get("activityId"):
                    continue
                usage.append(
                    {
                        "gearId": gear_id,
                        "activityId": str(activity.get("activityId")),
                        "activityName": activity.get("activityName"),
                        "sport": (activity.get("activityType") or {}).get("typeKey")
                        or activity.get("activityType"),
                        "startTime": activity.get("startTimeLocal")
                        or activity.get("startTimeGMT"),
                        "distanceMeters": _num(activity.get("distance")),
                    }
                )
    if rows:
        storage.sync_id("gear", rows)
    if usage:
        storage.sync_id("gear_usage", usage)
    return len(rows)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def _f_to_c(value) -> float | None:
    number = _num(value)
    if number is None:
        return None
    return round((number - 32.0) * 5.0 / 9.0, 1)


def _mph_to_kmh(value) -> float | None:
    number = _num(value)
    if number is None:
        return None
    return round(number * 1.609344, 1)


def extract_activity_weather(client, storage, activity_id: str, fetcher=None) -> bool:
    try:
        payload = _call(
            fetcher, client, "weather", "get_activity_weather", str(activity_id)
        )
    except Exception as error:
        print(f"  weather error for {activity_id}: {error}")
        return False
    if not isinstance(payload, dict) or not payload:
        return False
    station = payload.get("weatherStationDTO") or {}
    weather_type = payload.get("weatherTypeDTO") or {}
    record = {
        "activityId": str(activity_id),
        "timestamp": parse_ts(payload.get("issueDate")),
        "issueDate": payload.get("issueDate"),
        "tempF": _num(payload.get("temp")),
        "tempC": _f_to_c(payload.get("temp")),
        "apparentTempC": _f_to_c(payload.get("apparentTemp")),
        "dewPointC": _f_to_c(payload.get("dewPoint")),
        "humidityPct": _num(payload.get("relativeHumidity")),
        "windSpeedMph": _num(payload.get("windSpeed")),
        "windSpeedKmh": _mph_to_kmh(payload.get("windSpeed")),
        "windGustKmh": _mph_to_kmh(payload.get("windGust")),
        "windDirection": payload.get("windDirection"),
        "windCompass": payload.get("windDirectionCompassPoint"),
        "condition": weather_type.get("desc"),
        "stationId": station.get("id"),
        "stationName": station.get("name"),
        "latitude": _num(payload.get("latitude")),
        "longitude": _num(payload.get("longitude")),
    }
    storage.sync_id("weather", [record])
    return True


# ---------------------------------------------------------------------------
# Strength sets
# ---------------------------------------------------------------------------

def _primary_exercise(exercises: list) -> tuple[str | None, str | None, float | None]:
    best_category = None
    best_name = None
    best_probability = None
    for exercise in exercises or []:
        if not isinstance(exercise, dict):
            continue
        category = exercise.get("category")
        if not category or category == "UNKNOWN":
            continue
        probability = _num(exercise.get("probability"))
        if best_probability is None or (probability or 0) > best_probability:
            best_category = category
            best_name = exercise.get("name")
            best_probability = probability
    return best_category, best_name, best_probability


def extract_strength_sets(client, storage, activity_id: str, fetcher=None) -> int:
    try:
        payload = _call(
            fetcher,
            client,
            "strength_sets",
            "get_activity_exercise_sets",
            str(activity_id),
        )
    except Exception as error:
        print(f"  strength sets error for {activity_id}: {error}")
        return 0
    sets = (payload or {}).get("exerciseSets") or []
    rows = []
    for index, item in enumerate(sets):
        if not isinstance(item, dict):
            continue
        category, name, probability = _primary_exercise(item.get("exercises") or [])
        rows.append(
            {
                "activityId": str(activity_id),
                "setIndex": index,
                "timestamp": parse_ts(item.get("startTime")),
                "setType": item.get("setType"),
                "durationSeconds": _num(item.get("duration")),
                "repetitions": item.get("repetitionCount"),
                "weightKg": _num(item.get("weight")),
                "exerciseCategory": category,
                "exerciseName": name,
                "confidence": probability,
                "wktStepIndex": item.get("wktStepIndex"),
                "avgConcentricPower": _num(item.get("avgConcentricMeanPower")),
                "maxConcentricPower": _num(item.get("maxConcentricMeanPower")),
                "avgConcentricVelocity": _num(item.get("avgConcentricMeanVelocity")),
                "maxConcentricVelocity": _num(item.get("maxConcentricMeanVelocity")),
            }
        )
    if rows:
        storage.sync_id("strength_sets", rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def extract_plan(
    client,
    storage,
    start_date: datetime,
    end_date: datetime,
    user_profile_pk: str | int | None = None,
    activity_ids: list[str] | None = None,
    workout_limit: int = 100,
    fetcher=None,
) -> dict[str, int]:
    """Fetch plan-related data. Weather/strength need activity ids."""
    counts: dict[str, int] = {}
    counts["workouts"] = extract_workouts(
        client, storage, limit=workout_limit, fetcher=fetcher
    )
    counts["workout_adherence"] = extract_adherence(
        client, storage, start_date, end_date, fetcher=fetcher
    )

    if user_profile_pk is None:
        try:
            profile = _call(fetcher, client, "user_profile", "get_user_profile")
            user_profile_pk = (profile or {}).get("id") or (
                (profile or {}).get("userData") or {}
            ).get("userProfilePk")
        except Exception as error:
            print(f"  user profile error: {error}")
    if user_profile_pk:
        counts["gear"] = extract_gear(
            client, storage, user_profile_pk, fetcher=fetcher
        )

    weather = 0
    strength = 0
    for activity_id in activity_ids or []:
        if extract_activity_weather(client, storage, activity_id, fetcher=fetcher):
            weather += 1
        strength += extract_strength_sets(client, storage, activity_id, fetcher=fetcher)
    counts["weather"] = weather
    counts["strength_sets"] = strength
    print("  plan: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return counts
