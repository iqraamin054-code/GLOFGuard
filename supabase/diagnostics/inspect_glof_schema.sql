-- READ-ONLY DIAGNOSTIC, NOT A MIGRATION.
-- Run in the intended GLOF project's Supabase SQL Editor if MCP cannot access it.
-- Reads application-table metadata only: no lake records, passwords, API keys,
-- environment values, or function bodies. No grants/policies/schema are changed.
-- A null API-schema setting means it is not visible to this SQL session; it
-- does not prove the Data API is disabled or has no exposed schemas.

begin read only;
set local statement_timeout = '15s';

with application_tables as (
    select c.oid, n.nspname as schema_name, c.relname as table_name,
           c.relrowsecurity as rls_enabled,
           c.relforcerowsecurity as rls_forced
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where c.relkind in ('r', 'p')
      and n.nspname !~ '^pg_'
      and n.nspname not in (
          'information_schema', 'auth', 'storage', 'realtime', 'extensions',
          'supabase_functions', 'supabase_migrations', 'vault', 'graphql',
          'graphql_public', 'net', 'pgsodium', 'pgsodium_masks', 'cron'
      )
), table_metadata as (
    select t.schema_name, t.table_name, t.rls_enabled, t.rls_forced,
           coalesce((
               select jsonb_agg(jsonb_build_object(
                   'column', a.attname,
                   'type', pg_catalog.format_type(a.atttypid, a.atttypmod),
                   'not_null', a.attnotnull,
                   'has_default', a.atthasdef
               ) order by a.attnum)
               from pg_catalog.pg_attribute a
               where a.attrelid = t.oid and a.attnum > 0 and not a.attisdropped
           ), '[]'::jsonb) as columns,
           coalesce((
               select jsonb_agg(jsonb_build_object(
                   'name', con.conname,
                   'kind', con.contype,
                   'validated', con.convalidated,
                   'columns', (
                       select jsonb_agg(a.attname order by k.position)
                       from unnest(con.conkey) with ordinality as k(attnum, position)
                       join pg_catalog.pg_attribute a
                         on a.attrelid = t.oid and a.attnum = k.attnum
                   ),
                   'referenced_table', case when con.confrelid <> 0
                       then con.confrelid::regclass::text else null end
               ) order by con.conname)
               from pg_catalog.pg_constraint con
               where con.conrelid = t.oid
           ), '[]'::jsonb) as constraints,
           coalesce((
               select jsonb_agg(jsonb_build_object(
                   'name', p.policyname,
                   'permissive', p.permissive,
                   'roles', p.roles,
                   'command', p.cmd,
                   'using_is_literal_true', coalesce(p.qual in ('true', '(true)'), false),
                   'using_absent', p.qual is null,
                   'check_is_literal_true', coalesce(p.with_check in ('true', '(true)'), false),
                   'check_absent', p.with_check is null
               ) order by p.policyname)
               from pg_catalog.pg_policies p
               where p.schemaname = t.schema_name and p.tablename = t.table_name
           ), '[]'::jsonb) as policy_metadata,
           (
               select jsonb_object_agg(r.role_name, jsonb_build_object(
                   'schema_usage', has_schema_privilege(r.role_name, t.schema_name, 'USAGE'),
                   'select', has_table_privilege(r.role_name, t.oid, 'SELECT'),
                   'insert', has_table_privilege(r.role_name, t.oid, 'INSERT'),
                   'update', has_table_privilege(r.role_name, t.oid, 'UPDATE'),
                   'delete', has_table_privilege(r.role_name, t.oid, 'DELETE'),
                   'truncate', has_table_privilege(r.role_name, t.oid, 'TRUNCATE')
               ))
               from (values ('anon'), ('authenticated'), ('service_role')) as r(role_name)
           ) as effective_table_privileges
    from application_tables t
)
select jsonb_build_object(
    'transaction_read_only', current_setting('transaction_read_only') = 'on',
    'api_schema_setting_visible_to_sql_session', current_setting('pgrst.db_schemas', true),
    'authenticator_api_schema_setting', coalesce((
        select jsonb_agg(substring(setting from length('pgrst.db_schemas=') + 1))
        from pg_catalog.pg_roles r
        cross join lateral unnest(r.rolconfig) as options(setting)
        where r.rolname = 'authenticator' and setting like 'pgrst.db_schemas=%'
    ), '[]'::jsonb),
    'application_table_count', (select count(*) from application_tables),
    'tables', coalesce((
        select jsonb_agg(to_jsonb(t) order by t.schema_name, t.table_name)
        from table_metadata t
    ), '[]'::jsonb)
) as glof_schema_preflight;

commit;
