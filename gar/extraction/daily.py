"""Daily wellness, readiness and training-status extraction."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from gar.extraction.physiology import parse_ts


def _call(fetcher, client, dataset: str, method: str, *args):
    if fetcher is not None:
        return fetcher.call(dataset, method, *args)
    return getattr(client, method)(*args)


def _days(start_date: datetime, end_date: datetime):
    today = date.today()
    current = start_date.date() if isinstance(start_date, datetime) else start_date
    last = end_date.date() if isinstance(end_date, datetime) else end_date
    if last > today:
        last = today
    while current <= last:
        yield current
        current += timedelta(days=1)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flatten_metrics(rhr: dict) -> dict:
    metrics = ((rhr or {}).get("allMetrics") or {}).get("metricsMap") or {}
    values = metrics.get("WELLNESS_RESTING_HEART_RATE") or []
    if values and isinstance(values[0], dict):
        return {"restingHeartRate": values[0].get("value")}
    return {}


def _device_rows(date_str: str, timestamp: int, status: dict) -> list[dict]:
    rows: list[dict] = []
    status_data = ((status or {}).get("mostRecentTrainingStatus") or {}).get(
        "latestTrainingStatusData"
    ) or {}
    balance_map = ((status or {}).get("mostRecentTrainingLoadBalance") or {}).get(
        "metricsTrainingLoadBalanceDTOMap"
    ) or {}
    vo2max = (status or {}).get("mostRecentVO2Max") or {}

    for device_id, data in status_data.items():
        if not isinstance(data, dict):
            continue
        acute = data.get("acuteTrainingLoadDTO") or {}
        balance = balance_map.get(str(device_id)) or {}
        rows.append(
            {
                "timestamp": timestamp,
                "date": date_str,
                "deviceId": device_id,
                "trainingStatus": data.get("trainingStatus"),
                "statusFeedback": data.get("trainingStatusFeedbackPhrase"),
                "fitnessTrend": data.get("fitnessTrend"),
                "fitnessTrendSport": data.get("fitnessTrendSport"),
                "weeklyLoad": data.get("weeklyTrainingLoad"),
                "acuteLoad7d": acute.get("dailyTrainingLoadAcute"),
                "chronicLoad28d": acute.get("dailyTrainingLoadChronic"),
                "acwrPct": acute.get("acwrPercent"),
                "acwrStatus": acute.get("acwrStatus"),
                "loadBalanceFeedback": balance.get("trainingBalanceFeedbackPhrase"),
                "loadAerobicLow": balance.get("monthlyLoadAerobicLow"),
                "loadAerobicHigh": balance.get("monthlyLoadAerobicHigh"),
                "loadAnaerobic": balance.get("monthlyLoadAnaerobic"),
                "loadTargetMin": balance.get("monthlyLoadAerobicLowTargetMin"),
                "loadTargetMax": balance.get("monthlyLoadAerobicHighTargetMax"),
                "vo2maxGeneric": (vo2max.get("generic") or {}).get("vo2MaxValue"),
                "vo2maxCycling": (vo2max.get("cycling") or {}).get("vo2MaxValue"),
            }
        )
    return rows


def _max_metrics_rows(date_str: str, timestamp: int, payload) -> list[dict]:
    if isinstance(payload, list):
        entries = payload
    else:
        entries = [payload] if isinstance(payload, dict) else []
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        generic = entry.get("generic") or {}
        cycling = entry.get("cycling") or {}
        acclimation = entry.get("heatAltitudeAcclimation") or {}
        rows.append(
            {
                "timestamp": timestamp,
                "date": date_str,
                "vo2maxRunning": generic.get("vo2MaxValue"),
                "vo2maxRunningPrecise": generic.get("vo2MaxPreciseValue"),
                "fitnessAge": generic.get("fitnessAge"),
                "vo2maxCycling": cycling.get("vo2MaxValue"),
                "heatAcclimationPct": acclimation.get("heatAcclimationPercentage"),
                "altitudeAcclimation": acclimation.get("altitudeAcclimation"),
                "currentAltitude": acclimation.get("currentAltitude"),
            }
        )
    return rows


def extract_steps(
    client,
    storage,
    start_date: datetime,
    end_date: datetime,
    fetcher=None,
) -> int:
    """Lightweight daily step/distance backfill in one range request."""
    start = start_date.strftime("%Y-%m-%d")
    end = min(end_date, datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    try:
        payload = _call(fetcher, client, "steps_daily", "get_daily_steps", start, end)
    except Exception as error:
        print(f"  steps error: {error}")
        return 0
    rows = []
    for entry in payload or []:
        if not isinstance(entry, dict):
            continue
        when = entry.get("calendarDate")
        if not when:
            continue
        rows.append(
            {
                "timestamp": parse_ts(when),
                "date": when,
                "totalSteps": entry.get("totalSteps"),
                "totalDistanceMeters": entry.get("totalDistance"),
                "stepGoal": entry.get("stepGoal"),
            }
        )
    if rows:
        storage.sync("steps_daily", rows)
    return len(rows)


def extract_daily(client, storage, start_date: datetime, end_date: datetime, fetcher=None):
    days = 0
    for day in _days(start_date, end_date):
        date_str = day.strftime("%Y-%m-%d")
        timestamp = parse_ts(date_str)

        summary = {}
        try:
            summary = _call(fetcher, client, "daily_summary", "get_user_summary", date_str) or {}
        except Exception as error:
            print(f"  daily summary error on {date_str}: {error}")

        readiness_payload = []
        try:
            readiness_payload = _call(
                fetcher, client, "readiness", "get_training_readiness", date_str
            ) or []
        except Exception as error:
            print(f"  readiness error on {date_str}: {error}")

        status = {}
        try:
            status = _call(
                fetcher, client, "training_status", "get_training_status", date_str
            ) or {}
        except Exception as error:
            print(f"  training status error on {date_str}: {error}")

        rhr = {}
        try:
            rhr = _call(fetcher, client, "rhr", "get_rhr_day", date_str) or {}
        except Exception as error:
            print(f"  rhr error on {date_str}: {error}")

        max_metrics = []
        try:
            max_metrics = _call(
                fetcher, client, "max_metrics", "get_max_metrics", date_str
            ) or []
        except Exception as error:
            print(f"  max metrics error on {date_str}: {error}")

        readiness = readiness_payload[0] if isinstance(readiness_payload, list) and readiness_payload else {}
        device_rows = _device_rows(date_str, timestamp, status)
        primary = device_rows[0] if device_rows else {}
        rhr_fields = _flatten_metrics(rhr)
        max_row = (_max_metrics_rows(date_str, timestamp, max_metrics) or [{}])[0]

        record = {
            "timestamp": timestamp,
            "date": date_str,
            "restingHeartRate": summary.get("restingHeartRate")
            or rhr_fields.get("restingHeartRate"),
            "lastSevenDaysAvgRestingHeartRate": summary.get(
                "lastSevenDaysAvgRestingHeartRate"
            ),
            "totalSteps": summary.get("totalSteps"),
            "totalDistanceMeters": summary.get("totalDistanceMeters"),
            "floorsAscended": summary.get("floorsAscended"),
            "activeKilocalories": summary.get("activeKilocalories"),
            "totalKilocalories": summary.get("totalKilocalories"),
            "moderateIntensityMinutes": summary.get("moderateIntensityMinutes"),
            "vigorousIntensityMinutes": summary.get("vigorousIntensityMinutes"),
            "averageStressLevel": summary.get("averageStressLevel"),
            "maxStressLevel": summary.get("maxStressLevel"),
            "stressQualifier": summary.get("stressQualifier"),
            "bodyBatteryAtWakeTime": summary.get("bodyBatteryAtWakeTime"),
            "bodyBatteryChargedValue": summary.get("bodyBatteryChargedValue"),
            "bodyBatteryDrainedValue": summary.get("bodyBatteryDrainedValue"),
            "bodyBatteryHighestValue": summary.get("bodyBatteryHighestValue"),
            "bodyBatteryLowestValue": summary.get("bodyBatteryLowestValue"),
            "averageSpo2": summary.get("averageSpo2"),
            "lowestSpo2": summary.get("lowestSpo2"),
            "avgWakingRespirationValue": summary.get("avgWakingRespirationValue"),
            "lowestRespirationValue": summary.get("lowestRespirationValue"),
            "highestRespirationValue": summary.get("highestRespirationValue"),
            "sleepingSeconds": summary.get("sleepingSeconds"),
            "sedentarySeconds": summary.get("sedentarySeconds"),
            "highlyActiveSeconds": summary.get("highlyActiveSeconds"),
            "readinessScore": readiness.get("score"),
            "readinessLevel": readiness.get("level"),
            "recoveryTimeMinutes": readiness.get("recoveryTime"),
            "trainingStatus": primary.get("trainingStatus"),
            "trainingStatusFeedback": primary.get("statusFeedback"),
            "acuteLoad7d": primary.get("acuteLoad7d"),
            "chronicLoad28d": primary.get("chronicLoad28d"),
            "acwrPct": primary.get("acwrPct"),
            "acwrStatus": primary.get("acwrStatus"),
            "loadBalanceFeedback": primary.get("loadBalanceFeedback"),
            "vo2maxRunning": max_row.get("vo2maxRunning")
            or primary.get("vo2maxGeneric"),
            "vo2maxCycling": max_row.get("vo2maxCycling")
            or primary.get("vo2maxCycling"),
            "fitnessAge": max_row.get("fitnessAge"),
        }
        if any(value is not None for key, value in record.items() if key not in ("timestamp", "date")):
            storage.sync("daily_summary", [record])

        if readiness:
            storage.sync(
                "readiness",
                [
                    {
                        "timestamp": timestamp,
                        "date": date_str,
                        "score": readiness.get("score"),
                        "level": readiness.get("level"),
                        "feedbackShort": readiness.get("feedbackShort"),
                        "feedbackLong": readiness.get("feedbackLong"),
                        "recoveryTimeMinutes": readiness.get("recoveryTime"),
                        "recoveryTimeFactorPct": readiness.get("recoveryTimeFactorPercent"),
                        "hrvFactorPct": readiness.get("hrvFactorPercent"),
                        "sleepScoreFactorPct": readiness.get("sleepScoreFactorPercent"),
                        "stressFactorPct": readiness.get("stressHistoryFactorPercent"),
                        "acwrFactorPct": readiness.get("acwrFactorPercent"),
                        "sleepScore": readiness.get("sleepScore"),
                        "hrvWeeklyAverage": readiness.get("hrvWeeklyAverage"),
                        "validSleep": readiness.get("validSleep"),
                    }
                ],
            )

        if device_rows:
            storage.sync("training_status", device_rows)
        if max_metrics:
            rows = _max_metrics_rows(date_str, timestamp, max_metrics)
            if rows:
                storage.sync("max_metrics", rows)
        if rhr_fields:
            storage.sync(
                "rhr",
                [
                    {
                        "timestamp": timestamp,
                        "date": date_str,
                        "restingHeartRate": rhr_fields.get("restingHeartRate"),
                    }
                ],
            )
        days += 1
    print(f"  written daily wellness for {days} days.")
