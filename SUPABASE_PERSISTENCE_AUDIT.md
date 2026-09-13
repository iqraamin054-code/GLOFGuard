# Supabase Persistence Audit

Audit date: 2026-09-13 (continuation recheck)
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

The new `supabase-check` command uses an authenticated `GET` against a selected table (`lakes?select=*&limit=1`), never an unauthenticated OpenAPI-root probe. In the current configuration it exits **1** with only missing-variable names; it does not attempt a publishable-key fallback. The column-neutral probe also works for `ingestion_runs`, whose expected model has no `lake_id` column. A 404 is reported as missing **or not exposed**, not proof that a table does not exist. This command probes expected names; it does not discover the SQL schema or inspect RLS.

The intended project's frontend URL and configured direct-DB reference matched in the earlier read-only configuration inspection. That is configuration consistency, not proof of an active server connection. The Python process still has no configured URL/key/reference, and MCP project access was denied again on this recheck. Neither the intended server connection nor actual saving can be confirmed.

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
| `REMOTE_SAVED` | Authenticated read-back confirms the observation values, source rows, queue identity and completed run; a local receipt is stored |
| `REMOTE_FAILED` | A non-retryable remote failure, such as 401/403 or a missing parent lake, was recorded |

Retryable network/5xx failures remain `REMOTE_PENDING`; they are not reported as remote success. Writes use stable event, run, and observation IDs, immutable conflict targets, bounded batches (at most 25 events and four source rows per observation), and exponential backoff. The CLI exits nonzero for failed/pending work, including items waiting for backoff or explicit retry. No-op replays do not claim a new write. Refresh output distinguishes saved, unchanged and failed local work.

The writer verifies the outbox payload hash before network activity and rejects conflicting remote values, unexpected duplicates, missing read-back rows and zero-row completion updates. A retry preserves the original source ages and completed-run timestamp; changed source inputs create a separate historical observation. These properties are regression-tested, not yet proven against the real project.

The daily writer refuses to invent a static lake. It requires an existing remote `lakes` parent because the approved inventory model requires authoritative polygon/geometry validation fields. It does not auto-sync the 8,806 static lakes or baseline rows.

## Schema status

Existing expected models in `database/postgis_schema.sql` were inspected. The prior unverified proposal has been moved out of the deployable migrations directory to `supabase/drafts/20260908191916_secure_glof_persistence.sql`. It is **not an approved migration and must not be applied as-is**. Actual remote compatibility has not been checked, so there is no confirmed schema change to deploy. After inspection, generate a proper migration only for verified missing or incompatible schema.

| Logical role | Planned schema-qualified name | Remote status |
|---|---|---|
| Lakes | `public.lakes` | Not inspectable |
| Baseline susceptibility | `public.baseline_susceptibility` | Not inspectable |
| Environmental observations | `public.environmental_observations` | Not inspectable |
| Source freshness | `public.source_freshness` | Not inspectable |
| Ingestion runs | `public.ingestion_runs` | Not inspectable |
| Processing queue | `public.processing_queue` | Not inspectable |
| Operator receipt ledger | `private.sync_outbox` | Not inspectable; local SQLite `sync_outbox` is the durable source of truth |

The draft includes PostGIS because the expected static-lake model uses `geometry(Point, 4326)`, with proposed primary keys, foreign keys, historical identities and indexes. None of this establishes the real remote schema, and existing-table constraint compatibility still needs review.

