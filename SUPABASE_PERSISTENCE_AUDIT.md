# Supabase Persistence Audit

Audit date: 2026-09-13  
Scope respected: no full-inventory ingestion, no pilot sync, no delete/truncate/reset, no fake-data insert, no remote migration apply, and no credential values printed.

## Final verdict: `PARTIALLY_SAVING`

The GLOF pipeline now has an implemented and tested local-first Supabase path: a REAL record is saved to SQLite and its deterministic remote intent is written in the same SQLite transaction. The default runtime remains SQLite/CSV, and the actual Supabase project is not configured or queryable from this workspace. No remote save, remote row count, live idempotency result, pilot reconciliation, RLS state, or advisor result is claimed as verified.

`PARTIALLY_SAVING` means local persistence and the durable remote queue are proven; Supabase persistence is fail-closed and remains unproven until a server-only connection is configured and authorized.

## Connection status

| Check | Result |
|---|---|
| Root `.env`: `SUPABASE_URL` | Absent |
| Root `.env`: `SUPABASE_SECRET_KEY` | Absent |
| Process environment: those two variables | Absent |
| Optional expected project reference | Absent |
| Authenticated table connectivity check | Not run: configuration fails before any network request |
| Supabase MCP access to the intended project | Denied (`permission` error) |
| Remote security/performance advisors | Both denied by the same project permission boundary |
| Current Data API schema exposure setting | Not queryable |

The new `supabase-check` command uses an authenticated `GET` against a selected table (`lakes?select=lake_id&limit=1`), never an unauthenticated OpenAPI-root probe. In the current configuration it returns only missing-variable names; it does not attempt a publishable-key fallback.

## Active persistence mode and implementation

| Mode | Status |
|---|---|
| Normal refresh | SQLite + CSV, unchanged |
| `refresh --queue-supabase --lake-id <id>` | SQLite + CSV + transactional local `sync_outbox` intent; no immediate remote call |
| `sync-supabase --lake-id <id>` | Bounded server-only remote flush after an authenticated connectivity check |
| Full inventory / 8,806-lake bootstrap | Not started and never queued automatically |

`glofguard.supabase_persistence.SupabaseWriter` accepts only root `.env` `SUPABASE_URL` plus a server-only `SUPABASE_SECRET_KEY` using the `sb_secret_` format. It never reads `NEXT_PUBLIC_*` values. Missing or invalid configuration raises a safe error before transport use.

| State | Meaning |
|---|---|
| `LOCAL_SAVED` | SQLite record was committed |
| `REMOTE_PENDING` | Durable retryable outbox intent is due or awaiting retry |
| `REMOTE_SAVED` | Every remote write in the bundle completed and a local receipt was stored |
| `REMOTE_FAILED` | A non-retryable remote failure, such as 401/403 or a missing parent lake, was recorded |

Retryable network/5xx failures remain `REMOTE_PENDING`; they are not reported as remote success. Writes use stable event, run, and observation IDs, immutable conflict targets, bounded batches (at most 25 events and four source rows per observation), and exponential backoff.

The daily writer refuses to invent a static lake. It requires an existing remote `lakes` parent because the approved inventory model requires authoritative polygon/geometry validation fields. It does not auto-sync the 8,806 static lakes or baseline rows.

## Schema status

Existing expected models in `database/postgis_schema.sql` were inspected before creating `supabase/migrations/20260908191916_secure_glof_persistence.sql`. The migration is local and unapplied. It creates no data and must be applied only after the target project, current schema, and Data API exposure are inspected.

| Logical role | Planned schema-qualified name | Remote status |
|---|---|---|
| Lakes | `public.lakes` | Not inspectable |
| Baseline susceptibility | `public.baseline_susceptibility` | Not inspectable |
| Environmental observations | `public.environmental_observations` | Not inspectable |
| Source freshness | `public.source_freshness` | Not inspectable |
| Ingestion runs | `public.ingestion_runs` | Not inspectable |
| Processing queue | `public.processing_queue` | Not inspectable |
| Operator receipt ledger | `private.sync_outbox` | Not inspectable; local SQLite `sync_outbox` is the durable source of truth |

PostGIS is included because the approved static-lake model requires `geometry(Point, 4326)`. The migration fails closed if an existing `lakes` table lacks that geometry contract. It adds primary keys, foreign keys, versioned observation identities, constraints, and indexes. Composite and partial indexes match the expected lake/time/outbox access patterns.

Data API exposure is deliberately not assumed. The migration documents that the operator must explicitly expose the `public` schema before the server-side Data API client can use public tables. `private.sync_outbox` is intentionally not browser-exposed.

## Security and RLS

- `.env` is ignored by Git (`.gitignore` rule verified). `.env.example` has placeholders only for the server-only URL/key/reference.
- The Python adapter is the only privileged writer. The unused Next.js `supabase-server.ts` helper was removed because it could fall back to a browser publishable key.
- The remaining browser client reads only public `NEXT_PUBLIC_SUPABASE_*` variables. No secret/server variable is public-prefixed or present in browser source.
- The proposed migration enables RLS on every listed table, revokes all `PUBLIC`, `anon`, and `authenticated` table privileges, grants only `select`, `insert`, and `update` to `service_role`, creates no public policy, and grants no delete privilege.
- The writer rejects `MOCK` records. Error, outbox, and failure paths redact API-key, authorization, token, password, and URL-credential shapes before persistence or console output.

