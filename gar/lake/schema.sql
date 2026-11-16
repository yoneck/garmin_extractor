-- Core metadata tables for the DuckDB lake.
-- Fact tables and curated views are created by gar/lake/derive.py.

CREATE TABLE IF NOT EXISTS meta (
    key             VARCHAR PRIMARY KEY,
    value           VARCHAR NOT NULL,
    updated_at_utc  TIMESTAMP NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS schema_migration (
    version         VARCHAR PRIMARY KEY,
    applied_at_utc  TIMESTAMP NOT NULL DEFAULT current_timestamp,
    description     VARCHAR
);

CREATE TABLE IF NOT EXISTS data_quality_log (
    log_id          VARCHAR PRIMARY KEY,
    ts_utc          TIMESTAMP NOT NULL DEFAULT current_timestamp,
    dataset         VARCHAR,
    rule            VARCHAR,
    severity        VARCHAR,
    rows_affected   BIGINT,
    details         VARCHAR
);

CREATE TABLE IF NOT EXISTS quarantine_record (
    quarantine_id   VARCHAR PRIMARY KEY,
    ts_utc          TIMESTAMP NOT NULL DEFAULT current_timestamp,
    dataset         VARCHAR NOT NULL,
    reason          VARCHAR NOT NULL,
    payload_json    VARCHAR NOT NULL,
    source_file     VARCHAR
);

CREATE TABLE IF NOT EXISTS sync_state (
    dataset         VARCHAR PRIMARY KEY,
    watermark_utc   TIMESTAMP,
    last_run_utc    TIMESTAMP NOT NULL DEFAULT current_timestamp,
    status          VARCHAR,
    rows            BIGINT,
    error           VARCHAR,
    duration_s      DOUBLE,
    fingerprint     VARCHAR
);

CREATE TABLE IF NOT EXISTS fact_recommendation (
    rec_id          VARCHAR PRIMARY KEY,
    created_utc     TIMESTAMP NOT NULL DEFAULT current_timestamp,
    horizon_days    INTEGER,
    recommendation  VARCHAR NOT NULL,
    evidence_json   VARCHAR,
    status          VARCHAR,
    outcome         VARCHAR,
    adhered         BOOLEAN,
    reviewed_utc    TIMESTAMP
);
