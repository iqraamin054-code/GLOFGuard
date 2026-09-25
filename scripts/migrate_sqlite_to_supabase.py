"""Review-first, restartable PostgreSQL transfer. Default is LOCAL dry-run.

Preserves SQLite values and keys in private schemas; never edits source files.
Public observation publication remains the existing writer's responsibility.
Run --help. An explicit --apply plus matching --approved-manifest is required.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
import traceback
from contextlib import ExitStack, closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ORDER = ['lakes', 'official_lakes', 'inventory_reconciliations', 'inventory_matches',
         'full_inventory_runs', 'daily_weather', 'hourly_precipitation', 'forecasts',
         'satellite_observations', 'time_series_records', 'ingestion_failures',
         'monitoring_snapshots', 'full_inventory_lake_status',
         'full_inventory_error_events', 'environmental_source_snapshots', 'sync_outbox']
PMD_ORDER = ['pmd_2013_source_rows', 'pmd_2013_lakes', 'pmd_2013_row_memberships',
             'pmd_2013_current_coverage', 'pmd_2013_crosswalk',
             'pmd_2013_match_candidates', 'pmd_2013_metadata']
ALIASES = {'SOURCE-POLYGON-06960': 'PKGL-06121', 'SOURCE-POLYGON-06961': 'PKGL-06122'}
LAKE_FKS = ['lakes', 'daily_weather', 'hourly_precipitation', 'forecasts',
            'satellite_observations', 'time_series_records', 'ingestion_failures',
            'full_inventory_error_events', 'environmental_source_snapshots', 'sync_outbox']
BASINS = {'Swat': 214, 'Chitral': 116, 'Gilgit': 660, 'Hunza': 216,
          'Shigar': 110, 'Shyok': 270, 'Indus': 815, 'Shingo': 247, 'Astore': 196, 'Jhelum': 200}
FLOAT_DATA_TYPES = {'double precision', 'real'}
FLOAT_ABS_TOLERANCE = 1e-12


def qi(name):
    return '"' + name.replace('"', '""') + '"'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def verification_stats():
    return {'float_tolerated_comparisons': 0,
            'max_absolute_float_difference': 0.0,
            'float_abs_tolerance': FLOAT_ABS_TOLERANCE}


def rows_equal(source, remote, columns, float_columns, stats):
    for column in columns:
        source_value, remote_value = source[column], remote[column]
        if column in float_columns and isinstance(source_value, float) and isinstance(remote_value, float):
            if math.isfinite(source_value) and math.isfinite(remote_value):
                difference = abs(source_value - remote_value)
                if difference:
                    if not math.isclose(source_value, remote_value, rel_tol=0.0,
                                        abs_tol=FLOAT_ABS_TOLERANCE):
                        return False
                    stats['float_tolerated_comparisons'] += 1
                    stats['max_absolute_float_difference'] = max(
                        stats['max_absolute_float_difference'], difference)
                continue
        if source_value != remote_value:
            return False
    return True


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def readonly(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('pragma query_only=on')
    connection.execute('begin')
    return connection


def rows(connection, table):
    return [dict(row) for row in connection.execute('select * from ' + qi(table))]


def keys(connection, table):
    info = connection.execute('pragma table_info(' + qi(table) + ')').fetchall()
    return [r['name'] for r in sorted(info, key=lambda r: r['pk']) if r['pk']]


def validate_rows(data, identity):
    seen = set()
    for row in data:
        key = tuple(row[k] for k in identity)
        if None in key or key in seen:
            raise ValueError('Missing or duplicate source primary key')
        seen.add(key)


def prepare(op, pmd, mapdb, baseline_path):
    """Pure local read/transform, with fail-closed source integrity validation."""
    result, audit = [], {'lake_references': [], 'pmd_constraints': {}}
    parents = {r['lake_id'] for r in op.execute('select lake_id from lakes')}
    map_lakes = rows(mapdb, 'lakes')
    if parents != {r['lake_id'] for r in map_lakes} or len(parents) != 8806:
        raise ValueError('Operational/map parent inventories differ from reviewed scope')
    for conn in (op, pmd):
        if conn.execute('pragma foreign_key_check').fetchall():
            raise ValueError('Source foreign key check failed')
    for table in ORDER:
        data = rows(op, table)
        identity = keys(op, table)
        validate_rows(data, identity)
        for column in [r['name'] for r in op.execute('pragma table_info(' + qi(table) + ')')
                       if 'lake_id' in r['name']]:
            values = [r[column] for r in data if r[column] is not None]
            unknown = sorted(set(values) - parents)
            audit['lake_references'].append({'table': table, 'column': column,
                'non_null_rows': len(values), 'orphan_keys': unknown})
            if column == 'lake_id' and table in LAKE_FKS and unknown:
                raise ValueError('Unexpected canonical lake orphan: ' + table)
        if table == 'full_inventory_lake_status':
            for row in data:
                raw = row['lake_id']
                canonical = ALIASES.get(raw, raw)
                if canonical not in parents:
                    raise ValueError('Unknown historical status lake')
                if raw in ALIASES:
                    payload = json.loads(row['status_json'])
                    # The alias must be independently documented in the saved status.
                    if canonical not in json.dumps(payload) or row['processing_status'] != 'EXCLUDED_DUPLICATE':
                        raise ValueError('Duplicate source mapping evidence mismatch')
                row['canonical_lake_id'] = canonical
        result.append(('internal', table, identity, data))
    actual_basins = dict(pmd.execute('select basin,count(*) from pmd_2013_lakes group by basin'))
    if actual_basins != BASINS:
        raise ValueError('PMD basin totals differ from reviewed scope')
    for table in PMD_ORDER:
        data = rows(pmd, table)
        identity = keys(pmd, table)
        validate_rows(data, identity)
        if table in ('pmd_2013_current_coverage', 'pmd_2013_crosswalk', 'pmd_2013_match_candidates'):
            column = 'lake_id' if table.endswith('coverage') else 'current_lake_id'
            if {r[column] for r in data if r[column] is not None} - parents:
                raise ValueError('PMD current inventory orphan')
        audit['pmd_constraints'][table] = {'primary_key': identity, 'rows': len(data)}
        result.append(('reference', table, identity, data))
    selected = [r['current_lake_id'] for r in rows(pmd, 'pmd_2013_crosswalk')
                if r['current_lake_id'] is not None]
    if len(set(selected)) != len(selected):
        raise ValueError('Selected PMD crosswalk is not one-to-one')
    audit['pmd_selected_matches'] = len(selected)
    audit['pmd_basin_counts'] = actual_basins
    with baseline_path.open(encoding='utf-8-sig', newline='') as stream:
        baseline_csv = list(csv.DictReader(stream))
    validate_rows(baseline_csv, ['lake_id'])
    baseline_meta = {r['lake_id']: r for r in rows(mapdb, 'baseline_susceptibility')}
    if {r['lake_id'] for r in baseline_csv} != parents or set(baseline_meta) != parents:
        raise ValueError('Baseline key set differs from parent inventory')
    baseline = []
    for row in baseline_csv:
        saved = baseline_meta[row['lake_id']]
        score = float(row['baseline_susceptibility_score']) if row['baseline_susceptibility_score'] else None
        if (score != saved['susceptibility_score'] or row['baseline_susceptibility_level'] != saved['susceptibility_level']
                or (row['missing_static_fields'] or None) != saved['missing_static_fields']
                or row['interpretation'] != saved['interpretation']):
            raise ValueError('Baseline CSV and metadata disagree')
        baseline.append(saved)
    audit['counts'] = {'operational': sum(len(x[3]) for x in result if x[0] == 'internal'),
                       'pmd': sum(len(x[3]) for x in result if x[0] == 'reference'),
                       'baseline_csv': len(baseline)}
    audit['direct_source_rows'] = sum(audit['counts'].values())
    if audit['counts'] != {'operational': 433768, 'pmd': 31260, 'baseline_csv': 8806}:
        raise ValueError('Source counts changed; regenerate and review scope')
    # Extra parent projection is accounted separately from direct source rows.
    public_lakes = []
    for row in map_lakes:
        item = {k: v for k, v in row.items() if k not in ('map_id', 'location_wkt', 'created_at', 'updated_at')}
        item['source_geometry_valid'] = bool(item['source_geometry_valid'])
        public_lakes.append(item)
    result.insert(0, ('public', 'lakes', ['lake_id'], public_lakes))
    result.append(('public', 'baseline_susceptibility', ['lake_id'], baseline))
    return result, audit


def manifest(tables, audit, paths):
    report = {'mode': 'LOCAL_DRY_RUN', 'audit': audit,
              'source_files': {str(p.relative_to(ROOT)): file_hash(p) for p in paths},
              'transfer_script_sha256': file_hash(Path(__file__)),
              'migration_sql_sha256': file_hash(ROOT / 'supabase/migrations/20260923133650_reviewed_private_source_preservation.sql'),
              'tables': []}
    for schema, table, identity, data in tables:
        report['tables'].append({'schema': schema, 'table': table, 'key': identity,
            'rows': len(data), 'checksum': digest(sorted(digest(r) for r in data))})
    report['planned_target_rows'] = sum(r['rows'] for r in report['tables'])
    report['manifest_sha256'] = digest(report)
    return report


def target_float_columns(connection, schema, table):
    with connection.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s
              AND data_type = ANY(%s)
        """, (schema, table, list(FLOAT_DATA_TYPES)))
        return {row[0] for row in cur.fetchall()}


