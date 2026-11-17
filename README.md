# gar — Garmin Connect Data Extractor & LLM Context Builder

Extracts training and health data from Garmin Connect into a partitioned CSV
data lake, mirrors it into an analytical DuckDB lake (raw → curated facts →
derived metrics, flags, validation), and renders a layered, token-budgeted
context package for an LLM-based training coach.

Repository layout:

```
gar/            Python package (cli, extraction, io, lake, context, input, ...)
garmin_data/    CSV lake + garmin.duckdb + context/  (git-ignored data)
tests/unit/     pytest suite (74 tests)
dev/            local plans and drafts (git-ignored)
```

## Requirements

- Python 3.12+
- `pip install -r requirements.txt` (garminconnect, pandas, python-dotenv, duckdb, filelock)
- Garmin account credentials in `.env`.

## Configuration (`.env`)

Copy `.env.example` to `.env` and fill in:

```
GARMIN_EMAIL=<garmin-login-email>
GARMIN_PASSWORD=<garmin-password>
GARMIN_DATA_DIR=garmin_data
GARMIN_LOG_DIR=.gar_logs
GARMIN_SESSION_FILE=garmin_session.json
GARMIN_CREDENTIAL_FILE=garmin_credentials.json
GARMIN_MFA_PROMPT=false
```

Regenerate the template with `./setup_config.sh` (or `setup_config.ps1`).

### MFA (two-factor auth)

First login prompts for a one-time code from the Garmin Connect app. The session
cookie is saved to `garmin_session.json`; later runs reuse it. Use `--no-session`
to force a fresh MFA login.

## Running

The package uses absolute imports, so `PYTHONPATH` must point at the project root.
The launcher handles this:

```bash
cd gar_v2
./run.sh --days 30
```

Or manually:

```bash
cd gar_v2
source venv/bin/activate
PYTHONPATH=. python -m gar --days 30
```

`python -m gar` is supported.

## Command reference

### Sync (extract from Garmin)

```bash
python -m gar --days 30
python -m gar --since 2024-01-01 --until 2026-11-17 --datasets daily,sleep,hrv,activities
python -m gar --resume 30
python -m gar --activity-depth --plan --hrv-readings
python -m gar sync --days 30            # alias
```

Key flags: `--days`, `--since/--until`, `--resume`, `--force`, `--activity-depth`,
`--fit`, `--datasets`, `--plan`, `--no-daily`, `--no-performance`, `--hrv-readings`,
`--no-session`, `--no-progress`, `--mode {real,test}`.

### Subjective log

```bash
python -m gar log --date 2026-11-17 --sleep 3 --fatigue 4 --rpe 7 \
    --note "Travel day" --tags travel,alcohol
python -m gar log --show --json
```

### Coverage report

```bash
python -m gar coverage --days 90
python -m gar coverage --days 90 --json --out coverage.md
python -m gar report coverage --days 90   # alias
```

### DuckDB lake (`gar lake`)

```bash
python -m gar lake build --validate --export
python -m gar lake ingest --datasets activities --full-refresh
python -m gar lake derive
python -m gar lake validate --json
python -m gar lake export --format parquet
python -m gar lake export --format csv
python -m gar lake status
python -m gar lake query "SELECT count(*) FROM fact_daily" --json
```

The lake lives at `garmin_data/garmin.duckdb` and contains:
- `raw_<dataset>` — verbatim CSV mirror (with `_source_file`, `_ingested_at_utc`).
- `fact_*` — curated, unit-normalised tables (`fact_daily`, `fact_activity`,
  `fact_sleep_session`, `fact_flag`, `fact_threshold`, `fact_workout_adherence`,
  `fact_subjective`, `fact_weather`, `fact_strength_set`, ...).
- `sync_state`, `data_quality_log`, `quarantine_record` — ingest state and
  quality audit.
- `fact_recommendation` — recommendation ledger.

Ingest is incremental: a dataset is skipped when its CSV files are unchanged
(fingerprint on path/size/mtime). `--full-refresh` forces a re-read.

