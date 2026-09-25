# Remote schema/security gate — 2026-09-23

Overall: **FAIL — execution prerequisites missing; remote checks NOT RUN.**
This does not assert that the live database failed a security check.

Latest local recheck: 52 migration/persistence/CLI/PMD tests pass. The corrected
REAL constraint remains present; no quoted DOUBLE PRECISION, BIGINT, BOOLEAN,
TIMESTAMPTZ, or JSONB business literals were found. Added regression coverage
for accidental literal conversion and destructive statements. Regenerated the
manifest after documenting canonical public models in SQL comments.

`internal.lakes` and `internal.time_series_records` are strictly preservation/audit
tables; canonical models remain `public.lakes` and `public.environmental_observations`.

Prepared `supabase/diagnostics/preflight_private_source_preservation.sql` for the
requested live read-only catalog inventory and six exact public table counts.
It has not been run remotely. PostgreSQL credentials are still absent; collision
absence and current counts remain unverified. Any existing target object or backend
role must stop application for review. Capture counts again immediately before
eventual approved execution; earlier snapshots are not a fresh pre-migration baseline.

Reviewed file: `supabase/migrations/20260923133650_reviewed_private_source_preservation.sql`.
No remote SQL, transfer, provider refresh, role provisioning, or SQLite retirement
was performed at this gate.

## Local correction

The final review found that the previous generated migration still required
`source_mode = 'DOUBLE PRECISION'` for outbox rows. Corrected the literal to `REAL`.
The type-conversion error was introduced during prior preparation; the former
tests did not catch it. Added a regression test and regenerated the local manifest
to bind approval to the corrected SQL. The original file checksum is superseded.

## Results

| Check | Result | Evidence / limitation |
|---|---|---|
| Static DROP/TRUNCATE/data DELETE scan | PASS | None in executable SQL |
| Destructive ALTER scan | PASS | Only private-table RLS enables and FK additions |
| Canonical public data overwrite | PASS (static) | No public DDL or DML; foreign keys reference public.lakes |
| Expected tables | PASS (static) | 16 internal + 7 reference = 23 |
| REAL outbox constraint | PASS (corrected) | CHECK(source_mode = 'REAL'); regression test added |
| Local migration safety tests | PASS | 12 tests |
| Source scope / manifest regeneration | PASS | Local dry run; 473,834 direct source rows; 482,640 planned targets |
| Authenticated PostgreSQL connection/TLS | FAIL — NOT RUN | No migration DSN, backend DSN, or PostgreSQL environment credentials configured |
| Intended-project MCP access | FAIL — NOT RUN | Configured project not in connected account's project list |
| Apply reviewed schema/security SQL | FAIL — NOT RUN | Blocked before execution by connection prerequisites |
| Live schemas and expected tables | FAIL — UNVERIFIED | Requires intended-project SQL catalog access |
| Live PK/FK/unique constraints and indexes | FAIL — UNVERIFIED | Requires intended-project SQL catalog access |
| Live PostGIS geometry type/SRID | FAIL — UNVERIFIED | No extension changes proposed; existing catalog still needs inspection |
| Preserved excluded source IDs + canonical FK | PASS (local design only) | 10 historical rows, 2 source IDs; live insertion test not run |
| PMD selected/candidate matching constraints | PASS (local design only) | Mirrors actual source keys; live catalog not checked |
| Restricted backend SELECT/INSERT/UPDATE | FAIL — UNVERIFIED | glof_backend is a proposed NOLOGIN group, not a configured login |
| Backend destructive privileges denied | FAIL — UNVERIFIED | Proposed table grants omit DELETE/TRUNCATE; effective live privileges not tested |
| anon/authenticated private access denied | FAIL — UNVERIFIED | Proposed revocations/RLS present; effective live grants not tested |
| Browser API private-schema access denied | FAIL — UNVERIFIED | No live browser-key probes performed at this gate |
| Provider/outbox/PMD transactional integration | FAIL — NOT RUN | Requires migrated schema and restricted backend connection |
| FK enforcement and rollback count equality | FAIL — NOT RUN | No transaction was started remotely |
| Live schema vs approval manifest | FAIL — UNVERIFIED | Local manifest regenerated; live schema unavailable |

## Required connection prerequisite

Configure `GLOF_MIGRATION_DATABASE_URL` locally in ignored `.env` using the correct
project's direct PostgreSQL or session-pooler connection from Supabase Connect.
Use database credentials, not a publishable/secret API key. TLS must use verify-full
and a trusted CA (sslrootcert as required). Never paste credentials in chat or Git.

Before applying SQL, use that connection read-only to verify project identity,
existing private schemas/objects, existing glof_backend role, effective privileges,
and baseline public counts. Schema revocations could affect existing objects, and
CREATE ROLE/TABLE will fail if the named objects already exist. Do not bypass a
collision or rerun partial DDL without inspecting catalog state.

The approved schema creates only a NOLOGIN group role. A separate securely
provisioned restricted login/member connection is needed to prove actual backend
TLS and runtime permissions. An administrator SET ROLE test proves role behavior,
but must not be reported as proof that a dedicated backend login works.

Private schemas do not need Data API exposure for this PostgreSQL transfer design.
The existing REST secret is insufficient for PostgreSQL DDL/catalog execution.

After prerequisites are available, apply only the reviewed schema SQL, inspect
catalogs, and run the explicitly authorized representative inserts/upserts within
a transaction that always ROLLBACKs. Compare exact pre/post counts. Do not invoke
the bulk transfer or start the runtime refactor during this gate.
