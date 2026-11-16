"""Sleep extraction.

Fetches sleep data per day and stores:
- ``sleep_summary`` — one row per night with score, need, HR/HRV/SpO2/respiration,
- ``sleep_stages`` — deep/light/rem/awake stages from top-level ``sleepLevels``,
- ``sleep_samples`` — overnight HR, stress, HRV, SpO2 and movement samples.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# Garmin sleepLevels.activityLevel -> stage name (verified against dailySleepDTO)
_LEVEL_TO_STAGE = {0: "deep", 1: "light", 2: "rem", 3: "awake"}


def _parse_ts(value) -> int:
    if not value:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value < 10_000_000_000 else value // 1000
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        if text.isdigit():
            n = int(text)
            return n if n < 10_000_000_000 else n // 1000
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return 0
    return 0


def _days(start_date: datetime, end_date: datetime):
    """Inclusive, never beyond today (local date)."""
    today = date.today()
    current = start_date.date() if isinstance(start_date, datetime) else start_date
    last = end_date.date() if isinstance(end_date, datetime) else end_date
    if last > today:
        last = today
    while current <= last:
        yield current
        current += timedelta(days=1)


def _score_component(scores: dict, key: str) -> tuple[float | None, str | None]:
    value = (scores or {}).get(key) or {}
    if not isinstance(value, dict):
        return None, None
    return value.get("value"), value.get("qualifierKey")


def _stage_rows(sleep_id: str, calendar_date: str, levels: list) -> list[dict]:
    rows: list[dict] = []
    for index, stage in enumerate(levels or []):
        if not isinstance(stage, dict):
            continue
        start = _parse_ts(stage.get("startGMT") or stage.get("startTimeGMT"))
        end = _parse_ts(stage.get("endGMT") or stage.get("endTimeGMT"))
        if not start:
            continue
        level = stage.get("activityLevel")
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            level_int = -1
        duration = (end - start) if end and end > start else stage.get("durationSeconds")
        rows.append(
            {
                "timestamp": start,
                "calendarDate": calendar_date,
                "sleepId": sleep_id,
                "stageIndex": index,
                "stage": _LEVEL_TO_STAGE.get(level_int, "unknown"),
                "durationSeconds": duration,
            }
        )
    return rows


def _sample_rows(sleep_id: str, data: dict) -> list[dict]:
    rows: list[dict] = []

    def add(kind: str, items: list, ts_key: str, value_key: str, confidence_key: str | None = None):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            ts = _parse_ts(item.get(ts_key) or item.get("startGMT") or item.get("epochTimestamp"))
            if not ts:
                continue
            value = item.get(value_key)
            if value is None:
                continue
            rows.append(
                {
                    "timestamp": ts,
                    "sleepId": sleep_id,
                    "kind": kind,
                    "value": value,
                    "confidence": item.get(confidence_key) if confidence_key else None,
                }
            )

    add("hr", data.get("sleepHeartRate") or [], "startGMT", "value")
    add("stress", data.get("sleepStress") or [], "startGMT", "value")
    add("hrv", data.get("hrvData") or [], "startGMT", "value")
    add(
        "spo2",
        data.get("wellnessEpochSPO2DataDTOList") or [],
        "epochTimestamp",
        "spo2Reading",
        "readingConfidence",
    )
    add("movement", data.get("sleepMovement") or [], "startGMT", "activityLevel")
    return rows


def extract_sleep(client, storage, start_date: datetime, end_date: datetime):
    total = 0
    for day in _days(start_date, end_date):
        date_str = day.strftime("%Y-%m-%d")
        try:
            data = client.get_sleep_data(date_str) or {}
        except Exception as error:
            print(f"  error on {date_str}: {error}")
            continue
        dto = (data or {}).get("dailySleepDTO")
        if not isinstance(dto, dict) or not dto.get("calendarDate"):
            continue
        calendar_date = dto.get("calendarDate")
        sleep_id = str(dto.get("id") or calendar_date)
        timestamp = _parse_ts(dto.get("sleepStartTimestampGMT")) or _parse_ts(calendar_date)
        if not timestamp:
            continue

        scores = dto.get("sleepScores") or {}
        overall = scores.get("overall") if isinstance(scores, dict) else {}
        overall_value = overall.get("value") if isinstance(overall, dict) else None
        overall_qualifier = overall.get("qualifierKey") if isinstance(overall, dict) else None
        deep_pct, deep_q = _score_component(scores, "deepPercentage")
        rem_pct, rem_q = _score_component(scores, "remPercentage")
        light_pct, light_q = _score_component(scores, "lightPercentage")
        awake_count, awake_q = _score_component(scores, "awakeCount")
        restlessness, restless_q = _score_component(scores, "restlessness")
        stress_score, stress_q = _score_component(scores, "stress")

        spo2_summary = data.get("wellnessSpO2SleepSummaryDTO") or {}
        sleep_need = dto.get("sleepNeed") or {}
        next_need = dto.get("nextSleepNeed") or {}
        naps = dto.get("dailyNapDTOS") or []

        hrv_value = dto.get("avgSleepHRV") or data.get("avgOvernightHrv")
        if not hrv_value and isinstance(dto.get("hrvData"), dict):
            hrv_value = dto["hrvData"].get("avgOvernightHrv")

        summary = {
            "timestamp": timestamp,
            "calendarDate": calendar_date,
            "sleepId": sleep_id,
            "userPk": dto.get("userProfilePK"),
            "sleepTimeSeconds": dto.get("sleepTimeSeconds"),
            "napTimeSeconds": dto.get("napTimeSeconds"),
            "napCount": len(naps),
            "deepSleepSeconds": dto.get("deepSleepSeconds"),
            "lightSleepSeconds": dto.get("lightSleepSeconds"),
            "remSleepSeconds": dto.get("remSleepSeconds"),
            "awakeSleepSeconds": dto.get("awakeSleepSeconds"),
            "unmeasurableSleepSeconds": dto.get("unmeasurableSleepSeconds"),
            "sleepStartTimestampGMT": _parse_ts(dto.get("sleepStartTimestampGMT")),
            "sleepEndTimestampGMT": _parse_ts(dto.get("sleepEndTimestampGMT")),
            "avgHeartRate": dto.get("avgHeartRate"),
            "avgSleepHRV": hrv_value,
            "hrvStatus": data.get("hrvStatus") or dto.get("hrvStatus"),
            "avgSpO2": dto.get("averageSpO2Value")
            or spo2_summary.get("averageSPO2")
            or dto.get("averageSpO2HRSleep"),
            "lowestSpO2": dto.get("lowestSpO2Value") or spo2_summary.get("lowestSPO2"),
            "highestSpO2": dto.get("highestSpO2Value"),
            "avgRespiration": dto.get("averageRespirationValue"),
            "lowestRespiration": dto.get("lowestRespirationValue"),
            "highestRespiration": dto.get("highestRespirationValue"),
            "avgSleepStress": dto.get("avgSleepStress"),
            "awakeCount": dto.get("awakeCount"),
            "restlessMoments": data.get("restlessMomentsCount"),
            "overallSleepScore": overall_value,
            "sleepScoreQualifier": overall_qualifier,
            "deepPct": deep_pct,
            "deepQualifier": deep_q,
            "remPct": rem_pct,
            "remQualifier": rem_q,
            "lightPct": light_pct,
            "lightQualifier": light_q,
            "awakeCountScore": awake_count,
            "awakeCountQualifier": awake_q,
            "restlessnessScore": restlessness,
            "restlessnessQualifier": restless_q,
            "stressScore": stress_score,
            "stressScoreQualifier": stress_q,
            "sleepNeedBaselineMinutes": sleep_need.get("baseline"),
            "sleepNeedActualMinutes": sleep_need.get("actual"),
            "sleepNeedFeedback": sleep_need.get("feedback"),
            "nextSleepNeedActualMinutes": next_need.get("actual"),
            "sleepScoreFeedback": dto.get("sleepScoreFeedback"),
            "sleepScoreInsight": dto.get("sleepScoreInsight"),
            "skinTempDeviationC": data.get("avgSkinTempDeviationC"),
            "restingHeartRate": data.get("restingHeartRate"),
            "bodyBatteryChange": data.get("bodyBatteryChange"),
        }
        storage.sync("sleep_summary", [summary])

        levels = data.get("sleepLevels")
        if not levels:
            legacy = dto.get("calendarDataArray")
            levels = legacy.get("stages") if isinstance(legacy, dict) else legacy
        stage_rows = _stage_rows(sleep_id, calendar_date, levels or [])
        if stage_rows:
            storage.sync("sleep_stages", stage_rows)

        sample_rows = _sample_rows(sleep_id, data)
        if sample_rows:
            storage.sync("sleep_samples", sample_rows)

        total += 1
    print(f"  written {total} sleep summaries.")
