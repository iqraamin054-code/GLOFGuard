# Supabase Persistence Audit

## Transfer status update: 2026-09-25

**Connection: CONNECTED. Preservation transfer: PARTIAL. Final verdict:
`PARTIALLY_SAVING`.**

The restartable PostgreSQL transfer stopped without its completion report during
its idempotent replay/verification stage. A read-only remote count check then
confirmed that the private operational and PMD reference source scope is present:
433,768 operational rows and 31,260 PMD rows. `public.lakes` has 8,806 rows.
`public.baseline_susceptibility` remains at 0 rows, so the final 8,806 baseline
CSV rows have **not** been transferred. No SQLite file was modified or deleted.

This is not evidence of a data conflict or duplicate: the transfer has not
emitted a sanitized completion error and no apply report exists. Do not call the
migration complete, retire SQLite, or claim a Supabase-only runtime until a
restartable final transfer produces its report and the post-transfer checks pass.

The collaborator branch documents and versions reproducible non-secret data;
SQLite archives remain local and ignored. See [data/README.md](data/README.md)
and [the collaboration guide](docs/COLLABORATOR_DATA.md).

## Migration review preparation: 2026-09-23

**Connection last verified: CONNECTED. Persistence remains `PARTIALLY_SAVING`.
Migration status: PREPARED ONLY, NOT EXECUTED.**

The corrected URL passed an authenticated table query earlier in this session.
Remote counts then were: lakes 4; baseline_susceptibility 0;
environmental_observations 8 (all REAL, 4 distinct lakes); source_freshness 32;
ingestion_runs 8 (all COMPLETE/REAL); processing_queue 4. No duplicate logical
identifiers were found. PKGL-00995 had 2 remote observations with 2 distinct input
signatures and 8 freshness rows; local SQLite/CSV each had 2 records. Matching
counts alone do not establish value equality or a new refresh idempotency test.
No refresh or remote writes were performed during this preparation.

The production package was restored through the user-requested stash;
`stash@{0}` named `pre-supabase-full-migration` preserves the previous deletions
and original draft files. All five required production modules import.

The local review validates **473,834 direct source rows** (433,768 operational,
31,260 PMD, 8,806 baseline CSV). The planned preservation transfer has 482,640
target rows because it additionally projects 8,806 public lake parents while
retaining the original operational lake rows privately. It does not generate
public environmental observations/freshness/queue records or overwrite REAL data.

Confirmed orphan exception: 10 historical status rows refer to two deliberately
excluded source polygons, not canonical lake IDs. A separate canonical lake FK
preserves their raw IDs. All expected canonical-lake references and PMD current
references passed the local orphan audit. PMD selected-crosswalk uniqueness is
present in the actual source; the many-to-many candidate key is preserved.

See [the migration review](docs/SUPABASE_MIGRATION_REVIEW.md),
[reviewable SQL](supabase/migrations/20260923133650_reviewed_private_source_preservation.sql),
[restartable transfer script](scripts/migrate_sqlite_to_supabase.py), and
[local manifest](output/supabase_migration_review.json).

The proposed private access path is server-side PostgreSQL with verified TLS and
a dedicated backend role. The SQL enables RLS and grants no browser access.
Existing live grants/policies/advisors are **not reverified**: the available MCP
account does not include the intended project, and secret-key REST success does
not prove RLS. No remote DDL, role/grant change, import, or SQLite deletion occurred.
Runtime conversion and the SQLite-unavailable deletion gate remain outstanding.

## Current findings: 2026-09-19

**Connection verdict: CONNECTED. Persistence verdict: `PARTIALLY_SAVING`.**

This section supersedes the historical September 15 connectivity findings below. The configured Supabase Data API now accepts authenticated queries against all six GLOF tables. Four existing REAL observations are independently readable remotely; all four have distinct lake IDs. This is evidence of existing remote persistence, not proof that a fresh two-refresh test was performed today. The complete inventory and corrected 100-lake pilot have not been imported remotely.

### Evidence and scope