Actual remote RLS, grants, schema exposure, PostGIS state, and advisor results remain unverified because project access is denied. No RLS setting or policy was changed remotely.

## Controlled one-lake dry run

The safe dry run selected the existing REAL lake `PKGL-00995` with a limit of one. It made no network request, did not create an outbox row, and planned this write order:

1. Authenticated remote lake-parent check
2. Immutable `ingestion_runs` upsert
3. Immutable `environmental_observations` upsert
4. Immutable `source_freshness` upsert with four source-specific rows
5. Ignore-duplicate `processing_queue` upsert
6. `ingestion_runs` completion update

The dry run reported one selected lake and an empty local outbox before and afterward. The standard SQLite record for `PKGL-00995` exists exactly once, is `REAL`, and has local prediction timestamp `2026-09-08T18:58:28.537690+00:00`.

The live one-lake save was not attempted because `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are absent. That prevents an accidental local queue-only result being misrepresented as a remote save.

An explicit non-dry `--queue-existing` attempt also failed before staging a backfill intent; its following dry-run confirmed `0` pending, `0` saved, and `0` failed local outbox rows.

| One-lake/idempotency check | Result |
|---|---|
| Local one-lake dry run | Passed: exactly one existing REAL lake selected |
| Authenticated remote parent/table query | Blocked by absent configuration |
| Remote observation/source/run rows | Not queryable |
| Live duplicate check | Not run; no remote write was authorized/configured |
| Regression duplicate check | Passed: replay uses the same `run_id`/`observation_id` and an in-memory remote retains one observation plus four freshness rows |

## Local versus remote inventory and pilot reconciliation

Remote counts are intentionally not reported as zero: the remote database is unavailable, not known to be empty.

| Dataset / check | Local evidence | Remote Supabase evidence |
|---|---:|---:|
| Active daily SQLite lakes | 8,806 | Not queryable |
| Active daily SQLite records | 8 total; 4 distinct REAL lakes; 0 MOCK | Not queryable |
| Active daily SQLite outbox | 0 pending; 0 saved; 0 failed | Not queryable |
| Map SQLite unique lakes | 8,806 | Not queryable |
| Map SQLite baseline susceptibility | 8,806 distinct `lake_id` values | Not queryable |
| Corrected pilot observations | 100 distinct lakes; 100 REAL; 0 MOCK | Not synced / not queryable |
| Corrected pilot ingestion runs | 1 local map run | Not queryable |
| Latest local map ingestion completion | `2026-09-01T07:38:30.132097+00:00` | Not queryable |

The frozen corrected pilot preserves independent source freshness:

| Local corrected-pilot measure | Count | Remote result |
|---|---:|---|
| Sentinel FRESH / STALE / UNAVAILABLE | 34 / 19 / 47 | Not synced / not queryable |
| Fresh JAXA GSMaP | 100 | Not synced / not queryable |
| Fresh NOAA GFS | 100 | Not synced / not queryable |
| Available NASA POWER baseline | 100 | Not synced / not queryable |
| Pilot lakes present / missing / extra remotely | Not determinable | Not queryable |

The writer maps Sentinel-2, JAXA GSMaP, NOAA GFS, and NASA POWER to four separate `source_freshness` rows. NASA POWER is `NOT_APPLICABLE` for live freshness; no incorrect combined freshness field is used.

## Tests and fixes applied

`python -m unittest discover -s tests -v` passed **27/27** tests. New Supabase-focused coverage includes missing credentials, publishable-key rejection, HTTP 401, bounded retry, stable IDs and duplicate prevention, outbox resume, partial remote failure, source-specific freshness, mock exclusion, non-mutating dry-run preview, and secret-value redaction.

The Next.js production build completed after removal of the unused fallback helper.

## Confirmed blockers and next safe sequence

1. Add only `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and (recommended) `SUPABASE_PROJECT_REF` to the root ignored `.env`; do not put them in `web/.env.local` or any `NEXT_PUBLIC_*` variable.
2. Grant the deployment/audit identity access to the intended Supabase project.
3. Run `supabase-check` to inspect the real schema, table exposure, RLS/grants, migrations, and advisors using authenticated access.
4. Apply the reviewed migration only if inspection confirms the schema is missing or compatible. Do not overwrite an incompatible schema.
5. Run the one-lake REAL sync for `PKGL-00995`, query the remote parent, observation, four freshness rows, queue row, and ingestion run, then repeat it to prove live idempotency.
6. Only after that proof, sync and reconcile the corrected 100-lake pilot. Do not enqueue the 8,806 baseline/static rows without separate approval.

## Summary

| Area | Result |
|---|---|
| Connection status | Blocked safely: no root server configuration and no MCP project access |
| Schema status | Secure additive migration prepared; not applied remotely |
| One-lake saving proof | Local dry run only; live remote proof blocked |
| Idempotency proof | Regression-tested; not live-verified |
| Corrected pilot reconciliation | Local evidence verified; remote reconciliation not run |
| Local versus remote counts | Local counts recorded; remote counts unavailable, not assumed zero |
| RLS/security | Secure migration and Python-only writer implemented; remote enforcement unverified |
| Final verdict | `PARTIALLY_SAVING` |
