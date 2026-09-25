# Supabase migration review — 2026-09-23

Status: PREPARED FOR REVIEW; no remote SQL or transfer executed. Runtime still uses SQLite.

## Preserved work and restored package

`git stash push -u -m "pre-supabase-full-migration"` preserved the 26 local source
deletions and both original untracked migration drafts. The stash remains intact.
Do not pop it blindly: doing so would reapply the production-package deletions.
All five modules import successfully: `glofguard.cli`, `storage`, `pipeline`,
`full_inventory`, and `supabase_persistence`. Git HEAD supplied the restored files.
The replacement preparation files are uncommitted. No SQLite source was edited,
renamed, deleted, vacuumed, checkpointed, or migrated.

## Accounting and scope

| Direct source | Rows |
|---|---:|
| Operational SQLite, all 16 tables | 433,768 |
| PMD SQLite, all 7 tables | 31,260 |
| Baseline CSV | 8,806 |
| Direct source scope | **473,834** |

The prepared transfer preserves all 16 operational tables in `internal`, including
`internal.lakes` (which retains operational names and original timestamps) and
`internal.time_series_records` (which retains full record JSON and original keys).
It preserves all seven PMD tables under their original names in `reference`.
It separately projects the 8,806 reconciled map parents into existing `public.lakes`,
then projects the baseline CSV into existing `public.baseline_susceptibility`.
The resulting planned target-row count is **482,640**, not a promised remote total.
Existing remote rows, concurrent writers, and conflicts must be reconciled.

`internal.lakes` and `internal.time_series_records` are strictly source-preservation
and audit mirrors. They are not canonical runtime models. `public.lakes` remains
the canonical lake inventory and `public.environmental_observations` remains the
canonical environmental observation model. Future runtime conversion must not
redirect canonical reads/writes to these mirrors. Other private operational tables
may support provider history, queues, or reconciliation under their defined roles.

The map database supplies public lake geometry metadata and the baseline model
version/calculation timestamp. Baseline scores, levels, missing fields, and
interpretation are checked by lake ID against CSV. Original CSV remains intact,
including its latitude/longitude and safety-notice columns. No CSV deletion is proposed.
Map evidence tables and model training/test split files are outside this operational
transfer and remain separate retained artifacts.

This transfer does **not** synthesize new public observations, ingestion runs,
source freshness, or current queue entries. It preserves historical records and
the outbox privately, leaving existing REAL public measurements untouched. Future
publication must reuse the existing writer's deterministic identities and per-source
freshness mapping. Historical/mock-marked records retain their original flags in
private history; they are never promoted to REAL public measurements.

## Foreign-key review

All lake-reference columns were audited against the 8,806 operational parent IDs.
The detailed machine-readable report lists orphan keys per column.

| Table / column | Finding and constraint |
|---|---|
| internal.lakes.lake_id | 8,806 parents; FK to public.lakes |
| daily_weather, hourly_precipitation, forecasts, satellite_observations, time_series_records, ingestion_failures, environmental_source_snapshots, full_inventory_error_events, sync_outbox: lake_id | No orphan keys; FK to public.lakes |
| full_inventory_lake_status.lake_id | 10 source-status rows across 5 runs use 2 excluded source polygon IDs; preserve raw identifiers without a public-lake FK |
| full_inventory_lake_status.canonical_lake_id | New mapped column, NOT NULL FK to public.lakes |
| official_lakes.lake_id / matched_source_lake_id | External official inventory and source matching namespaces; currently empty, no public-lake FK assumed |
| inventory_matches source_lake_id / source_system_lake_id / official_lake_id / duplicate_of_source_lake_id | Historical reconciliation evidence, potentially unmatched source IDs; currently empty, no public-lake FK assumed |
| sync_outbox.observation_id / run_id | Intentionally no FK to public observations/runs: pending events must exist before publication |
| reference.pmd_2013_current_coverage.lake_id | All 8,806 are canonical IDs; FK to public.lakes |
| PMD crosswalk/candidate current_lake_id | FK to current coverage, which itself references public.lakes |
| PMD original IDs | PMD namespace, intentionally not a public PKGL FK |

The two historical mappings are independently confirmed by saved status JSON and
source reconciliation evidence:

- `SOURCE-POLYGON-06960` → `PKGL-06121`
- `SOURCE-POLYGON-06961` → `PKGL-06122`

The transformation requires `EXCLUDED_DUPLICATE` status and matching saved evidence;
unknown identifiers fail the dry run. Both historical run-ID references have zero
orphans. Existing reconciliation and PMD foreign keys pass SQLite foreign_key_check.

Foreign keys are created on empty private tables and immediately enforce every
insert. Transfer order inserts/validates public parents before private child rows;
there is no interval where imported child rows bypass constraints. Supporting
indexes cover canonical lake and run references. No CASCADE deletion is added.

## Actual PMD constraints

| Table | Rows | Preserved primary key |
|---|---:|---|
| pmd_2013_source_rows | 3,080 | source_row |
| pmd_2013_lakes | 3,044 | pmd_id; UNIQUE(basin, original_pmd_lake_id) |
| pmd_2013_row_memberships | 3,252 | source_row, pmd_id |
| pmd_2013_current_coverage | 8,806 | lake_id |
| pmd_2013_crosswalk | 3,044 | pmd_id; nullable UNIQUE(current_lake_id) |
| pmd_2013_match_candidates | 10,033 | pmd_id, current_lake_id |
| pmd_2013_metadata | 1 | key |

Crosswalk uniqueness is **already in the actual source database**. Its 1,945
non-null proposed matches are unique. Remaining statuses are 658 ambiguous spatial
candidates, 406 without a candidate, and 35 requiring PMD geometry review. Their
null current IDs remain valid. The 10,033 candidate rows are many-to-many: no
uniqueness on either candidate ID alone is added. All membership/source rows remain.
Basin counts verified: Swat 214, Chitral 116, Gilgit 660, Hunza 216, Shigar 110,
Shyok 270, Indus 815, Shingo 247, Astore 196, Jhelum 200.

