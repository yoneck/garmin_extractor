# gar — Garmin Connect Data Extlag Extractor

Extracts training and health data from Garmin Connect into a partitioned CSV data lake (one CSV per table, split by month) and renders an LLM context summary for training plan analysis.

## English

### What it does

Each pipeline run (`gar/cli/main.py`) downloads and writes:

| Table | Source method |
| --- | --- |
| `activities` | `get_activities` |
| `activity_laps` | per-activity depth (see `--activity-depth`) |
| `activity_splits` | per-activity depth |
| `activity_powers` | per-activity depth |
| `activity_power_metrics` | per-activity depth |
| `activity_hr` | per-activity depth |
| `sleep_summary` | `get_sleep_data` → `dailySleepDTO` |
| `sleep_stages` | `get_sleep_data` → `calendarDataArray` |
| `hrv` | `get_hrv_data` → `hrvReadings` |
| `stress` | `get_stress_data` → `stressValuesArray` |
| `body_battery` | `get_body_battery` → `bodyBatteryValuesArray` |
| `body_composition` | `get_body_composition` → `dateWeightList` |

### Consolidated Context
A consolidated JSONL file (`garmin_data/full_context.jsonl`) is generated, containing both activity details and biometric records (sleep, HRV, stress, etc.) in a single stream.

CSVs live under `garmin_data/` (path from `.env` `GARMIN_DATA_DIR`).
A text summary of the data is written to `garmin_data/context/summary.md`.

### Requirements

- Python 3.12+
- Dependencies: `pip install -r requirements.txt` (garminconnect, pandas, python-dotenv)
- Credentials in `.env` (see below).

### Configuration — `.env`

Copy `.env.example` to `.env` and fill in values:

```
GARMIN_EMAIL=<garmin-login-email>
GARMIN_PASSWORD=<garmin-password>
GARMIN_DATA_DIR=garmin_data
GARMIN_LOG_DIR=.gar_logs
GARMIN_SESSION_FILE=garmin_session.json
GARMIN_CREDENTIAL_FILE=garmin_credentials.json
GARMIN_MFA_PROMPT=false
```

A filled template is saved as `.env.example` in this repo. To regenerate it, run `./setup_config.sh` (or `setup_config.ps1`).

#### MFA (two-factor auth)

The first time you log in, the pipeline prompts for a one-time code. Type it from your Garmin Connect app and press Enter. The session cookie is saved to `garmin_session.json`; subsequent runs reuse it and need **no MFA**. If no interactive terminal is attached (e.g. piped input), the login fails and you should reconnect with a fresh session.

To force a fresh MFA login, pass `--no-session` (discards the saved session).

### How to run

The extractor uses absolute imports (`from gar...`), so `PYTHONPATH` must point at the project root.

#### Recommended: `./run.sh`

```bash
cd gar_v2
./run.sh --days 30
```

`run.sh` activates the venv, sets `PYTHONPGPATH`, and forwards all arguments to the extractor.

#### Alternatively, run manually:

```bash
cd gar_v2
source venv/bin/activate
PYTHONPATH=. python gar/cli/main.py --days 30
```

> `python -m gar` does **not** work yet. Use `./run.sh` or the manual command above.

#### Common flags

| Flag | Effect |
| --- | --- |
| `--days N` | Extract N days back from today (default 30). |
| `--resume N` | Resume from the last extracted data within the last N days. |
| `--activity-depth` | Also download FIT files plus per-activity laps/splits/power/HR. |
| `--no-session` | Discard the saved Garmin session (forces a new MFA login). |
| `--mode {real,test}` | Real = live API (default). Test = mock client, no network. |

### Data model notes

- **Timestamps**: storage is seconds-since-epoch.
- **Body composition**: mass fields from Garmin are in **grams**. `extract_body_composition` divides by 1000 to store kg.
- **Stress / body battery** readings are flat lists of `[timestamp, value]` pairs; stress value `-1` means "no data" and is skipped.

---

## Polski

Ekstrahuje dane treningowe i zdrowotne z Garmin Connect do podzielonego na części jeziora danych CSV (jeden plik CSV na tabelę, podzielony miesiącami) oraz generuje podsumowanie kontekstu dla LLM do analizy planów treningowych.

### Co robi ten program

Każde uruchomienie potoku (`gar/cli/main.py`) pobiera i zapisuje:

