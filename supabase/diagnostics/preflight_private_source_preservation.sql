-- READ ONLY. Run against the intended project before applying any migration.
-- Any existing target object or glof_backend role requires review: STOP.
-- This file does not change schemas, privileges, rows, or extensions.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30s';

-- Schema existence and complete schema ACLs (NULL means default ACL semantics).
SELECT expected.schema_name, n.oid IS NOT NULL AS exists,
       pg_get_userbyid(n.nspowner) AS owner, n.nspacl AS schema_acl
FROM (VALUES ('internal'), ('reference')) AS expected(schema_name)
LEFT JOIN pg_namespace n ON n.nspname = expected.schema_name;

-- Namespace-dependent objects of every catalog class, including relations,
-- functions, types, operators, collations, and text-search objects.
SELECT n.nspname AS schema_name, d.classid::regclass AS catalog,
       d.objid, d.objsubid,
       pg_describe_object(d.classid, d.objid, d.objsubid) AS object_description
FROM pg_depend d
JOIN pg_namespace n ON d.refclassid = 'pg_namespace'::regclass
                   AND d.refobjid = n.oid
WHERE n.nspname IN ('internal', 'reference')
ORDER BY n.nspname, d.classid, d.objid, d.objsubid;

-- Table-owned objects (constraints/triggers/indexes) for completeness.
SELECT n.nspname, c.relname, c.relkind, c.relacl, c.relrowsecurity,
       c.relforcerowsecurity, pg_get_userbyid(c.relowner) AS owner
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('internal', 'reference') ORDER BY 1, 2;
SELECT n.nspname, c.relname, k.conname, k.contype, k.convalidated,
       pg_get_constraintdef(k.oid) AS definition
FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('internal', 'reference') ORDER BY 1, 2, 3;
SELECT n.nspname, c.relname, t.tgname, t.tgisinternal
FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('internal', 'reference') ORDER BY 1, 2, 3;

-- Role existence, flags, and membership. Never inspect password catalogs.
SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb,
       rolcreaterole, rolinherit
FROM pg_roles WHERE rolname = 'glof_backend';
SELECT parent.rolname AS granted_role, member.rolname AS member,
       m.admin_option
FROM pg_auth_members m JOIN pg_roles parent ON parent.oid = m.roleid
JOIN pg_roles member ON member.oid = m.member
WHERE parent.rolname = 'glof_backend' OR member.rolname = 'glof_backend';

-- Grants/policies including future default ACLs and column-level grants.
SELECT * FROM information_schema.table_privileges
WHERE table_schema IN ('internal', 'reference');
SELECT * FROM information_schema.column_privileges
WHERE table_schema IN ('internal', 'reference');
SELECT * FROM information_schema.routine_privileges
WHERE routine_schema IN ('internal', 'reference');
SELECT * FROM pg_policies WHERE schemaname IN ('internal', 'reference');
SELECT pg_get_userbyid(a.defaclrole) AS owner, n.nspname,
       a.defaclobjtype, a.defaclacl
FROM pg_default_acl a LEFT JOIN pg_namespace n ON n.oid = a.defaclnamespace
WHERE n.nspname IN ('internal', 'reference') OR a.defaclnamespace = 0;

-- Report all relations with a proposed name, even in other schemas.
-- public.lakes is expected; ANY target-schema collision requires STOP.
SELECT n.nspname, c.relname, c.relkind,
       n.nspname IN ('internal', 'reference') AS private_collision_review_required
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = ANY(ARRAY[
 'lakes','official_lakes','inventory_reconciliations','inventory_matches',
 'full_inventory_runs','daily_weather','hourly_precipitation','forecasts',
 'satellite_observations','time_series_records','ingestion_failures',
 'monitoring_snapshots','full_inventory_lake_status','full_inventory_error_events',
 'environmental_source_snapshots','sync_outbox','pmd_2013_source_rows',
 'pmd_2013_lakes','pmd_2013_row_memberships','pmd_2013_current_coverage',
 'pmd_2013_crosswalk','pmd_2013_match_candidates','pmd_2013_metadata'])
ORDER BY 1, 2;

-- Exact public snapshot; rerun immediately before eventual approved execution.
SELECT transaction_timestamp() AS captured_at, 'public.lakes' AS table_name,
       count(*) AS row_count FROM public.lakes
UNION ALL SELECT transaction_timestamp(), 'public.baseline_susceptibility', count(*) FROM public.baseline_susceptibility
UNION ALL SELECT transaction_timestamp(), 'public.environmental_observations', count(*) FROM public.environmental_observations
UNION ALL SELECT transaction_timestamp(), 'public.source_freshness', count(*) FROM public.source_freshness
UNION ALL SELECT transaction_timestamp(), 'public.ingestion_runs', count(*) FROM public.ingestion_runs
UNION ALL SELECT transaction_timestamp(), 'public.processing_queue', count(*) FROM public.processing_queue
ORDER BY table_name;
COMMIT;