def target_rows(connection, schema, table, identity, columns, keys=None):
    from psycopg import sql
    with connection.cursor() as cur:
        query = sql.SQL('SELECT {} FROM {}').format(
            sql.SQL(',').join(sql.Identifier(column) for column in columns),
            sql.Identifier(schema, table))
        parameters = []
        if keys:
            key_placeholders = sql.SQL(',').join(
                sql.SQL('(') + sql.SQL(',').join(sql.Placeholder() for _ in identity) + sql.SQL(')')
                for _ in keys)
            query += sql.SQL(' WHERE ({}) IN ({})').format(
                sql.SQL(',').join(sql.Identifier(key) for key in identity), key_placeholders)
            parameters = [value for key in keys for value in key]
        cur.execute(query, parameters)
        names = [column.name for column in cur.description]
        return {tuple(row[names.index(key)] for key in identity): dict(zip(names, row))
                for row in cur.fetchall()}


def insert_batch(connection, schema, table, identity, data, float_columns=None, stats=None):
    """One transaction per batch; identical rows are a no-op, conflicts roll back."""
    from psycopg import sql
    columns = list(data[0])
    float_columns = float_columns or set()
    stats = stats or verification_stats()
    target = sql.Identifier(schema, table)
    temp = sql.Identifier('glof_transfer_batch')
    names = sql.SQL(',').join(map(sql.Identifier, columns))
    with connection.transaction():
        with connection.cursor() as cur:
            cur.execute(sql.SQL('CREATE TEMP TABLE {} ON COMMIT DROP AS SELECT {} FROM {} WITH NO DATA').format(temp, names, target))
            cur.executemany(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(temp, names,
                sql.SQL(',').join(sql.Placeholder() for _ in columns)),
                [tuple(row[c] for c in columns) for row in data])
            batch_keys = [tuple(row[key] for key in identity) for row in data]
            existing = target_rows(connection, schema, table, identity, columns, batch_keys)
            if any(tuple(row[key] for key in identity) in existing and not rows_equal(
                    row, existing[tuple(row[key] for key in identity)], columns, float_columns, stats)
                    for row in data):
                raise ValueError('Existing target values conflict: ' + schema + '.' + table)
            extra_name, extra_select = sql.SQL(''), sql.SQL('')
            if (schema, table) == ('public', 'lakes'):
                # Resolve PostGIS schema through its installed extension; no extension changes.
                cur.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='postgis'")
                found = cur.fetchone()
                if found is None:
                    raise ValueError('Required existing PostGIS extension absent')
                extra_name = sql.SQL(',location')
                extra_select = sql.SQL(',{}.st_setsrid({}.st_makepoint(longitude,latitude),4326)').format(sql.Identifier(found[0]), sql.Identifier(found[0]))
            cur.execute(sql.SQL('INSERT INTO {} ({}{}) SELECT {}{} FROM {} ON CONFLICT ({}) DO NOTHING').format(
                target, names, extra_name, names, extra_select, temp, sql.SQL(',').join(map(sql.Identifier, identity))))
            # Verify after conflict handling, including a concurrent insert by another process.
            written = target_rows(connection, schema, table, identity, columns, batch_keys)
            if any(tuple(row[key] for key in identity) not in written or not rows_equal(
                    row, written[tuple(row[key] for key in identity)], columns, float_columns, stats)
                    for row in data):
                raise ValueError('Post-write row verification failed')