## Secure runtime access decision

Use **server-side PostgreSQL** for `internal` and `reference`, through a direct
connection or Supabase session pooler, with TLS `verify-full` and the appropriate
trusted CA certificate. These schemas need no Data API exposure. A Python process
uses a dedicated login role that is a member of `glof_backend`; the migration
creates only the NOLOGIN, NOBYPASSRLS group role, never a password or login.
Login provisioning/membership remains an administrator step before runtime cutover.

The SQL grants only SELECT/INSERT/UPDATE on named private tables and schema USAGE
to that backend role. It enables RLS with a role-specific policy and revokes schema
and table access from PUBLIC, anon, and authenticated. No public write policy,
SECURITY DEFINER function, RLS disable, or PostGIS change is included. New tables
have no browser grants. Existing public permissions are unchanged by this file.

The current REST writer addresses public tables; it does not send schema-profile
headers. Merely prefixing `internal.` onto a REST table path would not enable custom
schema access. A REST alternative would require explicit Data API schema exposure,
schema USAGE, table privileges, and Accept-Profile (reads)/Content-Profile (writes).
That alternative is not selected here. Existing public observation publication can
continue via the server-only secret key while the Repository implementation is
converted to PostgreSQL. Cross-connection publishing must retain outbox/retry
semantics; it is not one atomic transaction with the REST call.

The transfer uses a separately configured server-only
`GLOF_MIGRATION_DATABASE_URL`; **an sb_secret key is not a PostgreSQL password**.
It verifies the DSN host/user project identity against SUPABASE_PROJECT_REF and
forces TLS verification. Credentials must be kept locally in ignored .env or a
secret store, never command-line literals or committed files. Migration access and
the future runtime account should be separate; do not run production with postgres.

References: [Supabase PostgreSQL connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[verified TLS connection](https://supabase.com/docs/guides/database/psql),
[custom Data API schemas](https://supabase.com/docs/guides/api/using-custom-schemas).

## Files prepared and execution gates

- `supabase/migrations/20260923133650_reviewed_private_source_preservation.sql`:
  actual transactional DDL, generated as a migration through the CLI. It deliberately
  fails if a destination table or group role already exists, instead of masking an
  incompatible schema with CREATE TABLE IF NOT EXISTS. Inspect existing custom schemas
  first. It creates 23 private tables and one backend group role; no data inserts.
- `scripts/migrate_sqlite_to_supabase.py`: defaults to a local-only dry run, opens
  all SQLite sources in immutable/read-only mode, rejects nonempty WAL, verifies file
  hashes before/after preparation, and produces an approval manifest.
- `output/supabase_migration_review.json`: exact counts, keys, source checksums,
  source-file hashes, script/DDL hashes, orphan audit, PMD validation and planned targets.
- `requirements-migration.txt`: pinned optional psycopg driver.
- `tests/test_migration_review.py`: local safety and transfer-control tests.

Safe command already run:

```powershell
.\.python\python.exe scripts/migrate_sqlite_to_supabase.py
```

After SQL approval/application and review of the generated manifest, the transfer
would require both `--apply` and `--approved-manifest output/supabase_migration_review.json`,
with a **different report path**. Do not run apply during this review stage.
Stop local ingestion processes while taking/reviewing the source snapshot. Any source,
DDL or transfer-script change invalidates the approved manifest.

The script validates all destination tables/RLS/columns and private browser-schema
isolation before its first insert. Batches default to 250 (maximum 1,000). Each batch
is transactional, compares all transferred fields before and after insert, and stops
on differing existing rows. It never updates conflicting rows or deletes anything.
Retries for serialization/deadlocks use bounded exponential delays. After a connection
loss or partial transfer, rerun with the same manifest: matching saved rows are skipped
and missing rows inserted. No blind progress offset can skip a failed batch. Full
private-table hashes and row counts are reconciled at table completion; target extras
cause a failure. Public existing-row timestamps/geometry are retained, with existing
geometry validation still required before deletion/cutover approval.

## Remaining proof before execution and retirement

Live public REST connectivity was confirmed previously, with 4 lakes, 0 baselines,
8 REAL observations, 32 freshness rows, 8 COMPLETE runs and 4 queue rows. Those are
an earlier snapshot, not counts after a migration. The MCP account available in this
session cannot access GLOF; current SQL catalog policies/grants/advisors cannot be
certified through it. Run the existing read-only `supabase/diagnostics/inspect_glof_schema.sql`
in the correct project's SQL Editor, or configure the intended PostgreSQL connection
for a read-only review before applying DDL. Do not infer RLS from secret-key REST success.

No local PostgreSQL server is available for real SQL execution testing. Unit tests
exercise transaction/control behavior with a fake cursor; they are not proof of
PostgreSQL integration, successful live transfer, or production SQLite independence.

Runtime conversion is a separate next phase: implement Repository parity using the
private PostgreSQL tables, transactional retry/outbox behavior, public projection,
monitoring/CLI state, PMD reads, and web routes that currently read local CSV. Preserve
original source histories and source-specific freshness. Keep the existing REAL refresh
implementation and reuse stable observation identities.

Retirement remains blocked until source/target counts, keys and hashes reconcile,
the entire test suite passes with SQLite unavailable, the app starts without SQLite,
one mock-disabled REAL refresh persists automatically, retry/restart and duplicate
prevention are verified, and PMD reads work. Keep a verified rollback backup. No
SQLite, CSV, raw PMD archive, model split file, or production code is approved for
deletion by this preparation step.
