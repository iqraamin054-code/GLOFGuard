# Supabase collaborator setup

This Git branch contains the GLOF Guard Supabase **code, configuration
templates, diagnostics and schema-draft artifacts**. It does not contain a Supabase
project, any database contents, or any API key. A Git branch cannot safely
contain those private resources.

## What collaborators receive from this branch

- `glofguard/supabase_persistence.py`: the Python-only writer. It loads only
  root `.env` server settings and rejects an invalid/mismatched project URL.
- `supabase/diagnostics/inspect_glof_schema.sql`: read-only schema inspection.
- `supabase/drafts/`: reviewed schema drafts. These are **not** an automatic
  migration history and must not be applied to a shared project without review.
- `.env.example` and `web/.env.example`: placeholders only.
- Tests for configuration validation and browser-key separation.

## Use the existing shared project

1. The project owner invites the collaborator in the Supabase Dashboard using
   the project/team access controls. Do not send a secret key through Git,
   chat, email, or a pull request.
2. Each collaborator clones the `ayeshaahmd` branch and creates local files:

   ```powershell
   Copy-Item .env.example .env
   Copy-Item web/.env.example web/.env.local
   ```

3. In root `.env`, the collaborator enters only their authorized server-side
   settings:

   ```text
   SUPABASE_URL=https://your-project-ref.supabase.co
   SUPABASE_SECRET_KEY=sb_secret_your_server_only_key
   SUPABASE_PROJECT_REF=your-project-ref
   ```

4. In `web/.env.local`, enter only public browser configuration:

   ```text
   NEXT_PUBLIC_SUPABASE_URL=https://your-project-ref.supabase.co
   NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_your_browser_key
   ```

`SUPABASE_SECRET_KEY` must never appear in `web/.env.local`, any
`NEXT_PUBLIC_` variable, client code, a commit, or a deployment log. The
publishable key is intentionally browser-visible; RLS and table grants still
control what it can access. The Python writer uses the secret key only from the
secure backend process.

Before onboarding anyone, rotate any server key that has ever been copied into
a chat, issue, screenshot, public repository, or other untrusted location.

## Verify without ingesting or writing data

After the owner has confirmed the correct remote schema, the collaborator can
run the authenticated, read-only connectivity check from the repository root:

```powershell
.\.python\python.exe -m glofguard supabase-check
```

This checks the configured project through an authenticated table query. It is
not a substitute for schema review and must not be followed by a bulk import.
Do not run `refresh --sync-supabase`, `sync-supabase`, a pilot sync, or a full
inventory job unless the project owner has explicitly approved that operation.

## Use a separate Supabase project

Do not point a new project at the production credentials. First review the
schema drafts against the actual required tables, RLS, grants, Data API exposure
and PostGIS needs. Then create a proper reviewed migration and apply it to the
new project. The drafts are kept for inspection; they are not a command to
replace, truncate, disable RLS on, or automatically populate any database.

## Browser key policy

The frontend reads `NEXT_PUBLIC_SUPABASE_URL` and
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` only. The server writer reads
`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and optionally `SUPABASE_PROJECT_REF`.
This matches Supabase's publishable/secret key model: publishable keys belong
in browser code; secret keys belong only in backend processes.