| Tabela | Metoda źródłowa |
| --- | --- |
| `activities` | `get_activities` |
| `activity_laps` | głębokość na aktywność (patrz `--activity-depth`) |
| `activity_splits` | głębokość na aktywność |
| `activity_powers` | głębokość na aktywność |
| `activity_power_metrics` | głębokość na aktywność |
| `activity_hr` | głębokość na aktywność |
| `sleep_summary` | `get_sleep_data` → `dailySleepDTO` |
| `sleep_stages` | `get_sleep_data` → `calendarDataArray` |
| `hrv` | `get_hrv_data` → `hrvReadings` |
| `stress` | `get_stress_data` → `stressValuesArray` |
| `body_battery` | `get_body_battery` → `bodyBatteryValuesArray` |
| `body_composition` | `get_body_composition` → `dateWeightList` |

### Skonsolidowany Kontekst
Generowany jest skonsolidowany plik JSONL (`garmin_data/full_context.jsonl`), który zawiera zarówno szczegóły aktywności, jak i rekordy biometryczne (sen, HRV, stres itp.) w jednym strumieniu.

Pliki CSV znajdują się w `garmin_data/` (path from `.env` `GARMIN_DATA_DIR`).
Podsumowanie tekstowe danych jest zapisywane w `gar_v2/garmin_data/context/summary.md`.

### Wymagania

- Python 3.12+
- Zależności: `pip install -r requirements.txt` (garminconnect, pandas, python-dotenv)
- Dane logowania w `.env` (patrz poniżej).

### Konfiguracja — `.env`

Skopiuj `.env.example` do `.env` i uzupełnij wartości:

```
GARMIN_EMAIL=<twój@email.com>
GARMIN_PASSWORD=<twoje_haslo>
GARMIN_DATA_DIR=garmin_data
GARMIN_LOG_DIR=.gar_logs
GARMIN_SESSION_FILE=garmin_session.json
GARMIN_CREDENTIAL_FILE=garmin_credentials.json
GARMIN_MFA_PROMPT=false
```

Gotowy szablon znajduje się jako `.env.example` w tym repozytorium. Aby go wygenerować ponownie, uruchom `./setup_config.async` (lub `setup_config.ps1`).

#### MFA (uwierzytelnianie dwuskładnikowe)

Przy pierwszym logowaniu program poprosi o jednorazowy kod. Wpisz go z aplikacji Garmin Connect i naciśnij Enter. Plik sesji jest zapisywany w `garmin_session.json`; kolejne uruchomienia korzystają z niego i **nie wymagają MFA**.

Aby wymusić nowe logowanie MFA, użyj flagi `--no-session` (usuwa zapisaną sesję).

### Jak uruchomić

Program używa importów bezwzględnych (`from gar...`), więc `PYTHONPATH` musi wskazywać na folder główny projektu. Najprostszym sposobem jest dostarczony launcher:

#### Zalecane: `./run.sh`

```bash
cd gar_v2
./run.sh --days 30
```

`run.sh` aktywuje venv, ustawia `PYTHONPATH` i przekazuje wszystkie argumenty do ekstraktora.

#### Alternatywnie, uruchom manualnie:

```bash
cd gar_v2
source venv/bin/activate
PYTHONPATH=. python gar/cli/main.py --days 30
```

> `python -m gar` **nie działa** jeszcze. Użyj `./run.sh` lub powyższej komendy manualnej.

#### Popularne flagi

| Flaga | Działanie |
| --- | --- |
| `--days N` | Pobierz dane z N dni wstecz od dzisiaj (domyślnie 30). |
| `--resume N` | Wznów pobieranie od ostatniego pobranego dnia w ciągu ostatnich N dni. |
| `--activity-depth` | Pobierz również pliki FIT oraz okrążenia/podziały/moc/HR dla każdej aktywności. |
| `--no-session` | Usuń zapisaną sesję Garmin (wymusza nowe logowanie MFA). |
| `--mode {real,test}` | Real = żywe API (domyślnie). Test = mock client, bez sieci. |

### Uwagi do modelu danych

- **Znaczniki czasu**: składowanie w sekundach od epoki.
- **Skład ciała**: pola masy z Garmina są w **gramach**. `extract_body_composition` dzieli przez 1000, aby przechowywać kg.
- **Stress / body battery**: odczyty to listy `[timestamp, value]`; wartość `-1` oznacza "brak danych".
