# Supabase Persistence Audit

Audit date: 2026-09-15 (post-reconnection, replacement-key preflight)
Scope respected: no full-inventory ingestion, no pilot sync, no delete/truncate/reset, no fake-data insert, no remote migration apply, and no credential values printed.

## Final verdict: `PARTIALLY_SAVING`

The Python backend is now configured and Supabase accepts its server key. However, the authenticated `public.lakes` table query returns HTTP 404 / `PGRST205`, and authenticated exposed-schema discovery returns HTTP 200 with no table paths or definitions. Authentication is working; usable GLOF tables and remote saving are not verified. The default runtime remains SQLite/CSV. No remote save, remote row count, live idempotency result, pilot reconciliation, RLS state, or advisor result is claimed as verified.

`PARTIALLY_SAVING` means local persistence and the durable remote queue are proven; Supabase persistence remains blocked by unavailable GLOF tables through the Data API. This does not mean any lake has been saved remotely.

## Connection status

| Check | Result |
|---|---|
| Root `.env`: `SUPABASE_URL` | Present |
| Root `.env`: `SUPABASE_SECRET_KEY` | Present; user reports replacement key installed; authenticated requests accepted |
| Python process loading of those two variables | Confirmed by the authenticated request |
| Expected project reference | Present and matches URL; URL also matches existing frontend project |
| Authenticated table connectivity check | HTTP 404 / `PGRST205`: `public.lakes` not found in schema cache |
| Authenticated exposed-schema discovery | HTTP 200; zero table paths and zero definitions visible |
| Supabase MCP access to the intended project | `get_project` and `list_tables` still denied after reported reconnection |
| Reconnected MCP project listing | Succeeds with 2 visible projects; configured GLOF project is not among them |
| Direct Postgres schema query | Connection failed with DNS `ENOTFOUND`; query never ran |
| Remote security/performance advisors | Last attempted September 13: both denied; project access remains denied |
| Current Data API exposure configuration and grants | Not inspectable through available admin connections |

The table probe uses authenticated `GET /rest/v1/lakes?select=*&limit=1`, never an unauthenticated OpenAPI-root connectivity probe. After inspecting its `PGRST205` error, a separate authenticated OpenAPI request was used solely to discover exposed-schema metadata; its empty response is not evidence that the actual SQL database contains no tables. A 404 can mean missing schema, missing grants, missing exposure or an out-of-date schema cache. SQL/admin access is needed to distinguish these safely. No publishable-key fallback was used.

The settings had been entered in the tracked `.env.example`, which is not the runtime configuration file. Only the intended settings were relocated into ignored root `.env`, preserving other runtime settings; `.env.example` was restored to placeholders. The backend URL was checked against the existing frontend project, and the direct DB host was checked against that same reference, without printing values. An initial Windows socket denial (`WinError 10013`) was a sandbox restriction; the approved read-only network retry reached Supabase and produced the responses above.

### Latest requested `PKGL-00995` test: stopped at preflight

After the user reported reconnection and a replacement secret, configuration was checked again without printing any values. Required server variables are present, the expected/project/frontend identities agree, and REAL mode with mock disabled is configured. Fresh authenticated requests again returned HTTP 404 / `PGRST205` for the expected `public.lakes` API route and HTTP 200 with no exposed table paths/definitions for metadata discovery.

The current MCP identity can list two projects but cannot see the configured target. A separate direct Postgres connection was checked against the same project and failed DNS resolution (`ENOTFOUND`) before any SQL executed. A replacement Data API secret does not change the MCP account's project permissions.

| Requested evidence | Before preflight | After preflight |
|---|---|---|
| Target lake | `PKGL-00995` | `PKGL-00995` |
| Local SQLite table | `time_series_records` | Unchanged |
| Local target-lake row count | 1 | 1 |
| Local `source_mode` | `REAL` | `REAL` |
| Local observation date | `2026-09-08` | `2026-09-08` |
| Local observation/prediction timestamp | `2026-09-08T18:58:28.537690+00:00` | Unchanged; not a new refresh |
| Local `created_at` / `updated_at` columns | Not present on this SQLite table | Not present |
| Local observations, all lakes | 8 | 8 |
| Local outbox rows | 0 | 0 |
| Actual remote table name | Not discoverable; `public.lakes` is only the expected probed name | Not discoverable |
| Remote lake row / source mode / timestamps | Unavailable | Unavailable |
| Remote row counts / duplicate check | Unavailable, not assumed zero | Unavailable |
| Independent remote versus SQLite/CSV comparison | Not possible without a remote row | Not performed |

No REAL refresh, outbox enqueue, Supabase insert/upsert, second refresh, pilot sync or inventory ingestion was started in this attempt. The old local row is not being presented as new saving proof.

