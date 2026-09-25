"""No network or production database writes in these tests."""
import sqlite3
import tempfile
import unittest
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import migrate_sqlite_to_supabase as migration


class MigrationReviewTests(unittest.TestCase):
    def test_migration_has_no_type_names_in_business_literals(self):
        sql = (migration.ROOT / 'supabase/migrations/20260923133650_reviewed_private_source_preservation.sql').read_text()
        sql = re.sub(r'--[^\n]*', '', sql)
        literals = re.findall(r"'((?:''|[^'])*)'", sql)
        forbidden = {'DOUBLE PRECISION', 'BIGINT', 'BOOLEAN', 'TIMESTAMPTZ', 'JSONB'}
        self.assertFalse(forbidden.intersection(x.upper().strip() for x in literals))

    def test_migration_has_only_expected_safe_alters(self):
        sql = (migration.ROOT / 'supabase/migrations/20260923133650_reviewed_private_source_preservation.sql').read_text()
        sql = re.sub(r'--[^\n]*', '', sql)
        self.assertIsNone(re.search(r'\b(DROP|TRUNCATE|DELETE)\b', sql, re.I))
        for alter in re.findall(r'ALTER TABLE\s+([^;]+);', sql, re.I):
            self.assertRegex(alter, r'^(internal|reference)\.\w+\s+(ENABLE ROW LEVEL SECURITY|ADD CONSTRAINT\s+\w+\s+FOREIGN KEY)')
        self.assertEqual(len(re.findall(r'CREATE TABLE internal\.', sql)), 16)
        self.assertEqual(len(re.findall(r'CREATE TABLE reference\.', sql)), 7)

    def test_migration_preserves_real_outbox_constraint(self):
        sql = (migration.ROOT / 'supabase/migrations/20260923133650_reviewed_private_source_preservation.sql').read_text()
        self.assertIn("CHECK (source_mode = 'REAL')", sql)
        self.assertNotIn("'DOUBLE PRECISION'", sql)

    def test_readonly_source_cannot_be_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.sqlite3'
            with sqlite3.connect(path) as conn:
                conn.execute('create table sample(id text primary key, payload text)')
                conn.execute("insert into sample values ('001','{\"a\": 1}')")
            conn.close()
            before = migration.file_hash(path)
            conn = migration.readonly(path)
            self.assertEqual(migration.rows(conn, 'sample')[0]['id'], '001')
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('delete from sample')
            conn.close()
            self.assertEqual(before, migration.file_hash(path))

    def test_duplicate_and_null_identity_rejected(self):
        for values in ([{'id': 1}, {'id': 1}], [{'id': None}]):
            with self.assertRaises(ValueError):
                migration.validate_rows(values, ['id'])

    def test_hash_preserves_text_types_and_json_bytes(self):
        self.assertNotEqual(migration.digest({'id': '001'}), migration.digest({'id': 1}))
        self.assertNotEqual(migration.digest({'json': '{"a":1}'}), migration.digest({'json': '{"a": 1}'}))

    def test_float_difference_within_tolerance_is_verified(self):
        stats = migration.verification_stats()
        source = {'lake_id': 'a', 'distance': 1.0}
        remote = {'lake_id': 'a', 'distance': 1.0 + 5e-13}
        self.assertTrue(migration.rows_equal(source, remote, list(source), {'distance'}, stats))
        self.assertEqual(stats['float_tolerated_comparisons'], 1)
        self.assertLessEqual(stats['max_absolute_float_difference'], 1e-12)

    def test_float_difference_above_tolerance_fails(self):
        stats = migration.verification_stats()
        source = {'lake_id': 'a', 'distance': 1.0}
        remote = {'lake_id': 'a', 'distance': 1.0 + 2e-12}
        self.assertFalse(migration.rows_equal(source, remote, list(source), {'distance'}, stats))

    def test_non_float_difference_remains_exact(self):
        stats = migration.verification_stats()
        source = {'lake_id': 'a', 'name': 'Lake A', 'count': 1, 'active': True}
        remote = {'lake_id': 'a', 'name': 'Lake B', 'count': 1, 'active': True}
        self.assertFalse(migration.rows_equal(source, remote, list(source), set(), stats))
        self.assertEqual(stats['float_tolerated_comparisons'], 0)

    def test_already_written_lake_resumes_with_tolerated_float_difference(self):
        stats = migration.verification_stats()
        source = {'lake_id': 'a', 'distance_to_nearest_settlement_km': 12.5}
        already_written = {'lake_id': 'a', 'distance_to_nearest_settlement_km': 12.5 + 5e-13}
        self.assertTrue(migration.rows_equal(
            source, already_written, list(source),
            {'distance_to_nearest_settlement_km'}, stats))

    def test_pmd_candidate_many_to_many_allowed(self):
        candidates = [{'pmd_id': 'a', 'current_lake_id': '1'},
                      {'pmd_id': 'a', 'current_lake_id': '2'},
                      {'pmd_id': 'b', 'current_lake_id': '1'}]
        migration.validate_rows(candidates, ['pmd_id', 'current_lake_id'])

    def test_wrong_project_dsn_rejected(self):
        for dsn in ('postgresql://user:dummy@db.wrong.supabase.co/postgres',
                    'postgresql://user.expected:dummy@evil.example/postgres'):
            with self.assertRaises(ValueError):
                migration.validate_dsn(dsn, 'expected')

    def test_retry_is_bounded_and_exponential(self):
        from psycopg import errors
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        tables = [('public', 'sample', ['id'], [{'id': 1}])]
        sleep = MagicMock()
        with patch.object(migration, 'insert_batch', side_effect=[errors.SerializationFailure(),
                errors.DeadlockDetected(), None]) as insert:
            migration.transfer(connection, tables, 1, sleep=sleep)
        self.assertEqual(insert.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_partial_failure_stops_later_batches(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (0,)
        tables = [('public', 'sample', ['id'], [{'id': 1}, {'id': 2}, {'id': 3}])]
        with patch.object(migration, 'insert_batch', side_effect=[None, ValueError('conflict')]) as insert:
            with self.assertRaises(ValueError):
                migration.transfer(connection, tables, 1)
        self.assertEqual(insert.call_count, 2)

    def test_empty_private_table_skips_row_verification_without_index_error(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        cursor.fetchone.return_value = (0,)
        results, stats = migration.transfer(
            connection, [('internal', 'official_lakes', ['lake_id'], [])], 250)
        self.assertEqual(results[0]['row_count_after'], 0)
        self.assertTrue(results[0]['private_full_checksum_verified'])
        self.assertEqual(stats['float_tolerated_comparisons'], 0)

    def test_batch_conflict_stops_before_insert(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        with patch.object(migration, 'target_rows', return_value={('a',): {'lake_id': 'b'}}):
            with self.assertRaises(ValueError):
                migration.insert_batch(connection, 'internal', 'lakes', ['lake_id'], [{'lake_id': 'a'}])
        statements = [call.args[0].as_string() for call in cursor.execute.call_args_list]
        self.assertFalse(any(s.startswith('INSERT INTO "internal"') for s in statements))

    def test_batch_verifies_after_duplicate_ignore(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        with patch.object(migration, 'target_rows', side_effect=[{}, {('a',): {'lake_id': 'a'}}]):
            migration.insert_batch(connection, 'internal', 'lakes', ['lake_id'], [{'lake_id': 'a'}])
        statements = [call.args[0].as_string() for call in cursor.execute.call_args_list]
        self.assertTrue(any('ON CONFLICT ("lake_id") DO NOTHING' in s for s in statements))
        self.assertEqual(len(statements), 2)

    def test_concurrent_conflict_fails_postwrite_verification(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(0,), (1,)]
        with self.assertRaises(ValueError):
            migration.insert_batch(connection, 'internal', 'lakes', ['lake_id'], [{'lake_id': 'a'}])

    def test_missing_approval_precedes_network(self):
        with patch.object(migration, 'prepare', return_value=([], {'direct_source_rows': 0})), \
             patch.object(migration, 'manifest', return_value={}), \
             patch('psycopg.connect') as connect:
            with self.assertRaisesRegex(ValueError, 'approved-manifest'):
                migration.main(['--apply'])
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
