# Extraction API Shapes — Confirmed via Live Probe (2026-09-15)

Verified against live `connect()` calls. Use as source of truth for extraction bugs.

## Session / auth
- `login.connect(cfg)` reuses `garmin_session.json`.
- Must set `PYTHONPATH=/mnt/c/data/opencode/gar/gar_v2` (venv activate alone is insufficient).

## HRV — get_hrv_data(cdate: str) -> dict
- Key for per-day data is `hrvReadings` (NOT `hrvSummary`, which is often null).
- On days **with** a summary, `hrvReadings` is a list of 74–98 records:
  - `{hrvValue: int, readingTimeGMT: str, readingTimeLocal: str}` e.g. `543`, `2026-09-09T22:55:16.0`.
- On days without a summary, `hrvReadings` is empty → return value `[]` / `{}`.
- `hrvMean` is often absent; use `hrvValue` for each reading.
- `hrvSummary` (if present): `{calendarDate, weeklyAvg, lastNightAvg, lastNight5MinHigh, status, feedbackPhrase}`.

## Stress — get_stress_data(cdate) -> dict
- Key is `stressValuesArray`.
- Shape is a **flat list of `[ts_ms, value]` pairs** (e.g. `[[1789336800000, -1], [1789336980000, -1]]`); NOT dicts. Values are `-1` (no real stress data today).

## Body Battery — get_body_battery(start_date, end_date) -> list
- Returns **list**; one entry per day.
- Keys: `date`, `day`, `charged`, `drained`, `bodyBatteryValuesArray`, `startTimestampGMT` / `startTimestampLocal`.
- `bodyBatteryValuesArray` is a flat `[ts, value]` list of samples; `charged` and `drained` are scalars.
- For a single day use the first entry — read `charged`, `drained`, and samples.

## Body Composition — get_body_composition(start, end) -> dict
- Returns `dateWeightList` (list), not `bodyCompositions`.
- One row per entry (11 rows today: body_composition not currently written).
- Numeric fields are in **grams / small units**:
  - `weight`: 87230 (this is **grams** → divide by 1000 for kg).
  - `boneMass`: 4929, `muscleMass`: 34430 (grams).
- Other field names (actual API): `bodyFat` (percent), `bodyWater` (percent), `bmi`.
- Missing/null: `physiqueRating`, `visceralFat`, `metabolicAge` in today's data.

## Sleep — get_sleep_data(cdate) -> dict
- `dailySleepDTO` is **a single dict per day**; the extractor reads it as `sleep_summary`.
  - Fields: `sleepStartTimestampGMT`/`sleepEndTimestampGMT` (ms), `deepSleepSeconds`, `remSleepSeconds`, `lightSleepSeconds`, `awakeSleepSeconds`, `avgSleepHRV`, `avgSpO2`, sleep scores under `dailySleepDTO.sleepScores` etc.
- Stages come from the `calendarDataArray` block under `dailySleepDTO` and become `sleep_stages` rows (`timestamp`, `sleepStage`, `durationSeconds`).
- `avgSleepHRV`/`avgSpO2` may be `null` on some days — store as-is, do not crash.

## Notes on other tables
- Timestamps in storage are seconds; ms epoch values from the API are divided by 1000.
- Activity depth (`--activity-depth`) downloads FIT files and per-activity laps/splits/power/HR via activity API endpoints in `gar/extraction/activities_deep.py`.
- `get_activity_hr_in_timezones(activity_id)` expects an **int** id.

## Activities — get_activities(...) -> list of dicts
- Each entry: `activityId` (int), `activityName`, `distance` (meters), `duration` (e.g. step=2654.1 sec), `elevationGain`, etc.
  - Example: `{"activityId": 24360640563, "distance": 2332.09, "duration": 2654.1}`.
- `startTimeGMT`: `2026-09-13 16:59:18` (string; parse with strptime "%Y-%m-%d %H:%M:%S").
- **`get_activity_hr_in_timezones(activity_id)`**: accept `int`, not `str`. Today this failed:
  - `TypeError: strptime argument activity_id must be str, not int`. So convert `str()` the id.
- Depth data: `get_activity_hr_in_timezones`, `get_activity_power_in_timezones`, etc.
  - All keyed by `activity_id`.

## FIT / Archives
- `_download_fit()`: `fitx` (per activity) is an **867-byte zip archive** (not a single file).
- Reading a single archive: `TypeError: __init__ expected length 64, got 140` when passing wrong arg.
- Process: zip per activity → `FITX` contains `DataMessage` objects with `.name`, `.data`, and an index.
- `get_fit_data(activity_id)` should read from the archived zip; raw `.fit`/`.zip` from `_download_fit()` are not yet usable.

## Extraction module — physiology.py (all fixed)
- `extract_body_composition`: reads `dateWeightList`, converts grams → kg.
- `extract_hrv`: uses `hrvValue` + `readingTimeGMT`; skips days with empty readings.
- `extract_stress`: `stressValuesArray` is a flat `[ts, val]` list; skips `-1` no-data values and initializes its buffer correctly.
- `extract_body_battery`: iterates the body battery list, reads `bodyBatteryValuesArray`, writes `charged`/`drained`/mean.

## Body composition — data availability
Body composition is only available on a **sparse** set of dates (roughly one
entry per weekly weigh-in; e.g. 29 days May–Sep 2026, many empty between).
A run that lands entirely in a gap legitimately writes 0 rows — this is not a
bug. Use `--days 90 --force` to cover the full history.

## Storage / pipeline
- `storage.sync(kind, rows)` writes one CSV per day under `garmin_data/{kind}/` (keyed by `date`/`timestamp`).
- `garmin_data/activity_hr/2026_09.csv` exists (800 lines) — 99.9% empty (`timestamp`=0); data extraction is the blocker.
- Tables present: activities, activity_hr, activity_laps, activity_powers, activity_power_metrics, activity_splits, hrv, stress.
- Tables missing/empty: body_composition (11 rows available but unextracted), body_composition raw, sleep_summary (empty), body_battery (empty timestamps).
- Pipeline runs in `gar_v2/../scripts/monitor/`: `python main.py {YYYY-MM-DD}` — no separate process for `sleep_summary`.
