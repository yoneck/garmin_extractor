"""Sleep extraction.

Fetches sleep data per day and stores the daily summary, the stages, and the
per-minute arrays when present.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _parse_ts(value) -> int:
    if not value:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value < 10_000_000_000 else value // 1000
    if isinstance(value, (datetime,)):
        return int(value.timestamp())
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        if text.isdigit():
            n = int(text)
            return n if n < 10_000_000_000 else n // 1000
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return 0
    return 0


def _days(start_date: datetime, end_date: datetime):
    offset = 0
    max_days = (end_date - start_date).days + 2
    while offset <= max_days:
        yield start_date + timedelta(days=offset)
        offset += 1


def _fetch_stage_rows(stages_block) -> list:
    if isinstance(stages_block, dict):
        return stages_block.get("stages") or []
    if isinstance(stages_block, list):
        return stages_block
    return []


def extract_sleep(client, storage, start_date: datetime, end_date: datetime):
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    total = 0
    for date_str in (start_date + timedelta(days=i) for i in range(0, (end_date - start_date).days + 2)):
        try:
            data = client.get_sleep_data(date_str.strftime("%Y-%m-%d")) or {}
        except Exception as error:
            print(f"  error on {date_str.strftime('%Y-%m-%d')}: {error}")
            continue
        dto = (data or {}).get("dailySleepDTO")
        if not isinstance(dto, dict) or not dto.get("calendarDate"):
            continue
        timestamp = _parse_ts(dto.get("sleepStartTimestampGMT")) or _parse_ts(
            dto.get("calendarDate")
        )
        if not timestamp:
            continue

        summary = {
            "timestamp": timestamp,
            "calendarDate": dto.get("calendarDate"),
            "userPk": dto.get("userProfilePK"),
            "sleepTimeSeconds": dto.get("sleepTimeSeconds"),
            "napTimeSeconds": dto.get("napTimeSeconds"),
            "deepSleepSeconds": dto.get("deepSleepSeconds"),
            "lightSleepSeconds": dto.get("lightSleepSeconds"),
            "remSleepSeconds": dto.get("remSleepSeconds"),
            "awakeSleepSeconds": dto.get("awakeSleepSeconds"),
            "sleepStartTimestampGMT": _parse_ts(dto.get("sleepStartTimestampGMT")),
            "sleepEndTimestampGMT": _parse_ts(dto.get("sleepEndTimestampGMT")),
            "avgSleepHRV": dto.get("avgSleepHRV"),
            "avgSpO2": dto.get("avgSpO2"),
            "avgRespiration": dto.get("avgRespirationValue"),
            "lowestRespiration": dto.get("lowestRespirationValue"),
            "highestRespiration": dto.get("highestRespirationValue"),
            "overallSleepScore": None,
        }
        scores = dto.get("sleepScores") or {}
        overall = scores.get("overall")
        if isinstance(overall, dict):
            summary["overallSleepScore"] = overall.get("value")
        storage.sync("sleep_summary", [summary])

        stages_block = dto.get("calendarDataArray")
        for stage in _fetch_stage_rows(stages_block):
            if not isinstance(stage, dict):
                continue
            storage.sync(
                "sleep_stages",
                [
                    {
                        "timestamp": _parse_ts(
                            stage.get("startGMT")
                            or stage.get("startTimeGMT")
                            or stage.get("start_local")
                        ),
                        "calendarDate": dto.get("calendarDate"),
                        "type": stage.get("type"),
                        "durationSeconds": stage.get("durationSeconds")
                        or (stage.get("durationMs", 0) / 1000),
                    }
                ],
            )
        total += 1
    print(f"  written {total} sleep summaries.")
