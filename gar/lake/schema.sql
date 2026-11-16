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

CREATE TABLE IF NOT EXISTS fact_flag (
    flag_id         VARCHAR PRIMARY KEY,
    date_local      DATE NOT NULL,
    severity        VARCHAR NOT NULL,
    code            VARCHAR NOT NULL,
    value           DOUBLE,
    threshold       DOUBLE,
    reason          VARCHAR,
    source          VARCHAR
);
