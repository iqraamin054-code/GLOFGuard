-- REVIEW ONLY. No remote execution has been authorized.
-- Preserve original SQLite column names and value types for lossless reconciliation.
-- Dates and JSON remain TEXT in these source-preservation tables intentionally.
-- The future runtime conversion must explicitly map/validate them.
-- No PostGIS, public table, or Data API exposure changes.
-- internal.lakes and internal.time_series_records are source-preservation/audit
-- tables only. Canonical runtime models remain public.lakes and
-- public.environmental_observations; never route runtime writes to these mirrors.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

CREATE SCHEMA IF NOT EXISTS internal;
CREATE SCHEMA IF NOT EXISTS reference;
REVOKE ALL ON SCHEMA internal, reference FROM PUBLIC, anon, authenticated;
-- A non-login role; credentials and role membership are provisioned separately.
-- Fail on an existing role rather than silently trust unknown privileges.
CREATE ROLE glof_backend NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT USAGE ON SCHEMA internal, reference TO glof_backend;

CREATE TABLE internal.lakes (
    lake_id TEXT PRIMARY KEY,
    name TEXT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    elevation_m DOUBLE PRECISION,
    distance_to_nearest_settlement_km DOUBLE PRECISION,
    reference_area_km2 DOUBLE PRECISION,
    updated_at TEXT NOT NULL
);
ALTER TABLE internal.lakes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.lakes FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.lakes TO glof_backend;
CREATE POLICY backend_only ON internal.lakes FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.official_lakes (
    lake_id TEXT PRIMARY KEY,
    official_source_id TEXT NOT NULL,
    name TEXT,
    basin TEXT,
    lake_type TEXT,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    elevation_m DOUBLE PRECISION,
    distance_to_nearest_settlement_km DOUBLE PRECISION,
    reference_area_km2 DOUBLE PRECISION,
    matched_source_lake_id TEXT,
    match_distance_m DOUBLE PRECISION,
    match_method TEXT,
    matched_source_count BIGINT NOT NULL DEFAULT 0,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
ALTER TABLE internal.official_lakes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.official_lakes FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.official_lakes TO glof_backend;
CREATE POLICY backend_only ON internal.official_lakes FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.inventory_reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    official_source_path TEXT NOT NULL,
    official_source_sha256 TEXT NOT NULL,
    existing_source_path TEXT NOT NULL,
    existing_source_sha256 TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
ALTER TABLE internal.inventory_reconciliations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.inventory_reconciliations FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.inventory_reconciliations TO glof_backend;
CREATE POLICY backend_only ON internal.inventory_reconciliations FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.inventory_matches (
    reconciliation_id TEXT NOT NULL,
    source_row_number BIGINT NOT NULL,
    source_lake_id TEXT NOT NULL,
    source_system_lake_id TEXT,
    status TEXT NOT NULL,
    official_lake_id TEXT,
    match_method TEXT,
    match_distance_m DOUBLE PRECISION,
    duplicate_of_source_lake_id TEXT,
    details_json TEXT NOT NULL,
    PRIMARY KEY (reconciliation_id, source_row_number),
    FOREIGN KEY (reconciliation_id) REFERENCES internal.inventory_reconciliations(reconciliation_id)
);
ALTER TABLE internal.inventory_matches ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.inventory_matches FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.inventory_matches TO glof_backend;
CREATE POLICY backend_only ON internal.inventory_matches FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.full_inventory_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL
);
ALTER TABLE internal.full_inventory_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.full_inventory_runs FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.full_inventory_runs TO glof_backend;
CREATE POLICY backend_only ON internal.full_inventory_runs FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.daily_weather (
    lake_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    temperature_c DOUBLE PRECISION,
    rainfall_mm DOUBLE PRECISION,
    source TEXT NOT NULL,
    is_mock BIGINT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, observation_date, source)
);
ALTER TABLE internal.daily_weather ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.daily_weather FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.daily_weather TO glof_backend;
CREATE POLICY backend_only ON internal.daily_weather FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.hourly_precipitation (
    lake_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    rainfall_mm DOUBLE PRECISION NOT NULL,
    source TEXT NOT NULL,
    is_mock BIGINT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, observed_at, source)
);
ALTER TABLE internal.hourly_precipitation ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.hourly_precipitation FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.hourly_precipitation TO glof_backend;
CREATE POLICY backend_only ON internal.hourly_precipitation FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.forecasts (
    lake_id TEXT NOT NULL,
    forecast_created_at TEXT NOT NULL,
    valid_at TEXT NOT NULL,
    precipitation_mm DOUBLE PRECISION,
    temperature_c DOUBLE PRECISION,
    horizon_hours BIGINT NOT NULL,
    source TEXT NOT NULL,
    is_mock BIGINT NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, forecast_created_at, valid_at, source)
);
ALTER TABLE internal.forecasts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.forecasts FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.forecasts TO glof_backend;
CREATE POLICY backend_only ON internal.forecasts FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.satellite_observations (
    lake_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    area_km2 DOUBLE PRECISION,
    cloud_percentage DOUBLE PRECISION NOT NULL,
    clear_fraction DOUBLE PRECISION NOT NULL,
    quality_status TEXT NOT NULL,
    source_image_id TEXT NOT NULL,
    water_index TEXT NOT NULL,
    source TEXT NOT NULL,
    is_mock BIGINT NOT NULL,
    rejection_reason TEXT,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, observed_at, source_image_id)
);
ALTER TABLE internal.satellite_observations ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.satellite_observations FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.satellite_observations TO glof_backend;
CREATE POLICY backend_only ON internal.satellite_observations FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.time_series_records (
    lake_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    input_signature TEXT NOT NULL,
    record_json TEXT NOT NULL,
    prediction_timestamp TEXT,
    model_version TEXT NOT NULL,
    risk_score DOUBLE PRECISION,
    risk_level TEXT,
    data_quality_status TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    PRIMARY KEY (lake_id, observation_date, input_signature)
);
ALTER TABLE internal.time_series_records ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.time_series_records FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.time_series_records TO glof_backend;
CREATE POLICY backend_only ON internal.time_series_records FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.ingestion_failures (
    id BIGINT PRIMARY KEY,
    lake_id TEXT,
    source TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    resolved BIGINT NOT NULL DEFAULT 0
);
ALTER TABLE internal.ingestion_failures ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.ingestion_failures FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.ingestion_failures TO glof_backend;
CREATE POLICY backend_only ON internal.ingestion_failures FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.monitoring_snapshots (
    created_at TEXT PRIMARY KEY,
    metrics_json TEXT NOT NULL
);
ALTER TABLE internal.monitoring_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.monitoring_snapshots FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.monitoring_snapshots TO glof_backend;
CREATE POLICY backend_only ON internal.monitoring_snapshots FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.full_inventory_lake_status (
    canonical_lake_id TEXT NOT NULL REFERENCES public.lakes(lake_id),
    run_id TEXT NOT NULL,
    lake_id TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    status_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, lake_id)
);
ALTER TABLE internal.full_inventory_lake_status ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.full_inventory_lake_status FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.full_inventory_lake_status TO glof_backend;
CREATE POLICY backend_only ON internal.full_inventory_lake_status FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.full_inventory_error_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    lake_id TEXT NOT NULL,
    source TEXT NOT NULL,
    attempt_number BIGINT NOT NULL,
    occurred_at TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL
);
ALTER TABLE internal.full_inventory_error_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.full_inventory_error_events FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.full_inventory_error_events TO glof_backend;
CREATE POLICY backend_only ON internal.full_inventory_error_events FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.environmental_source_snapshots (
    lake_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, source_type, source_timestamp, source)
);
ALTER TABLE internal.environmental_source_snapshots ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.environmental_source_snapshots FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.environmental_source_snapshots TO glof_backend;
CREATE POLICY backend_only ON internal.environmental_source_snapshots FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE internal.sync_outbox (
    event_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL UNIQUE,
    lake_id TEXT NOT NULL,
    local_record_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    source_mode TEXT NOT NULL CHECK (source_mode = 'REAL'),
    status TEXT NOT NULL CHECK (status IN (
        'REMOTE_PENDING', 'REMOTE_SAVED', 'REMOTE_FAILED'
    )),
    retryable BIGINT NOT NULL DEFAULT 1 CHECK (retryable IN (0, 1)),
    attempt_count BIGINT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TEXT,
    last_attempt_at TEXT,
    last_error_type TEXT,
    last_error_message TEXT,
    remote_receipt_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
ALTER TABLE internal.sync_outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON internal.sync_outbox FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON internal.sync_outbox TO glof_backend;
CREATE POLICY backend_only ON internal.sync_outbox FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE reference.pmd_2013_source_rows (source_row BIGINT PRIMARY KEY, geometry_validation_status TEXT NOT NULL, row_status TEXT NOT NULL, payload_json TEXT NOT NULL);
ALTER TABLE reference.pmd_2013_source_rows ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reference.pmd_2013_source_rows FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON reference.pmd_2013_source_rows TO glof_backend;
CREATE POLICY backend_only ON reference.pmd_2013_source_rows FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE reference.pmd_2013_lakes (pmd_id TEXT PRIMARY KEY, original_pmd_lake_id TEXT NOT NULL, basin TEXT NOT NULL,
            source_organization TEXT NOT NULL, source_year BIGINT NOT NULL CHECK(source_year=2013),
            source_role TEXT NOT NULL CHECK(source_role='OFFICIAL_HISTORICAL_REFERENCE'),
            geometry_validation_status TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(basin,original_pmd_lake_id));
ALTER TABLE reference.pmd_2013_lakes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reference.pmd_2013_lakes FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON reference.pmd_2013_lakes TO glof_backend;
CREATE POLICY backend_only ON reference.pmd_2013_lakes FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE reference.pmd_2013_row_memberships (source_row BIGINT NOT NULL REFERENCES reference.pmd_2013_source_rows(source_row),
            pmd_id TEXT NOT NULL REFERENCES reference.pmd_2013_lakes(pmd_id), resolution TEXT NOT NULL,
            payload_json TEXT NOT NULL, PRIMARY KEY(source_row,pmd_id));
ALTER TABLE reference.pmd_2013_row_memberships ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reference.pmd_2013_row_memberships FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON reference.pmd_2013_row_memberships TO glof_backend;
CREATE POLICY backend_only ON reference.pmd_2013_row_memberships FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE reference.pmd_2013_current_coverage (lake_id TEXT PRIMARY KEY, pmd_id TEXT REFERENCES reference.pmd_2013_lakes(pmd_id), status TEXT NOT NULL, payload_json TEXT NOT NULL);
ALTER TABLE reference.pmd_2013_current_coverage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reference.pmd_2013_current_coverage FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON reference.pmd_2013_current_coverage TO glof_backend;
CREATE POLICY backend_only ON reference.pmd_2013_current_coverage FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE reference.pmd_2013_crosswalk (pmd_id TEXT PRIMARY KEY REFERENCES reference.pmd_2013_lakes(pmd_id),
            current_lake_id TEXT UNIQUE REFERENCES reference.pmd_2013_current_coverage(lake_id), status TEXT NOT NULL, payload_json TEXT NOT NULL);
ALTER TABLE reference.pmd_2013_crosswalk ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reference.pmd_2013_crosswalk FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON reference.pmd_2013_crosswalk TO glof_backend;
CREATE POLICY backend_only ON reference.pmd_2013_crosswalk FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE reference.pmd_2013_match_candidates (pmd_id TEXT NOT NULL REFERENCES reference.pmd_2013_lakes(pmd_id),
            current_lake_id TEXT NOT NULL REFERENCES reference.pmd_2013_current_coverage(lake_id), distance_m DOUBLE PRECISION NOT NULL CHECK(distance_m>=0),
            payload_json TEXT NOT NULL, PRIMARY KEY(pmd_id,current_lake_id));
ALTER TABLE reference.pmd_2013_match_candidates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reference.pmd_2013_match_candidates FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON reference.pmd_2013_match_candidates TO glof_backend;
CREATE POLICY backend_only ON reference.pmd_2013_match_candidates FOR ALL TO glof_backend USING (true) WITH CHECK (true);

CREATE TABLE reference.pmd_2013_metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
ALTER TABLE reference.pmd_2013_metadata ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON reference.pmd_2013_metadata FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON reference.pmd_2013_metadata TO glof_backend;
CREATE POLICY backend_only ON reference.pmd_2013_metadata FOR ALL TO glof_backend USING (true) WITH CHECK (true);

ALTER TABLE internal.lakes ADD CONSTRAINT lakes_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
ALTER TABLE internal.daily_weather ADD CONSTRAINT daily_weather_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
ALTER TABLE internal.hourly_precipitation ADD CONSTRAINT hourly_precipitation_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
ALTER TABLE internal.forecasts ADD CONSTRAINT forecasts_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
ALTER TABLE internal.satellite_observations ADD CONSTRAINT satellite_observations_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
ALTER TABLE internal.time_series_records ADD CONSTRAINT time_series_records_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
ALTER TABLE internal.ingestion_failures ADD CONSTRAINT ingestion_failures_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
CREATE INDEX ingestion_failures_lake_fk_idx ON internal.ingestion_failures(lake_id);
ALTER TABLE internal.full_inventory_error_events ADD CONSTRAINT full_inventory_error_events_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
CREATE INDEX full_inventory_error_events_lake_fk_idx ON internal.full_inventory_error_events(lake_id);
ALTER TABLE internal.environmental_source_snapshots ADD CONSTRAINT environmental_source_snapshots_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
ALTER TABLE internal.sync_outbox ADD CONSTRAINT sync_outbox_canonical_lake_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
CREATE INDEX sync_outbox_lake_fk_idx ON internal.sync_outbox(lake_id);

ALTER TABLE internal.full_inventory_lake_status ADD CONSTRAINT full_inventory_status_run_fk FOREIGN KEY (run_id) REFERENCES internal.full_inventory_runs(run_id);
ALTER TABLE internal.full_inventory_error_events ADD CONSTRAINT full_inventory_errors_run_fk FOREIGN KEY (run_id) REFERENCES internal.full_inventory_runs(run_id);
ALTER TABLE reference.pmd_2013_current_coverage ADD CONSTRAINT pmd_coverage_current_fk FOREIGN KEY (lake_id) REFERENCES public.lakes(lake_id);
CREATE INDEX full_inventory_status_canonical_idx ON internal.full_inventory_lake_status(canonical_lake_id);
CREATE INDEX full_inventory_errors_run_idx ON internal.full_inventory_error_events(run_id);
CREATE INDEX pmd_memberships_lake_idx ON reference.pmd_2013_row_memberships(pmd_id);
CREATE INDEX pmd_coverage_pmd_idx ON reference.pmd_2013_current_coverage(pmd_id);
CREATE INDEX pmd_candidates_current_idx ON reference.pmd_2013_match_candidates(current_lake_id);
CREATE INDEX outbox_retry_idx ON internal.sync_outbox(status, next_attempt_at);

-- No FK on raw status lake_id: it includes excluded source-polygon identifiers.
-- No public FK on official_lakes identifiers or inventory_matches source identifiers:
-- these belong to external/source inventories, including unmatched and duplicate rows.
-- No observation/run FK on sync_outbox: it must hold events BEFORE publication succeeds.
-- Crosswalk current_lake_id UNIQUE exactly mirrors source; candidates remain many-to-many.
COMMIT;