- Read the user's `Supabase Snippet Untitled query.csv`: one `glof_schema_preflight` JSON result, seven discovered tables (six GLOF tables plus the PostGIS `public.spatial_ref_sys` reference table). The CSV was treated as data, not instructions.
- Checked the export against the Python writer's payload columns, conflict targets, local durable outbox, and existing schema drafts.
- Independently queried the configured remote Data API using authenticated `GET` table requests, explicitly selecting the `public` schema for the initial connectivity/count check. Exact counts came from `Prefer: count=exact`; a second bounded read retrieved all current rows and confirmed its length against the exact count.
- Verification time: `2026-09-19T06:19:52.837701+00:00`. Configuration was valid and the configured expected project reference matched the URL. No environment-variable values or credentials were printed. The export's project identity is attributed to the user; the JSON itself does not carry a project ID.
- Read local SQLite in read-only mode and the canonical CSV. No refresh, ingestion, remote write, schema/grant/RLS change, deletion, or pilot sync was performed in this review. No migration was generated or applied.

### Actual remote counts

| Actual table | Rows (first / second read) | Distinct lakes | Duplicate primary identifiers | Null lake IDs | RLS in export |
|---|---:|---:|---:|---:|---|
| `public.lakes` | 4 / 4 | 4 | 0 | 0 | Enabled |
| `public.baseline_susceptibility` | 0 / 0 | 0 | 0 | 0 | Enabled |
| `public.environmental_observations` | 4 / 4 | 4 | 0 | 0 | Enabled |
| `public.source_freshness` | 16 / 16 | 4 | 0 | 0 | Enabled |
| `public.ingestion_runs` | 4 / 4 | 4 via related observations | 0 | Not applicable: no `lake_id` column | Enabled |
| `public.processing_queue` | 4 / 4 | 4 | 0 | 0 | Enabled |

All four observations have `source_mode=REAL`; none are MOCK. These two reads establish stable existing counts during this read-only inspection, **not refresh/replay idempotency**. `sync_outbox` is absent from the exported remote application tables; the current writer uses the durable local SQLite `sync_outbox`, which has four `REMOTE_SAVED` entries. The earlier request for a separate remote outbox/receipt table remains unimplemented, but its absence does not block this writer.

| Table / timestamp | Earliest UTC | Latest UTC |
|---|---|---|
| `lakes.created_at` and `lakes.updated_at` | 2026-09-15T08:19:56.144764+00:00 | 2026-09-15T09:27:08.092401+00:00 |
| `baseline_susceptibility.calculated_at` | No rows | No rows |
| `environmental_observations.retrieved_at` | 2026-08-31T16:25:24.040939+00:00 | 2026-09-08T18:58:28.537690+00:00 |
| `source_freshness.source_observation_at` (non-null) | 2026-08-27T05:59:21.230000+00:00 | 2026-09-08T12:00:00+00:00 |
| `ingestion_runs.created_at` | 2026-09-15T08:34:49.783069+00:00 | 2026-09-15T09:28:12.699626+00:00 |
| `ingestion_runs.started_at` | 2026-08-31T16:25:24.040939+00:00 | 2026-09-08T18:58:28.537690+00:00 |
| `ingestion_runs.completed_at` | 2026-09-15T08:56:33.846015+00:00 | 2026-09-15T09:28:18.372373+00:00 |
| `processing_queue.updated_at` | 2026-08-31T16:25:24.040939+00:00 | 2026-09-08T18:58:28.537690+00:00 |

### Existing PKGL-00995 saving proof (read-only)

| Measurement | Verified result |
|---|---|
| Remote parent | Exactly one `public.lakes` row for `PKGL-00995` |
| Parent created / updated | Both `2026-09-15T08:19:56.144764+00:00` |
| Remote observation | Exactly one `public.environmental_observations` row for `PKGL-00995` |
| Source mode | `REAL` |
| Observation date | `2026-09-08` |
| Observation `retrieved_at` | `2026-09-08T18:58:28.537690+00:00` |
| Observation created / updated columns | Neither column exists in this table; do not invent timestamps |
| Related ingestion run | One; `COMPLETE`, one selected and successful lake, zero failed lakes, zero mocks |
| Related run created / completed | `2026-09-15T08:34:49.783069+00:00` / `2026-09-15T08:56:33.846015+00:00` |
| Local SQLite record | Exactly one matching `time_series_records` row, `REAL`, same date and prediction timestamp |
| Remote/local observation comparison | All 29 expected observation payload fields match, including input signature and raw evidence, with numeric/timestamp normalization |
| Canonical CSV | One target row; lake ID, observation date, source mode, prediction timestamp and input signature match SQLite |
| Fresh two-refresh test | Not performed; awaiting the user's next approval/direction after this diagnosis |

