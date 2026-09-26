-- DRAFT ONLY: not a deployable migration; remote schema inspection is blocked.
-- Review compatibility against the actual database, then generate a proper
-- migration only for confirmed missing/incompatible schema. Do not apply as-is.
-- Proposed secure, additive Supabase schema for the Python local-first sync path.
--
-- This migration creates no inventory rows, starts no ingestion, and creates no
-- anon/authenticated policies. Apply it only after checking the intended remote
-- project, its current schema, and its Data API schema-exposure configuration.
-- The public schema must be deliberately exposed in the Supabase dashboard for
-- the server-side Data API client to query it; RLS/grants below remain separate.

create extension if not exists postgis;
create schema if not exists private;

-- An existing map-import schema must retain the authoritative polygon contract.
-- Fail closed rather than pretending a point-only daily refresh can populate it.
do $$
begin
    if to_regclass('public.lakes') is not null and exists (
        select 1
        from unnest(array[
            'lake_id', 'source_polygon_id', 'location', 'source_geometry_valid',
            'inventory_version'
        ]) as required(column_name)
        where not exists (
            select 1
            from information_schema.columns
            where table_schema = 'public'
              and table_name = 'lakes'
              and column_name = required.column_name
        )
    ) then
        raise exception using message =
            'public.lakes exists but does not match the approved inventory geometry contract';
    end if;
end
$$;