**Database change approval:** no table/migration/grant/RLS change is proposed or applied yet, because the actual SQL schema is unknown. A 404 alone does not establish which SQL change is needed. The existing draft remains unapplied. Once real metadata is available, any required change will be presented as exact proposed SQL with its impact before execution, as requested.

**Concrete next step:** run `supabase/diagnostics/inspect_glof_schema.sql` in the intended project's SQL Editor and provide the result, or repair MCP access to that same project. This is a read-only transaction with a 15-second statement timeout. It reads application table/column/key metadata, effective `anon`/`authenticated`/`service_role` grants, RLS flags and policy metadata, plus API-schema settings when visible. It returns no lake rows, credentials, environment variables or function bodies and changes no schema, policy or data. It has not been executed remotely. Policy metadata is not a complete evaluation of arbitrary policy expressions; deeper review may still be needed.

### Persistence-code preflight findings

- Runtime config comes from root `.env`, never `.env.example`; existing process variables take precedence. The writer validates the hosted URL and secret-key format, uses the `apikey` header from Python only, and refuses redirects.
- The explicit one-lake REAL refresh command queues newly saved records locally; it does not contact Supabase. A separate bounded `sync-supabase --lake-id PKGL-00995 --limit 1` performs the remote writes and read-back checks. Mock plus remote queue is rejected.
- The input signature excludes the prediction timestamp. Identical source-derived inputs on the same lake/date are a no-op. A genuine input/source/freshness change creates a new historical version rather than an accidental duplicate; the eventual test must compare signatures as well as counts.
- The canonical CSV contains the latest local revision per lake/date; the SQLite history retains prior versions. The daily writer requires an existing authoritative remote lake parent and will not fabricate it.
- The writer expects an `environmental_observations.input_signature` column, while the older local `database/postgis_schema.sql` omits it. This is a local schema-contract discrepancy to check against the actual remote database, not proof that the remote column is missing. No SQL was applied to address it.
- An A-to-B-to-A same-day local signature sequence can hit the existing SQLite primary-key constraint because only the latest signature is compared before insert. This is a code-review finding, not an observed result of the requested live test; no live test ran.

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
| Lakes | `public.lakes` | Expected name probed: HTTP 404 / `PGRST205`; actual SQL existence unverified |
| Baseline susceptibility | `public.baseline_susceptibility` | Not inspectable |
| Environmental observations | `public.environmental_observations` | Not inspectable |
| Source freshness | `public.source_freshness` | Not inspectable |
| Ingestion runs | `public.ingestion_runs` | Not inspectable |
| Processing queue | `public.processing_queue` | Not inspectable |
| Operator receipt ledger | `private.sync_outbox` | Not inspectable; local SQLite `sync_outbox` is the durable source of truth |

The draft includes PostGIS because the expected static-lake model uses `geometry(Point, 4326)`, with proposed primary keys, foreign keys, historical identities and indexes. None of this establishes the real remote schema, and existing-table constraint compatibility still needs review.