Data API exposure is deliberately not assumed. Both schema exposure and explicit table grants must be checked; newly created public tables are not necessarily exposed automatically. Grants and RLS are separate controls. Backend-only writes require appropriate `service_role` privileges, not public write grants. `private.sync_outbox` is intentionally not browser-exposed. [Supabase's Data API exposure change](https://github.com/orgs/supabase/discussions/45329).

### Required remote measurements

No actual remote table names have been discovered. The following are logical roles only, not a claim that the planned tables exist. `Unavailable` does not mean zero or empty.

| Logical role | Actual remote name | Rows | Distinct lakes | Earliest/latest timestamps | Duplicate primary IDs | Null lake IDs | RLS |
|---|---|---|---|---|---|---|---|
| Lakes | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |
| Baseline susceptibility | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |
| Environmental observations | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |
| Source freshness | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |
| Ingestion runs | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |
| Processing queue | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |
| Sync outbox | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable | Unavailable |

## Security and RLS

- `.env` is ignored by Git (`.gitignore` rule verified). `.env.example` has placeholders only for the server-only URL/key/reference.
- The Python adapter is the only privileged writer. The unused Next.js `supabase-server.ts` helper was removed because it could fall back to a browser publishable key.
- The remaining browser client reads only public `NEXT_PUBLIC_SUPABASE_*` variables. No secret/server variable is public-prefixed or present in browser source.
- The draft proposes RLS and revocation of `PUBLIC`, `anon`, and `authenticated` table privileges. It grants `select`, `insert`, and `update` to `service_role` and creates no public policy. Existing/default privileges, including any pre-existing delete grant, remain unverified; the draft is not a security attestation.
- The writer rejects `MOCK` records. Error, outbox, and failure paths redact API-key, authorization, token, password, and URL-credential shapes before persistence or console output.
- Redaction also covers quoted JSON/dict credentials and bare JWTs. The adapter validates plain hosted-project HTTPS URLs, rejects embedded credentials/routing components and refuses HTTP redirects, preventing key forwarding to a redirected host. Secret keys are sent in the `apikey` header only, from Python, in line with [Supabase's API-key documentation](https://supabase.com/docs/guides/getting-started/api-keys).

Actual remote RLS, grants, schema exposure, PostGIS state, and advisor results remain unverified because project access is denied. No RLS setting or policy was changed remotely.

## Controlled one-lake dry run

The safe dry run selected the existing REAL lake `PKGL-00995` with a limit of one. It made no network request, did not create an outbox row, and planned this write order:

1. Authenticated remote lake-parent check
2. Immutable `ingestion_runs` upsert
3. Immutable `environmental_observations` upsert
4. Immutable `source_freshness` upsert with four source-specific rows
5. Ignore-duplicate `processing_queue` upsert
6. `ingestion_runs` completion update
7. Authenticated read-back verification before `REMOTE_SAVED`

The dry run reported one selected lake and an empty local outbox before and afterward. The standard SQLite record for `PKGL-00995` exists exactly once, is `REAL`, and has local prediction timestamp `2026-09-08T18:58:28.537690+00:00`.

The continuation ran `python -m glofguard.cli supabase-check` (exit 1: missing required configuration) and `python -m glofguard.cli sync-supabase --lake-id PKGL-00995 --limit 1 --queue-existing --dry-run` (exit 0). The preview uses SQLite read-only mode, including for legacy databases, and makes no schema/data write.

| Measured local state | Before recheck/dry run | After recheck/dry run |
|---|---|---|
| Daily SQLite lakes | 8,806 | 8,806 |
| Daily SQLite observations | 8 | 8 |
| `PKGL-00995` observation rows | 1 | 1 |
| `PKGL-00995` earliest/latest timestamp | `2026-09-08T18:58:28.537690+00:00` | `2026-09-08T18:58:28.537690+00:00` |
| Outbox rows, all statuses | 0 | 0 |
| Remote state | Not queryable | Not queryable |

The live one-lake save was not attempted because `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are absent. That prevents an accidental local queue-only result being misrepresented as a remote save.

An explicit non-dry `--queue-existing` attempt also failed before staging a backfill intent; its following dry-run confirmed `0` pending, `0` saved, and `0` failed local outbox rows.

| One-lake/idempotency check | Result |
|---|---|
| Local one-lake dry run | Passed: exactly one existing REAL lake selected |
| Authenticated remote parent/table query | Blocked by absent configuration |
| Remote observation/source/run rows | Not queryable |
| Live duplicate check | Authorized by the user, but not run because required configuration/access is absent |
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

`python -m unittest discover -s tests -v` passed **52/52** tests in the final continuation run. Supabase-focused coverage includes missing credentials, publishable-key rejection, HTTP 401/403, bounded retry, stable IDs and duplicate prevention, outbox resume, partial remote failure, source-specific freshness, mock exclusion, read-only dry runs and secret-value redaction.

New continuation regressions cover 2xx responses that save no observation, zero-row run completion, conflicting/mocked remote values, duplicate returned rows, a connection lost after server commit, stable ages/completion time on replay, outbox hash corruption, historical versions, small batches, blocked/backoff CLI status and credential-routing rejection. Unit tests use synthetic data and in-memory transports, never remote fake inserts.

Confirmed code defects fixed in this continuation: unchecked write acknowledgements, false-success CLI statuses/exit codes, assumed `lake_id` on run-table probes, mutable retry-time source ages, and incomplete diagnostic redaction. The Next.js helper removal from the previous implementation is retained; no additional frontend development was done.

The remote failure cause remains unknown: no database/API logs, constraints, table names, grants or RLS could be inspected with the current access. This is a configuration/access blocker, not evidence of a database failure or proof of “schema created but data not imported.”

## Confirmed blockers and next safe sequence

1. Add only `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and (recommended) `SUPABASE_PROJECT_REF` to the root ignored `.env`; do not put them in `web/.env.local` or any `NEXT_PUBLIC_*` variable.
2. Grant the deployment/audit identity access to the intended Supabase project.
3. Use MCP or a direct authenticated DB connection to discover actual schemas, columns, keys, counts, timestamps, RLS/grants, migrations and advisors. Use `supabase-check` separately for authenticated Data API table connectivity/exposure probes.
4. Generate/apply a targeted migration only if inspection confirms a schema change is necessary. Do not deploy the draft unchanged or infer that a 404 means missing schema.
5. Run the one-lake REAL sync for `PKGL-00995`, query the remote parent, observation, four freshness rows, queue row and ingestion run, then repeat it to prove live idempotency. If its static parent is missing, review a bounded import of only that lake's authoritative local geometry/static data, not a full-inventory bootstrap.
6. Only after that proof, implement/verify the bounded corrected-pilot export against the actual schema, then sync and reconcile the corrected 100-lake pilot. The daily writer does not yet import the separate corrected-pilot map database. Do not enqueue the 8,806 baseline/static rows without separate approval.

## Summary

| Area | Result |
|---|---|
| Connection status | Blocked safely: no root server configuration and no MCP project access |
| Schema status | Unverified proposal quarantined in drafts; no deployable migration or remote schema change |
| One-lake saving proof | Local dry run only; live remote proof blocked |
| Idempotency proof | Regression-tested; not live-verified |
| Corrected pilot reconciliation | Local evidence verified; remote reconciliation not run |
| Local versus remote counts | Local counts recorded; remote counts unavailable, not assumed zero |
| RLS/security | Python-only writer hardened; proposed RLS/grants not deployed, remote enforcement unverified |
| Final verdict | `PARTIALLY_SAVING` |
