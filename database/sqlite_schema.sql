PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS evidence_versions (
    evidence_version TEXT PRIMARY KEY,
    evidence_kind TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    source_path TEXT NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lakes (
    map_id INTEGER PRIMARY KEY,
    lake_id TEXT NOT NULL UNIQUE,
    source_polygon_id INTEGER NOT NULL UNIQUE,
    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    elevation_m REAL,
    reference_area_km2 REAL,
    distance_to_nearest_settlement_km REAL,
    location_wkt TEXT NOT NULL,
    source_geometry_valid INTEGER NOT NULL CHECK (source_geometry_valid IN (0, 1)),
    source_validation_warning TEXT,
    inventory_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS lakes_rtree USING rtree(
    map_id,
    min_longitude,
    max_longitude,
    min_latitude,
    max_latitude
);

CREATE TABLE IF NOT EXISTS baseline_susceptibility (
    lake_id TEXT PRIMARY KEY REFERENCES lakes(lake_id),
    susceptibility_score REAL CHECK (susceptibility_score BETWEEN 0 AND 100),
    susceptibility_level TEXT CHECK (
        susceptibility_level IN ('Low', 'Medium', 'High') OR susceptibility_level IS NULL
    ),
    missing_static_fields TEXT,
    interpretation TEXT NOT NULL,
    model_version TEXT NOT NULL,
    calculated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    run_kind TEXT NOT NULL,
    evidence_version TEXT REFERENCES evidence_versions(evidence_version),
    source_mode TEXT NOT NULL CHECK (source_mode IN ('REAL', 'MOCK', 'NONE')),
    run_status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    exact_command TEXT,
    selected_lake_count INTEGER NOT NULL DEFAULT 0,
    successful_lake_count INTEGER NOT NULL DEFAULT 0,
    failed_lake_count INTEGER NOT NULL DEFAULT 0,
    mock_count INTEGER NOT NULL DEFAULT 0,
    runtime_seconds REAL,
    api_usage_json TEXT NOT NULL,
    data_version TEXT NOT NULL,
    evidence_manifest_sha256 TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS environmental_observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    lake_id TEXT NOT NULL REFERENCES lakes(lake_id),
    observation_date TEXT NOT NULL,
    source_mode TEXT NOT NULL CHECK (source_mode = 'REAL'),
    environmental_conditions_score REAL CHECK (
        environmental_conditions_score BETWEEN 0 AND 100
        OR environmental_conditions_score IS NULL
    ),
    environmental_conditions_level TEXT,
    score_interpretation TEXT NOT NULL,
    current_area_km2 REAL,
    sentinel_observation_at TEXT,
    sentinel_cloud_percentage REAL,
    current_temperature_c REAL,
    rainfall_last_24h_mm REAL,
    rainfall_last_7d_mm REAL,
    rainfall_last_30d_mm REAL,
    gsmap_observed_at TEXT,
    forecast_rainfall_next_72h_mm REAL,
    forecast_temperature_next_24h_c REAL,
    forecast_temperature_next_7d_c REAL,
    gfs_created_at TEXT,
    nasa_power_baseline_available INTEGER NOT NULL CHECK (
        nasa_power_baseline_available IN (0, 1)
    ),
    nasa_power_latest_date TEXT,
    confidence TEXT,
    data_completeness_warning TEXT,
    model_version TEXT NOT NULL,
    data_version TEXT NOT NULL,
    retrieved_at TEXT,
    raw_evidence_json TEXT NOT NULL,
    UNIQUE (run_id, lake_id)
);

CREATE TABLE IF NOT EXISTS source_freshness (
    observation_id TEXT NOT NULL REFERENCES environmental_observations(observation_id),
    lake_id TEXT NOT NULL REFERENCES lakes(lake_id),
    source_name TEXT NOT NULL,
    availability_status TEXT NOT NULL,
    freshness_status TEXT NOT NULL,
    quality_status TEXT,
    source_observation_at TEXT,
    age_hours REAL,
    details_json TEXT NOT NULL,
    PRIMARY KEY (observation_id, source_name)
);

CREATE TABLE IF NOT EXISTS processing_queue (
    lake_id TEXT PRIMARY KEY REFERENCES lakes(lake_id),
    processing_status TEXT NOT NULL CHECK (processing_status IN (
        'LIVE_COMPLETE',
        'LIVE_PARTIAL',
        'LIVE_PENDING',
        'SATELLITE_STALE',
        'SATELLITE_UNAVAILABLE',
        'PROCESSING_FAILED'
    )),
    approval_required INTEGER NOT NULL DEFAULT 1 CHECK (approval_required IN (0, 1)),
    batch_id TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_successful_run_id TEXT REFERENCES ingestion_runs(run_id),
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_reconciliation (
    source_row_number INTEGER PRIMARY KEY,
    source_polygon_id INTEGER NOT NULL UNIQUE,
    source_lake_id TEXT NOT NULL,
    canonical_lake_id TEXT NOT NULL,
    processed_sample_id INTEGER,
    reconciliation_status TEXT NOT NULL,
    reconciliation_reason TEXT NOT NULL,
    eligible_for_processing INTEGER NOT NULL CHECK (eligible_for_processing IN (0, 1)),
    source_geometry_sha256 TEXT NOT NULL,
    source_geometry_valid INTEGER NOT NULL CHECK (source_geometry_valid IN (0, 1)),
    source_validation_warning TEXT,
    source_centroid_latitude REAL,
    source_centroid_longitude REAL,
    source_area_km2 REAL,
    inventory_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_duplicate_mappings (
    duplicate_source_polygon_id INTEGER PRIMARY KEY,
    canonical_lake_id TEXT NOT NULL REFERENCES lakes(lake_id),
    canonical_source_polygon_id INTEGER NOT NULL,
    duplicate_geometry_sha256 TEXT NOT NULL,
    reconciliation_reason TEXT NOT NULL,
    inventory_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lakes_lat_lon ON lakes(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_baseline_level ON baseline_susceptibility(susceptibility_level);
CREATE INDEX IF NOT EXISTS idx_observations_lake_date ON environmental_observations(lake_id, observation_date DESC);
CREATE INDEX IF NOT EXISTS idx_queue_status ON processing_queue(processing_status);
CREATE INDEX IF NOT EXISTS idx_source_freshness_lake ON source_freshness(lake_id, source_name);

CREATE VIEW IF NOT EXISTS map_lake_details AS
WITH latest_observation AS (
    SELECT environmental_observations.*
    FROM environmental_observations
    JOIN (
        SELECT lake_id, MAX(rowid) AS latest_rowid
        FROM environmental_observations
        GROUP BY lake_id
    ) latest ON environmental_observations.rowid = latest.latest_rowid
)
SELECT
    lakes.map_id,
    lakes.lake_id,
    lakes.latitude,
    lakes.longitude,
    lakes.elevation_m,
    lakes.reference_area_km2,
    lakes.distance_to_nearest_settlement_km,
    lakes.source_geometry_valid,
    lakes.source_validation_warning,
    processing_queue.processing_status,
    processing_queue.approval_required,
    baseline_susceptibility.susceptibility_score,
    baseline_susceptibility.susceptibility_level,
    baseline_susceptibility.interpretation AS susceptibility_interpretation,
    latest_observation.environmental_conditions_score,
    latest_observation.environmental_conditions_level,
    latest_observation.score_interpretation,
    latest_observation.current_area_km2,
    latest_observation.sentinel_observation_at,
    latest_observation.sentinel_cloud_percentage,
    latest_observation.current_temperature_c,
    latest_observation.rainfall_last_24h_mm,
    latest_observation.rainfall_last_7d_mm,
    latest_observation.rainfall_last_30d_mm,
    latest_observation.gsmap_observed_at,
    latest_observation.forecast_rainfall_next_72h_mm,
    latest_observation.forecast_temperature_next_24h_c,
    latest_observation.forecast_temperature_next_7d_c,
    latest_observation.gfs_created_at,
    latest_observation.nasa_power_baseline_available,
    latest_observation.nasa_power_latest_date,
    latest_observation.confidence,
    latest_observation.data_completeness_warning,
    latest_observation.model_version,
    latest_observation.data_version,
    latest_observation.source_mode,
    latest_observation.retrieved_at
FROM lakes
JOIN processing_queue ON processing_queue.lake_id = lakes.lake_id
LEFT JOIN baseline_susceptibility ON baseline_susceptibility.lake_id = lakes.lake_id
LEFT JOIN latest_observation ON latest_observation.lake_id = lakes.lake_id;