Data API exposure is deliberately not assumed. Both schema exposure and explicit table grants must be checked; newly created public tables are not necessarily exposed automatically. Grants and RLS are separate controls. Backend-only writes require appropriate `service_role` privileges, not public write grants. `private.sync_outbox` is intentionally not browser-exposed. [Supabase's Data API exposure change](https://github.com/orgs/supabase/discussions/45329).

### Required remote measurements

No actual remote SQL table names have been discovered. Zero tables are visible in the authenticated API metadata, but SQL tables may exist outside its exposed/granted schema. The following are logical roles only, not a claim that the planned tables exist. `Unavailable` does not mean zero or empty.

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
- A secret was previously supplied in chat and temporarily stored in the tracked template. It was removed from the working template and was not committed by this agent. The user now reports installing a replacement in `.env`; its format and authentication were checked, but retirement of the old key was not independently inspected. No credential values were printed during relocation or verification.
- The Python adapter is the only privileged writer. The unused Next.js `supabase-server.ts` helper was removed because it could fall back to a browser publishable key.
- The remaining browser client reads only public `NEXT_PUBLIC_SUPABASE_*` variables. No secret/server variable is public-prefixed or present in browser source.
- The draft proposes RLS and revocation of `PUBLIC`, `anon`, and `authenticated` table privileges. It grants `select`, `insert`, and `update` to `service_role` and creates no public policy. Existing/default privileges, including any pre-existing delete grant, remain unverified; the draft is not a security attestation.
- The writer rejects `MOCK` records. Error, outbox, and failure paths redact API-key, authorization, token, password, and URL-credential shapes before persistence or console output.
- Redaction also covers quoted JSON/dict credentials and bare JWTs. The adapter validates plain hosted-project HTTPS URLs, rejects embedded credentials/routing components and refuses HTTP redirects, preventing key forwarding to a redirected host. Secret keys are sent in the `apikey` header only, from Python, in line with [Supabase's API-key documentation](https://supabase.com/docs/guides/getting-started/api-keys).

Actual remote RLS, grants, schema exposure, PostGIS state, and advisor results remain unverified because project access is denied. No RLS setting or policy was changed remotely.

## Controlled one-lake dry run (September 13 evidence)

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

The live one-lake save was not attempted on September 13 because configuration was absent. On September 15 configuration is present, but the expected remote lake table is unavailable. No fresh Earth Engine run, remote write, pilot sync or bulk import was launched during this configuration check.

An explicit non-dry `--queue-existing` attempt also failed before staging a backfill intent; its following dry-run confirmed `0` pending, `0` saved, and `0` failed local outbox rows.

| One-lake/idempotency check | Result |
|---|---|
| Local one-lake dry run | Passed: exactly one existing REAL lake selected |
| Authenticated remote parent/table query | September 15: table query reached Supabase but returned `PGRST205` |
| Remote observation/source/run rows | Not queryable |
| Live duplicate check | Authorized by the user, but not run because remote GLOF schema is inaccessible |
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

`python -m unittest discover -s tests -v` passed **54/54** tests on September 15. Two new template-safety tests enforce placeholder-only Supabase settings and reject non-placeholder secret tokens without printing matched values. Supabase-focused coverage also includes missing credentials, publishable-key rejection, HTTP 401/403, bounded retry, stable IDs and duplicate prevention, outbox resume, partial remote failure, source-specific freshness, mock exclusion, read-only dry runs and secret-value redaction.

The September 15 read-only local recheck still found 8,806 lakes, 8 daily observations, exactly 1 observation for `PKGL-00995`, and 0 outbox rows. No database row counts changed as part of the configuration relocation and network checks.

New continuation regressions cover 2xx responses that save no observation, zero-row run completion, conflicting/mocked remote values, duplicate returned rows, a connection lost after server commit, stable ages/completion time on replay, outbox hash corruption, historical versions, small batches, blocked/backoff CLI status and credential-routing rejection. Unit tests use synthetic data and in-memory transports, never remote fake inserts.

Confirmed code defects fixed in this continuation: unchecked write acknowledgements, false-success CLI statuses/exit codes, assumed `lake_id` on run-table probes, mutable retry-time source ages, and incomplete diagnostic redaction. The Next.js helper removal from the previous implementation is retained; no additional frontend development was done.

The remaining remote failure cause is not established: no database/API logs, actual SQL tables, grants, constraints or RLS could be inspected with current admin access. The confirmed API symptom is `PGRST205` and no exposed tables in authenticated metadata. This is not proof of a database failure or of “schema created but data not imported.” No migration was applied based on the 404 alone.

## Confirmed blockers and next safe sequence

1. Keep the reported replacement secret only in ignored root `.env`, retain template placeholders, and ensure the previously exposed key is retired. No additional credential values are needed in chat.
2. Run the read-only diagnostic SQL in the intended project's SQL Editor and return metadata, or grant/reconnect the audit identity to that same project. The current identity lists two other projects, and the existing direct hostname does not resolve from this environment.
3. Use MCP or a direct authenticated DB connection to discover actual schemas, columns, keys, counts, timestamps, RLS/grants, migrations and advisors. Use `supabase-check` separately for authenticated Data API table connectivity/exposure probes.
4. Generate/apply a targeted migration only if inspection confirms a schema change is necessary. Do not deploy the draft unchanged or infer that a 404 means missing schema.
5. Run the one-lake REAL sync for `PKGL-00995`, query the remote parent, observation, four freshness rows, queue row and ingestion run, then repeat it to prove live idempotency. If its static parent is missing, review a bounded import of only that lake's authoritative local geometry/static data, not a full-inventory bootstrap.
6. Only after that proof, implement/verify the bounded corrected-pilot export against the actual schema, then sync and reconcile the corrected 100-lake pilot. The daily writer does not yet import the separate corrected-pilot map database. Do not enqueue the 8,806 baseline/static rows without separate approval.

## Summary

| Area | Result |
|---|---|
| Connection status | Backend authentication works; GLOF table query fails `PGRST205`; SQL/MCP inspection blocked |
| Schema status | Unverified proposal quarantined in drafts; no deployable migration or remote schema change |
| One-lake saving proof | Local dry run only; no exposed GLOF table available for live proof |
| Idempotency proof | Regression-tested; not live-verified |
| Corrected pilot reconciliation | Local evidence verified; remote reconciliation not run |
| Local versus remote counts | Local counts recorded; remote counts unavailable, not assumed zero |
| RLS/security | Python-only writer hardened; proposed RLS/grants not deployed, remote enforcement unverified |
| Final verdict | `PARTIALLY_SAVING` |