def transfer(connection, tables, batch_size, attempts=3, sleep=time.sleep):
    from psycopg import OperationalError, errors
    from psycopg import sql
    results = []
    stats = verification_stats()
    for schema, table, identity, data in tables:
        float_columns = target_float_columns(connection, schema, table)
        with connection.cursor() as cur:
            cur.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(schema, table)))
            before_count = cur.fetchone()[0]
        for start in range(0, len(data), batch_size):
            for attempt in range(attempts):
                try:
                    insert_batch(connection, schema, table, identity, data[start:start + batch_size],
                                 float_columns, stats)
                    break
                except (errors.SerializationFailure, errors.DeadlockDetected):
                    if attempt + 1 == attempts:
                        raise
                    sleep(min(2 ** attempt, 8))
                except OperationalError:
                    # Lost connections/uncertain commits require a fresh process; rerun is idempotent.
                    raise RuntimeError('Connection lost; resume by rerunning approved manifest') from None
        with connection.cursor() as cur:
            cur.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(schema, table)))
            after_count = cur.fetchone()[0]
            if schema in ('internal', 'reference') and data:
                columns = list(data[0])
                cur.execute(sql.SQL('SELECT {} FROM {}').format(
                    sql.SQL(',').join(sql.Identifier(column) for column in columns),
                    sql.Identifier(schema, table)))
                remote_rows = {}
                while batch := cur.fetchmany(batch_size):
                    for row in batch:
                        item = dict(zip(columns, row))
                        remote_rows[tuple(item[key] for key in identity)] = item
                if after_count != len(data) or len(remote_rows) != len(data) or any(
                        tuple(row[key] for key in identity) not in remote_rows or not rows_equal(
                            row, remote_rows[tuple(row[key] for key in identity)], columns,
                            float_columns, stats) for row in data):
                    raise ValueError('Full private-table checksum mismatch: ' + schema + '.' + table)
        results.append({'table': schema + '.' + table, 'verified_source_rows': len(data),
                        'row_count_before': before_count, 'row_count_after': after_count,
                        'source_checksum': digest(sorted(digest(r) for r in data)),
                        'private_full_checksum_verified': schema in ('internal', 'reference')})
    return results, stats