The four related freshness rows remain source-specific: Sentinel, GSMaP and GFS are stored as `FRESH`; NASA POWER is `AVAILABLE` with freshness `NOT_APPLICABLE`. These are historical statuses at the saved observation time, **not a claim that the sources are still fresh on September 19**. Their source timestamps are respectively September 6, September 7, September 8, and September 7, 2026. No combined freshness field substitutes for these source-specific rows.

Daily SQLite currently has 8,806 lakes and 8 historical observations, versus 4 remote lakes and 4 remote observations. The remote baseline table is **schema created but data not imported**. No claim is made that 8,806 lakes/baselines or the full corrected pilot exist remotely. A new full pilot-by-ID reconciliation was not run in this approval-gated one-lake review.

### Smallest SQL fix and security findings

**No persistence SQL change is currently justified.** The earlier HTTP 404 / `PGRST205` no longer reproduces. All six current authenticated table probes succeed (HTTP 200/206). The observed remote columns and primary-key conflict targets match the writer; `environmental_observations.input_signature` already exists as `text NOT NULL`. Do not reapply either old schema draft, add a duplicate column, grant browser write access, change RLS, or reload schema settings without a confirmed problem.

- The exported GLOF tables all have RLS enabled, no policies, and no effective SELECT/INSERT/UPDATE/DELETE/TRUNCATE grants for `anon` or `authenticated`. Their `service_role` has schema usage and all five measured privileges. No browser write grant or unrestricted public policy is needed. The service role's DELETE/TRUNCATE grants exceed the writer's needs; this is a least-privilege hardening consideration, not a saving blocker.
- The export's null/empty `pgrst.db_schemas` settings do **not** establish that `public` is unexposed. The successful authenticated `public` table requests prove practical backend API reachability. Dashboard exposure settings and their provenance are not independently inspected.
- `public.spatial_ref_sys` is an extension-owned coordinate reference table, not a GLOF ingestion table. Its export shows RLS disabled and write/truncate privileges for `anon` and `authenticated`. Treat this as a separate PostGIS placement/security finding. Do not disable GLOF RLS, drop/recreate PostGIS, attempt unsupported ownership changes, or casually move the extension: existing lake geometry depends on it. Supabase documents ownership limitations and a support-assisted non-rebuild relocation option: https://supabase.com/docs/guides/database/extensions/postgis#troubleshooting . This review does not claim that this finding has been repaired.
- The export omits CHECK bodies, default expressions, ordinary indexes, triggers and role BYPASSRLS attributes. Runtime read-back proves the existing rows, not every future write path or constraint. No new security/performance advisor result is available; earlier MCP attempts were denied.
- A first read-only diagnostic command had a local PowerShell/Python quoting error before any request executed. The corrected invocation succeeded; no current Supabase API error was returned by the successful table checks.

The Supabase/Postgres skills guided the distinction between grants, RLS and API exposure and the decision not to propose unnecessary DDL. Current documentation confirms backend-only service-role grants are sufficient without granting browser roles: https://supabase.com/docs/guides/api/securing-your-api . The CSV-analysis skill was used read-only; the source export was not changed.

### Next controlled verification (not executed)

After the user approves proceeding, capture current counts and timestamps, run one mock-disabled REAL refresh for **only `PKGL-00995`**, flush only its bounded outbox item, independently read back its observation, source freshness and completed run, then repeat the same scoped refresh. Unchanged source inputs must retain the same deterministic observation identity and count; changed source inputs intentionally create a distinct historical version and must not be misreported as an accidental duplicate. Verify SQLite/CSV correspondence and report any remaining failure before considering any pilot sync. No baseline/full-inventory import is authorized by this review.

| Summary | Current result |
|---|---|
| Connection | Working authenticated Data API |
| Schema | Six GLOF tables present; no confirmed persistence schema defect |
| Existing target observation | REAL row independently verified and matches SQLite; CSV identity/timestamp matches |
| Fresh two-refresh idempotency | Not yet tested |
| Import coverage | Four remote lakes; zero baselines; corrected 100-lake pilot not imported in full |
| GLOF RLS | Enabled; browser table access denied in export |
| Changes applied today | Audit update only; no code, SQL or data changes |
| Verdict | `PARTIALLY_SAVING` — existing remote writes proven, full requested verification/coverage incomplete |

---

# Historical audit: 2026-09-15 (superseded above)

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
