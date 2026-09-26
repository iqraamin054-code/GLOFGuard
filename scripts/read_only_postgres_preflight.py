"""Read-only GLOF PostgreSQL preflight; never logs connection strings or errors."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = 'cfuujnvtwztqrwuwubhh'
PUBLIC_REQUIRED = ('lakes', 'baseline_susceptibility', 'environmental_observations',
                   'source_freshness', 'ingestion_runs', 'processing_queue')
MIGRATION_PRIVILEGES = ('SELECT', 'INSERT', 'UPDATE')
DUPLICATE_KEYS = {
    'public.lakes': ('lake_id',),
    'public.baseline_susceptibility': ('lake_id',),
    'public.environmental_observations': ('observation_id',),
    'public.source_freshness': ('observation_id', 'source_name'),
    'public.processing_queue': ('lake_id',),
}


def client_tls_state(connection, sslmode):
    """Return client TLS state without relying on the pooler's backend session."""
    for owner in (getattr(connection, 'pgconn', None), getattr(connection, 'info', None)):
        if owner is None:
            continue
        try:
            value = getattr(owner, 'ssl_in_use')
        except AttributeError:
            continue
        return bool(value), 'LIBPQ_SSL_STATE'
    if sslmode in ('require', 'verify-ca', 'verify-full'):
        return None, 'CLIENT_TLS_REQUIRED_BY_DSN'
    return False, 'CLIENT_TLS_NOT_ACTIVE'