create table if not exists public.lakes (
    lake_id text primary key,
    source_polygon_id integer not null unique,
    latitude double precision not null check (latitude between -90 and 90),
    longitude double precision not null check (longitude between -180 and 180),
    elevation_m double precision,
    reference_area_km2 double precision check (
        reference_area_km2 is null or reference_area_km2 >= 0
    ),
    distance_to_nearest_settlement_km double precision check (
        distance_to_nearest_settlement_km is null
        or distance_to_nearest_settlement_km >= 0
    ),
    location geometry(Point, 4326) not null,
    source_geometry_valid boolean not null,
    source_validation_warning text,
    inventory_version text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.baseline_susceptibility (
    lake_id text primary key references public.lakes(lake_id),
    susceptibility_score double precision check (
        susceptibility_score is null or susceptibility_score between 0 and 100
    ),
    susceptibility_level text check (
        susceptibility_level is null or susceptibility_level in ('Low', 'Medium', 'High')
    ),
    missing_static_fields text,
    interpretation text not null,
    model_version text not null,
    calculated_at timestamptz not null
);

create table if not exists public.ingestion_runs (
    run_id text primary key,
    run_kind text not null,
    evidence_version text,
    source_mode text not null check (source_mode = 'REAL'),
    run_status text not null check (run_status in ('RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED')),
    started_at timestamptz not null,
    completed_at timestamptz,
    exact_command text,
    selected_lake_count integer not null default 0 check (selected_lake_count >= 0),
    successful_lake_count integer not null default 0 check (successful_lake_count >= 0),
    failed_lake_count integer not null default 0 check (failed_lake_count >= 0),
    mock_count integer not null default 0 check (mock_count = 0),
    runtime_seconds double precision check (runtime_seconds is null or runtime_seconds >= 0),
    api_usage jsonb not null default '{}'::jsonb,
    data_version text not null,
    evidence_manifest_sha256 text,
    created_at timestamptz not null default now(),
    check (completed_at is null or completed_at >= started_at)
);

create table if not exists public.environmental_observations (
    observation_id text primary key,
    run_id text not null references public.ingestion_runs(run_id),
    lake_id text not null references public.lakes(lake_id),
    observation_date date not null,
    input_signature text not null,
    source_mode text not null check (source_mode = 'REAL'),
    environmental_conditions_score double precision check (
        environmental_conditions_score is null or environmental_conditions_score between 0 and 100
    ),
    environmental_conditions_level text,
    score_interpretation text not null,
    current_area_km2 double precision check (current_area_km2 is null or current_area_km2 >= 0),
    sentinel_observation_at timestamptz,
    sentinel_cloud_percentage double precision check (
        sentinel_cloud_percentage is null or sentinel_cloud_percentage between 0 and 100
    ),
    current_temperature_c double precision,
    rainfall_last_24h_mm double precision check (
        rainfall_last_24h_mm is null or rainfall_last_24h_mm >= 0
    ),
    rainfall_last_7d_mm double precision check (
        rainfall_last_7d_mm is null or rainfall_last_7d_mm >= 0
    ),
    rainfall_last_30d_mm double precision check (
        rainfall_last_30d_mm is null or rainfall_last_30d_mm >= 0
    ),
    gsmap_observed_at timestamptz,
    forecast_rainfall_next_72h_mm double precision check (
        forecast_rainfall_next_72h_mm is null or forecast_rainfall_next_72h_mm >= 0
    ),
    forecast_temperature_next_24h_c double precision,
    forecast_temperature_next_7d_c double precision,
    gfs_created_at timestamptz,
    nasa_power_baseline_available boolean not null,
    nasa_power_latest_date date,
    confidence text,
    data_completeness_warning text,
    model_version text not null,
    data_version text not null,
    retrieved_at timestamptz not null,
    raw_evidence jsonb not null,
    unique (run_id, lake_id),
    unique (observation_id, lake_id)
);

-- Older approved schemas predate input_signature. The writer supplies it for
-- traceability, while no existing observation is rewritten or deleted here.
alter table if exists public.environmental_observations
    add column if not exists input_signature text;

create table if not exists public.source_freshness (
    observation_id text not null,
    lake_id text not null,
    source_name text not null check (
        source_name in ('SENTINEL_2', 'JAXA_GSMAP', 'NOAA_GFS', 'NASA_POWER')
    ),
    availability_status text not null check (availability_status in ('AVAILABLE', 'UNAVAILABLE')),
    freshness_status text not null check (
        freshness_status in ('FRESH', 'STALE', 'UNAVAILABLE', 'NOT_APPLICABLE')
    ),
    quality_status text,
    source_observation_at timestamptz,
    age_hours double precision check (age_hours is null or age_hours >= 0),
    details jsonb not null default '{}'::jsonb,
    primary key (observation_id, source_name),
    foreign key (observation_id, lake_id)
        references public.environmental_observations(observation_id, lake_id),
    check (source_name <> 'NASA_POWER' or freshness_status = 'NOT_APPLICABLE')
);

create table if not exists public.processing_queue (
    lake_id text primary key references public.lakes(lake_id),
    processing_status text not null check (processing_status in (
        'LIVE_COMPLETE', 'LIVE_PARTIAL', 'LIVE_PENDING', 'SATELLITE_STALE',
        'SATELLITE_UNAVAILABLE', 'PROCESSING_FAILED'
    )),
    approval_required boolean not null default true,
    batch_id text,
    priority integer not null default 0,
    attempt_count integer not null default 0 check (attempt_count >= 0),
    last_attempt_at timestamptz,
    last_successful_run_id text references public.ingestion_runs(run_id),
    last_error text,
    updated_at timestamptz not null default now()
);

-- Local SQLite is the durable source-of-truth outbox. This private table is an
-- optional operator-only receipt ledger; it is never granted to browser roles.
create table if not exists private.sync_outbox (
    event_id text primary key,
    observation_id text not null,
    run_id text not null references public.ingestion_runs(run_id),
    lake_id text not null references public.lakes(lake_id),
    payload_sha256 text not null,
    source_mode text not null check (source_mode = 'REAL'),
    state text not null check (state in ('REMOTE_PENDING', 'REMOTE_SAVED', 'REMOTE_FAILED')),
    attempt_count integer not null default 0 check (attempt_count >= 0),
    next_attempt_at timestamptz,
    last_attempt_at timestamptz,
    last_error_type text,
    last_error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (observation_id),
    unique (run_id)
);

create index if not exists lakes_location_gix on public.lakes using gist(location);
create index if not exists environmental_observations_lake_retrieved_idx
    on public.environmental_observations(lake_id, retrieved_at desc, observation_id desc);
create index if not exists ingestion_runs_completed_idx
    on public.ingestion_runs(completed_at desc, created_at desc);
create index if not exists source_freshness_lake_source_idx
    on public.source_freshness(lake_id, source_name, source_observation_at desc);
create index if not exists processing_queue_work_idx
    on public.processing_queue(processing_status, approval_required, priority desc, updated_at);
create index if not exists sync_outbox_due_idx
    on private.sync_outbox(state, next_attempt_at, created_at)
    where state <> 'REMOTE_SAVED';

alter table public.lakes enable row level security;
alter table public.baseline_susceptibility enable row level security;
alter table public.ingestion_runs enable row level security;
alter table public.environmental_observations enable row level security;
alter table public.source_freshness enable row level security;
alter table public.processing_queue enable row level security;
alter table private.sync_outbox enable row level security;

-- No anon/authenticated/PUBLIC table access and no unrestricted policies.
revoke all on table public.lakes from public, anon, authenticated;
revoke all on table public.baseline_susceptibility from public, anon, authenticated;
revoke all on table public.ingestion_runs from public, anon, authenticated;
revoke all on table public.environmental_observations from public, anon, authenticated;
revoke all on table public.source_freshness from public, anon, authenticated;
revoke all on table public.processing_queue from public, anon, authenticated;
revoke all on table private.sync_outbox from public, anon, authenticated;

grant usage on schema public to service_role;
grant select, insert, update on table public.lakes to service_role;
grant select, insert, update on table public.baseline_susceptibility to service_role;
grant select, insert, update on table public.ingestion_runs to service_role;
grant select, insert, update on table public.environmental_observations to service_role;
grant select, insert, update on table public.source_freshness to service_role;
grant select, insert, update on table public.processing_queue to service_role;
grant usage on schema private to service_role;
grant select, insert, update on table private.sync_outbox to service_role;
