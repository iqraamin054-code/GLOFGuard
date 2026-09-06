-- Supabase/PostgreSQL deployment schema. Apply only after a configured,
-- operator-approved Supabase database is available.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS public.lakes (
    lake_id text PRIMARY KEY,
    source_polygon_id integer NOT NULL UNIQUE,
    latitude double precision NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude double precision NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    elevation_m double precision,
    reference_area_km2 double precision,
    distance_to_nearest_settlement_km double precision,
    location geometry(Point, 4326) NOT NULL,
    source_geometry_valid boolean NOT NULL,
    source_validation_warning text,
    inventory_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS lakes_location_gix ON public.lakes USING gist(location);

CREATE TABLE IF NOT EXISTS public.baseline_susceptibility (
    lake_id text PRIMARY KEY REFERENCES public.lakes(lake_id),
    susceptibility_score double precision CHECK (susceptibility_score BETWEEN 0 AND 100),
    susceptibility_level text CHECK (
        susceptibility_level IN ('Low', 'Medium', 'High') OR susceptibility_level IS NULL
    ),
    missing_static_fields text,
    interpretation text NOT NULL,
    model_version text NOT NULL,
    calculated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS public.ingestion_runs (
    run_id text PRIMARY KEY,
    run_kind text NOT NULL,
    evidence_version text,
    source_mode text NOT NULL CHECK (source_mode IN ('REAL', 'MOCK', 'NONE')),
    run_status text NOT NULL,
    started_at timestamptz,
    completed_at timestamptz,
    exact_command text,
    selected_lake_count integer NOT NULL DEFAULT 0,
    successful_lake_count integer NOT NULL DEFAULT 0,
    failed_lake_count integer NOT NULL DEFAULT 0,
    mock_count integer NOT NULL DEFAULT 0,
    runtime_seconds double precision,
    api_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    data_version text NOT NULL,
    evidence_manifest_sha256 text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.environmental_observations (
    observation_id text PRIMARY KEY,
    run_id text NOT NULL REFERENCES public.ingestion_runs(run_id),
    lake_id text NOT NULL REFERENCES public.lakes(lake_id),
    observation_date date NOT NULL,
    source_mode text NOT NULL CHECK (source_mode = 'REAL'),
    environmental_conditions_score double precision CHECK (
        environmental_conditions_score BETWEEN 0 AND 100
        OR environmental_conditions_score IS NULL
    ),
    environmental_conditions_level text,
    score_interpretation text NOT NULL,
    current_area_km2 double precision,
    sentinel_observation_at timestamptz,
    sentinel_cloud_percentage double precision,
    current_temperature_c double precision,
    rainfall_last_24h_mm double precision,
    rainfall_last_7d_mm double precision,
    rainfall_last_30d_mm double precision,
    gsmap_observed_at timestamptz,
    forecast_rainfall_next_72h_mm double precision,
    forecast_temperature_next_24h_c double precision,
    forecast_temperature_next_7d_c double precision,
    gfs_created_at timestamptz,
    nasa_power_baseline_available boolean NOT NULL,
    nasa_power_latest_date date,
    confidence text,
    data_completeness_warning text,
    model_version text NOT NULL,
    data_version text NOT NULL,
    retrieved_at timestamptz,
    raw_evidence jsonb NOT NULL,
    UNIQUE (run_id, lake_id)
);

CREATE TABLE IF NOT EXISTS public.source_freshness (
    observation_id text NOT NULL REFERENCES public.environmental_observations(observation_id),
    lake_id text NOT NULL REFERENCES public.lakes(lake_id),
    source_name text NOT NULL,
    availability_status text NOT NULL,
    freshness_status text NOT NULL,
    quality_status text,
    source_observation_at timestamptz,
    age_hours double precision,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (observation_id, source_name)
);

CREATE TABLE IF NOT EXISTS public.processing_queue (
    lake_id text PRIMARY KEY REFERENCES public.lakes(lake_id),
    processing_status text NOT NULL CHECK (processing_status IN (
        'LIVE_COMPLETE',
        'LIVE_PARTIAL',
        'LIVE_PENDING',
        'SATELLITE_STALE',
        'SATELLITE_UNAVAILABLE',
        'PROCESSING_FAILED'
    )),
    approval_required boolean NOT NULL DEFAULT true,
    batch_id text,
    priority integer NOT NULL DEFAULT 0,
    attempt_count integer NOT NULL DEFAULT 0,
    last_attempt_at timestamptz,
    last_successful_run_id text REFERENCES public.ingestion_runs(run_id),
    last_error text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.inventory_reconciliation (
    source_row_number integer PRIMARY KEY,
    source_polygon_id integer NOT NULL UNIQUE,
    source_lake_id text NOT NULL,
    canonical_lake_id text NOT NULL,
    processed_sample_id integer,
    reconciliation_status text NOT NULL,
    reconciliation_reason text NOT NULL,
    eligible_for_processing boolean NOT NULL,
    source_geometry_sha256 text NOT NULL,
    source_geometry_valid boolean NOT NULL,
    source_validation_warning text,
    source_centroid_latitude double precision,
    source_centroid_longitude double precision,
    source_area_km2 double precision,
    inventory_version text NOT NULL
);

CREATE TABLE IF NOT EXISTS public.inventory_duplicate_mappings (
    duplicate_source_polygon_id integer PRIMARY KEY,
    canonical_lake_id text NOT NULL REFERENCES public.lakes(lake_id),
    canonical_source_polygon_id integer NOT NULL,
    duplicate_geometry_sha256 text NOT NULL,
    reconciliation_reason text NOT NULL,
    inventory_version text NOT NULL
);

CREATE INDEX IF NOT EXISTS processing_queue_status_idx
    ON public.processing_queue(processing_status);
CREATE INDEX IF NOT EXISTS environmental_observations_lake_date_idx
    ON public.environmental_observations(lake_id, observation_date DESC);
CREATE INDEX IF NOT EXISTS source_freshness_lake_idx
    ON public.source_freshness(lake_id, source_name);