def main():
    report = {'expected_project': EXPECTED, 'mode': 'READ_ONLY'}
    try:
        env = {}
        for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                env[key.strip()] = value.strip().strip('\"\'')
        dsn = env.get('GLOF_MIGRATION_DATABASE_URL') or os.environ.get('GLOF_MIGRATION_DATABASE_URL')
        if not dsn:
            report['status'] = 'STOP_MISSING_DSN'
            return report
        config = conninfo_to_dict(dsn)
        host, user = config.get('host', ''), config.get('user', '')
        direct = host == f'db.{EXPECTED}.supabase.co'
        pooled = host.endswith('.pooler.supabase.com') and user.endswith('.' + EXPECTED)
        safe = (direct or pooled) and not any(config.get(k) for k in ('hostaddr', 'service'))
        report['endpoint_project_match'] = safe
        report['api_project_match'] = urlsplit(env.get('SUPABASE_URL', '')).hostname == EXPECTED + '.supabase.co'
        if not safe or env.get('SUPABASE_PROJECT_REF', EXPECTED) != EXPECTED:
            report['status'] = 'STOP_PROJECT_MISMATCH'
            return report
        manifest = json.loads((ROOT / 'output/supabase_migration_review.json').read_text())
        migration = ROOT / 'supabase/migrations/20260923133650_reviewed_private_source_preservation.sql'
        report['manifest_sql_checksum_matches'] = hashlib.sha256(migration.read_bytes()).hexdigest() == manifest['migration_sql_sha256']
        if not report['manifest_sql_checksum_matches']:
            report['status'] = 'STOP_MANIFEST_SQL_MISMATCH'
            return report
        expected_tables = {(t['schema'], t['table']) for t in manifest['tables']}
        expected_private_tables = {(t['schema'], t['table']) for t in manifest['tables']
                       if t['schema'] in ('internal', 'reference')}
        report['expected_private_tables'] = len(expected_private_tables)
        report['expected_target_rows'] = {t['schema'] + '.' + t['table']: t['rows']
                         for t in manifest['tables']}
        # Never weaken configured verification; otherwise enforce encrypted transport.
        mode = config.get('sslmode') or os.environ.get('PGSSLMODE') or 'require'
        mode = mode if mode in ('verify-full', 'verify-ca') else 'require'
        report['sslmode_used'] = mode
        with psycopg.connect(dsn, sslmode=mode, connect_timeout=15,
                options='-c default_transaction_read_only=on -c statement_timeout=15000',
                row_factory=dict_row) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            report['connection'] = conn.execute("SELECT current_database() AS database, current_user AS database_user, version() AS postgres_version, current_setting('transaction_read_only') AS read_only").fetchone()
            report['tls'] = conn.execute('SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
            report['client_tls_active'], report['client_tls_state'] = client_tls_state(conn, mode)
            client_tls_confirmed = report['client_tls_active'] is True or report['client_tls_state'] == 'CLIENT_TLS_REQUIRED_BY_DSN'
            if not client_tls_confirmed:
                report['status'] = 'STOP_TLS_NOT_CONFIRMED'
                conn.rollback()
                return report
            report['certificate_hostname_verified'] = mode == 'verify-full'
            report['identity_evidence'] = 'Validated hosted endpoint; pooler username routing where applicable. No project ID is assumed from database name.'
            report['schemas'] = conn.execute("SELECT nspname, nspacl::text AS acl FROM pg_namespace WHERE nspname IN ('internal','reference') ORDER BY 1").fetchall()
            visible = conn.execute("""
                SELECT n.nspname AS schema, c.relname AS table_name,
                       c.relkind, c.relrowsecurity,
                       has_schema_privilege(current_user, n.nspname, 'USAGE') AS schema_usage
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE (n.nspname IN ('internal', 'reference')
                       AND c.relkind IN ('r', 'p'))
                   OR (n.nspname = 'public' AND c.relname = ANY(%s))
                ORDER BY 1, 2
            """, (list(PUBLIC_REQUIRED),)).fetchall()
            report['visible_destination_tables'] = visible
            actual_tables = {(row['schema'], row['table_name']) for row in visible}
            missing_tables = sorted(expected_tables - actual_tables)
            report['unexpected_private_tables'] = sorted(actual_tables - expected_private_tables
                                                         - {('public', name) for name in PUBLIC_REQUIRED})
            report['missing_manifest_tables'] = missing_tables
            report['schema_table_set_matches_manifest'] = not missing_tables and not report['unexpected_private_tables']
            if not report['schema_table_set_matches_manifest']:
                report['status'] = 'STOP_SCHEMA_TABLE_MISMATCH'
                conn.rollback()
                return report
            report['backend_role'] = conn.execute("SELECT rolname,rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname='glof_backend'").fetchall()
            report['backend_role_is_hardened'] = bool(report['backend_role']) and all(
                not role['rolcanlogin'] and not role['rolsuper'] and not role['rolbypassrls']
                for role in report['backend_role'])
            if not report['backend_role_is_hardened']:
                report['status'] = 'STOP_BACKEND_ROLE_SECURITY_MISMATCH'
                conn.rollback()
                return report
            names = sorted({t for _, t in expected_tables})
            report['existing_proposed_names'] = conn.execute('SELECT n.nspname AS schema,c.relname AS name,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname=ANY(%s) ORDER BY 1,2', (names,)).fetchall()
            report['policies'] = conn.execute("SELECT schemaname,tablename,policyname,roles,cmd FROM pg_policies WHERE schemaname IN ('internal','reference')").fetchall()
            report['table_grants'] = conn.execute("SELECT table_schema,table_name,grantee,privilege_type FROM information_schema.table_privileges WHERE table_schema IN ('internal','reference')").fetchall()
            privileges = {}
            for schema, table in sorted(expected_tables):
                qualified = schema + '.' + table
                privileges[qualified] = {
                    privilege: conn.execute(
                        'SELECT has_table_privilege(current_user, %s, %s) AS allowed',
                        (qualified, privilege)).fetchone()['allowed']
                    for privilege in MIGRATION_PRIVILEGES
                }
            report['migration_account'] = conn.execute(
                'SELECT current_user AS role, current_database() AS database').fetchone()
            report['migration_account_privileges'] = privileges
            report['required_privileges_granted'] = all(
                allowed for table_privileges in privileges.values()
                for allowed in table_privileges.values())
            if not report['required_privileges_granted']:
                report['status'] = 'STOP_REQUIRED_PRIVILEGES_MISSING'
                conn.rollback()
                return report
            report['default_grants'] = conn.execute("SELECT n.nspname,a.defaclobjtype,a.defaclacl::text AS acl FROM pg_default_acl a LEFT JOIN pg_namespace n ON n.oid=a.defaclnamespace WHERE n.nspname IN ('internal','reference') OR a.defaclnamespace=0").fetchall()
            counts = {}
            for name in PUBLIC_REQUIRED:
                counts['public.' + name] = conn.execute(psycopg.sql.SQL('SELECT count(*) AS n FROM {}').format(psycopg.sql.Identifier('public', name))).fetchone()['n']
            report['public_counts'] = counts
            report['duplicate_checks'] = {}
            for qualified, columns in DUPLICATE_KEYS.items():
                identifiers = psycopg.sql.SQL(',').join(
                    psycopg.sql.Identifier(column) for column in columns)
                report['duplicate_checks'][qualified] = conn.execute(
                    psycopg.sql.SQL(
                        'SELECT count(*) AS rows, count(DISTINCT ({0})) AS distinct_keys '
                        'FROM {1}'
                    ).format(identifiers, psycopg.sql.SQL(qualified))).fetchone()
            report['duplicate_free'] = all(
                result['rows'] == result['distinct_keys']
                for result in report['duplicate_checks'].values())
            if not report['duplicate_free']:
                report['status'] = 'STOP_PUBLIC_DUPLICATES'
                conn.rollback()
                return report
            report['expected_final_counts'] = dict(counts)
            for name in ('lakes', 'baseline_susceptibility'):
                report['expected_final_counts']['public.' + name] = max(
                    counts['public.' + name], manifest['audit']['counts']['baseline_csv'])
            report['expected_final_counts']['public.lakes'] = max(
                counts['public.lakes'], manifest['audit']['counts']['baseline_csv'])
            report['duplicate_sensitive_public_tables'] = {
                'public.' + name: {
                    'existing_rows': counts['public.' + name],
                    'planned_rows': next((t['rows'] for t in manifest['tables']
                                         if t['schema'] == 'public' and t['table'] == name), 0),
                    'expected_final_rows': report['expected_final_counts']['public.' + name]
                }
                for name in ('lakes', 'baseline_susceptibility',
                             'environmental_observations', 'source_freshness',
                             'processing_queue')
            }
            report['captured_at'] = str(conn.execute('SELECT transaction_timestamp() AS ts').fetchone()['ts'])
            report['status'] = 'PASS'
            conn.rollback()
    except Exception as exc:
        report['status'] = 'STOP_CONNECTION_OR_QUERY_ERROR'
        report['error_class'] = type(exc).__name__
        # Categorize known errors without returning libpq text (which may contain secrets).
        message = str(exc).lower()
        report['error_category'] = next((label for token,label in [
            ('permission denied','PERMISSION_DENIED'),('certificate','CERTIFICATE'),
            ('password authentication failed','AUTHENTICATION'),('could not translate host','DNS'),
            ('network is unreachable','NETWORK'),('timeout','TIMEOUT'),
            ('tenant or user not found','POOLER_ROUTING')] if token in message), 'UNCLASSIFIED')
    return report


if __name__ == '__main__':
    result = main()
    output = ROOT / 'output/postgres_read_only_preflight.json'
    output.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    print(json.dumps(result, default=str))