### LLM context (`gar context`)

```bash
python -m gar context --days 28 --token-budget 12000
python -m gar context --days 28 --out garmin_data/context --json
```

Writes layered markdown files (`profile.md`, `daily.md`, `activities.md`,
`physiology.md`, `plan.md`, `subjective.md`, `flags.md`, `coverage.md`) plus
`context.md` (combined) and `manifest.json`. PII and GPS coordinates are
redacted by default. A `data_dictionary.md` is also written.

### Recommendations (`gar rec`)

```bash
python -m gar rec log --text "Increase Z2 volume" --horizon-days 7 --evidence '{"acwr":0.7}'
python -m gar rec list --limit 20
python -m gar rec outcome --id <id> --outcome "completed 4/5 sessions" --adhered yes
```

### Migration (`gar migrate`)

```bash
python -m gar migrate csv-to-lake --validate --export
```

## Data model notes

- **Timestamps**: seconds since epoch (UTC). Daily aggregation uses local date.
- **Body composition**: Garmin mass fields are grams; divided by 1000 → kg.
- **Stress / body battery**: flat `[timestamp, value]` lists; `-1` means "no data"
  and is skipped.
- **Sleep stages**: `activityLevel` 0=deep, 1=light, 2=REM, 3=awake.
- **HRV**: nightly summary via single range call; per-night readings only for
  windows ≤ 14 days (`--hrv-readings`).
- **Weather**: normalised from °F/mph to °C/km/h.
- **NULL = missing data**, never `0`/`-1`/`1970`.

## Derived metrics (lake)

`fact_daily` includes: sleep minutes/stages/efficiency/score, HRV night avg +
weekly + status + z-score + baseline, RHR + delta + baseline, readiness, training
status + ACWR + acute/chronic load, body battery (wake/end/avg/min/max),
stress (avg/min/max + zone minutes), steps/distance, weight/body fat, subjective
ratings, monotony, strain, ramp rate, sleep debt (7d), sleep regularity SD,
intensity low/high minutes & %, load 7d sum & 7d/28d ratio, readiness delta,
body-battery wake delta. Flags: `ACWR_HIGH/LOW`, `HRV_LOW`, `HRV_ZSCORE_LOW`,
`RHR_ELEVATED`, `RHR_ELEVATED_3D`, `SLEEP_DEBT`, `SLEEP_DEBT_HIGH`,
`SLEEP_SHORT_3D`, `READINESS_LOW`, `STRESS_HIGH`, `LOAD_RAMP_HIGH`,
`BODY_BATTERY_LOW`, `WORKOUT_MISSED`.

`fact_activity` includes IF, TSS, efficiency factor (power/HR), Banister TRIMP,
time-in-zone 1..5 (minutes).

## Tests

```bash
PYTHONPATH=. pytest -q          # 74 tests
```

## Privacy

Context output redacts emails, phone numbers and GPS coordinate pairs by
default (`gar/context/privacy.py`). Raw GPS is never stored in curated tables;
only activity/weather summaries are kept.

## Architecture

```
gar/extraction/*   Garmin API → typed rows
gar/io/raw_store   raw JSON archive + retrying fetcher
gar/utils/storage  partitioned CSV storage (dedup, atomic writes, allow_future)
gar/lake/*         CSV → DuckDB (raw_* → fact_* → flags) + Parquet/CSV export
gar/context/*      layered context builder + privacy + data dictionary
gar/input/*        subjective training log
gar/recommendations ledger for recommendations and outcomes
```

## Notes for developers

- `dev/` is git-ignored; plans and drafts live there.
- `*.csv`, `*.lock`, `dev/` are never committed.
- User files `analyze_training.py`, `analyze_training_v2.py`,
  `garmin_data/full_context.jsonl`, `garmin_data/full_context.meta.json` must not
  be modified by automated runs.
- Run with `PYTHONPATH=.` (or `./run.sh`). `python -m gar` works.
- CI runs `pytest` on push/PR (`.github/workflows/ci.yml`).