def validate_dsn(dsn, project_ref):
    from psycopg.conninfo import conninfo_to_dict
    values = conninfo_to_dict(dsn)
    host, user = values.get('host', ''), values.get('user', '')
    direct = host == 'db.' + project_ref + '.supabase.co'
    pooled = host.endswith('.pooler.supabase.com') and user.endswith('.' + project_ref)
    if not project_ref or not (direct or pooled) or values.get('hostaddr'):
        raise ValueError('Database endpoint does not match expected hosted project')
    return values


def reviewed_sslmode(values):
    mode = values.get('sslmode') or os.environ.get('PGSSLMODE') or 'require'
    if mode not in ('require', 'verify-ca', 'verify-full'):
        raise ValueError('Unsupported PostgreSQL sslmode; refusing to weaken TLS policy')
    return mode


def preflight_target(connection, tables):
    """Check ALL destinations before the first insert, including browser isolation."""
    with connection.cursor() as cur:
        for schema in ('internal', 'reference'):
            for role in ('anon', 'authenticated'):
                cur.execute('SELECT has_schema_privilege(%s,%s,\'USAGE\')', (role, schema))
                if cur.fetchone()[0]:
                    raise ValueError('Private schema has browser access')
        for schema, table, identity, data in tables:
            name = schema + '.' + table
            cur.execute('SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass(%s)', (name,))
            found = cur.fetchone()
            if not found or not found[0]:
                raise ValueError('Missing destination or RLS: ' + name)
            cur.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s', (schema, table))
            columns = {r[0] for r in cur.fetchall()}
            if data and set(data[0]) - columns:
                raise ValueError('Destination columns incompatible: ' + name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approved-manifest', type=Path)
    parser.add_argument('--batch-size', type=int, default=250)
    parser.add_argument('--report', type=Path, default=ROOT / 'output/supabase_migration_review.json')
    args = parser.parse_args(argv)
    if args.approved_manifest and args.report.resolve() == args.approved_manifest.resolve():
        raise ValueError('Report must not overwrite approved manifest')
    if not 1 <= args.batch_size <= 1000:
        parser.error('batch-size must be 1..1000')
    paths = [ROOT / 'data/glofguard.sqlite3', ROOT / 'data/reference/pmd_2013/pmd_2013_reference.sqlite3',
             ROOT / 'data/map/glof_map.sqlite3', ROOT / 'data/baseline_susceptibility.csv']
    # Files must be quiescent; reject sidecar WAL changes rather than miss committed data.
    if any(Path(str(p) + '-wal').exists() and Path(str(p) + '-wal').stat().st_size for p in paths[:3]):
        raise ValueError('SQLite WAL present; stop writers and review snapshot procedure first')
    before = [file_hash(p) for p in paths]
    with ExitStack() as stack:
        connections = [stack.enter_context(closing(readonly(p))) for p in paths[:3]]
        tables, audit = prepare(*connections, paths[3])
        report = manifest(tables, audit, paths)
    if before != [file_hash(p) for p in paths] or any(
            Path(str(p) + '-wal').exists() and Path(str(p) + '-wal').stat().st_size for p in paths[:3]):
        raise ValueError('Source files changed during inspection')
    if args.apply:
        if not args.approved_manifest:
            raise ValueError('--apply requires --approved-manifest')
        approved = json.loads(args.approved_manifest.read_text(encoding='utf-8'))
        if approved != report:
            raise ValueError('Approved manifest differs from current source or transformation')
        from glofguard.config import load_env_file
        load_env_file(ROOT / '.env')
        dsn = os.environ.get('GLOF_MIGRATION_DATABASE_URL')
        if not dsn:
            raise ValueError('Missing GLOF_MIGRATION_DATABASE_URL (server-only PostgreSQL DSN)')
        import psycopg
        dsn_values = validate_dsn(dsn, os.environ.get('SUPABASE_PROJECT_REF', ''))
        with psycopg.connect(dsn, sslmode=reviewed_sslmode(dsn_values),
                             connect_timeout=15, autocommit=True) as connection:
            # Apply never executes DDL. Schema and privileges must already have been approved/applied.
            preflight_target(connection, tables)
            report['transfer'], report['verification'] = transfer(connection, tables, args.batch_size)
        report['mode'] = 'TRANSFER_VERIFIED'
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps({'mode': report['mode'], 'direct_source_rows': audit['direct_source_rows'],
                      'planned_target_rows': report['planned_target_rows'], 'report': str(args.report)}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ValueError as exc:
        origin = traceback.extract_tb(exc.__traceback__)[-1]
        detail = str(exc)
        if not detail.replace(' ', '').replace('.', '').replace('_', '').replace('-', '').replace('/', '').replace(':', '').isalnum():
            detail = 'ValueError guard raised; detail redacted'
        print(json.dumps({
            'status': 'FAILED',
            'error_class': 'ValueError',
            'guard_detail': detail,
            'source_file': str(Path(origin.filename).resolve().relative_to(ROOT)),
            'source_line': origin.lineno,
        }))
        raise SystemExit(2)
    except Exception as exc:
        # Driver exceptions can contain DSNs, SQL values, payloads, and credentials.
        print(json.dumps({'status': 'FAILED', 'error_class': type(exc).__name__,
                          'detail': 'Stopped safely; no credentials or source payloads logged.'}))
        raise SystemExit(2)
