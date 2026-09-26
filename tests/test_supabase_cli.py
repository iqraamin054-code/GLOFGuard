from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import UTC, date, datetime
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from glofguard import cli
from glofguard.pipeline import RefreshSummary
from glofguard.schemas import TimeSeriesRecord
from glofguard.storage import Repository
from glofguard.supabase_persistence import SyncReport


class SupabaseCliTests(unittest.TestCase):
    lake_id = "PKGL-00995"

    def _record(self) -> TimeSeriesRecord:
        return TimeSeriesRecord(
            lake_id=self.lake_id,
            observation_date=date(2026, 9, 8),
            latitude=35.4,
            longitude=74.6,
            source_mode="REAL",
            input_signature="isolated-cli-test",
            prediction_timestamp=datetime(2026, 9, 8, 12, tzinfo=UTC),
        )

    def _record_for_lake(self, lake_id: str, signature: str) -> TimeSeriesRecord:
        return replace(self._record(), lake_id=lake_id, input_signature=signature)

    def _repository(self, path: Path, *, queued: bool = True) -> Repository:
        repository = Repository(path)
        repository.initialize()
        repository.save_record_if_changed(self._record(), queue_supabase=queued)
        return repository

    def _run(self, path: Path, arguments: list[str], *, report: SyncReport | None = None):
        output = io.StringIO()
        settings = SimpleNamespace(database_path=path, validate=Mock())
        writer = Mock()
        writer.connectivity_check.return_value = SimpleNamespace(
            schema_available=True, to_dict=lambda: {"authenticated": True}
        )
        service = Mock()
        service.sync_pending.return_value = report or SyncReport(
            local_status="LOCAL_SAVED", remote_saved=0, remote_pending=0,
            remote_failed=0, queued_events=0, dry_run=False,
        )
        with (
            patch.object(cli.Settings, "from_env", return_value=settings),
            patch.object(cli.SupabaseConfig, "from_env", return_value=None),
            patch.object(cli, "SupabaseWriter", return_value=writer),
            patch.object(cli, "SupabaseSyncService", return_value=service),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            result = cli.main(arguments)
        payload, _ = json.JSONDecoder().raw_decode(output.getvalue())
        return result, payload, writer, service

    def test_dry_run_missing_database_does_not_create_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent-parent" / "local.sqlite3"
            with self.assertRaises(SystemExit) as raised:
                self._run(path, ["sync-supabase", "--dry-run", "--lake-id", self.lake_id])
            self.assertEqual(raised.exception.code, 1)
            self.assertFalse(path.parent.exists())

    def test_supabase_check_fails_for_unavailable_tables_and_passes_for_present_tables(self) -> None:
        for schema_available, observation_status, expected_exit in (
            (True, "PRESENT", 0),
            (True, "UNAVAILABLE", 1),
            (False, "PRESENT", 1),
        ):
            with self.subTest(schema_available=schema_available, observation_status=observation_status):
                output = io.StringIO()
                writer = Mock()
                writer.connectivity_check.return_value.to_dict.return_value = {
                    "authenticated": True, "schema_available": schema_available,
                }
                tables = {
                    "lakes": "PRESENT",
                    "baseline_susceptibility": "PRESENT",
                    "environmental_observations": observation_status,
                    "source_freshness": "PRESENT",
                    "ingestion_runs": "PRESENT",
                    "processing_queue": "PRESENT",
                    "sync_outbox": "PRIVATE_NOT_EXPOSED",
                }
                writer.inspect_required_tables.return_value = tables
                with (
                    patch.object(cli.Settings, "from_env"),
                    patch.object(cli.SupabaseConfig, "from_env"),
                    patch.object(cli, "SupabaseWriter", return_value=writer),
                    patch.object(cli, "_repository") as repository,
                    redirect_stdout(output),
                ):
                    result = cli.main(["supabase-check"])
                payload, _ = json.JSONDecoder().raw_decode(output.getvalue())
                self.assertEqual(result, expected_exit)
                self.assertEqual(payload["required_tables"], tables)
                self.assertEqual(payload["connection"]["schema_available"], schema_available)
                writer.connectivity_check.assert_called_once_with("lakes")
                repository.assert_not_called()

    def test_dry_run_previews_legacy_database_without_schema_or_data_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE time_series_records (lake_id TEXT, source_mode TEXT, "
                    "prediction_timestamp TEXT, record_json TEXT)"
                )
                connection.execute(
                    "INSERT INTO time_series_records VALUES (?, 'REAL', ?, ?)",
                    (self.lake_id, "2026-09-08T12:00:00+00:00", json.dumps(self._record().to_dict())),
                )
            before = path.read_bytes()
            result, payload, writer, service = self._run(
                path, ["sync-supabase", "--dry-run", "--queue-existing", "--lake-id", self.lake_id]
            )
            self.assertEqual(result, 0)
            self.assertEqual(payload["queued_event_count"], 1)
            self.assertEqual(payload["remote_status"], "DRY_RUN")
            self.assertEqual(payload["selected_outbox"]["REMOTE_PENDING"], 0)
            self.assertEqual(path.read_bytes(), before)
            writer.connectivity_check.assert_not_called()
            service.sync_pending.assert_not_called()

    def test_dry_run_force_previews_nonretryable_failed_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.sqlite3"
            repository = self._repository(path)
            item = repository.pending_sync_outbox([self.lake_id], limit=1)[0]
            repository.mark_sync_failed(item.event_id, ValueError("authorization failed"), retryable=False, next_attempt_at=None)
            _, payload, _, _ = self._run(
                path, ["sync-supabase", "--dry-run", "--force", "--lake-id", self.lake_id]
            )
            self.assertEqual(payload["queued_event_count"], 1)
            self.assertEqual(repository.sync_outbox_summary()["REMOTE_FAILED"], 1)

    def test_sync_failed_or_delayed_outbox_is_not_success_when_nothing_is_due(self) -> None:
        for retryable in (False, True):
            with self.subTest(retryable=retryable), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "local.sqlite3"
                repository = self._repository(path)
                item = repository.pending_sync_outbox([self.lake_id], limit=1)[0]
                repository.mark_sync_failed(
                    item.event_id, ValueError("isolated test failure"), retryable=retryable,
                    next_attempt_at="2999-01-01T00:00:00+00:00" if retryable else None,
                )
                result, payload, _, _ = self._run(path, ["sync-supabase", "--lake-id", self.lake_id])
                self.assertEqual(result, 1)
                self.assertEqual(payload["remote_status"], "REMOTE_PENDING" if retryable else "REMOTE_FAILED")

    def test_sync_attempt_reporting_pending_or_failure_returns_nonzero(self) -> None:
        for pending, failed in ((1, 0), (0, 1)):
            with self.subTest(pending=pending), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "local.sqlite3"
                self._repository(path)
                report = SyncReport("LOCAL_SAVED", 0, pending, failed, 1, False)
                result, payload, _, _ = self._run(
                    path, ["sync-supabase", "--lake-id", self.lake_id], report=report
                )
                self.assertEqual(result, 1)
                self.assertEqual(payload["sync"]["REMOTE_PENDING"], pending)
                self.assertEqual(payload["sync"]["REMOTE_FAILED"], failed)

    def test_replay_of_saved_event_is_success_without_claiming_new_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.sqlite3"
            repository = self._repository(path)
            item = repository.pending_sync_outbox([self.lake_id], limit=1)[0]
            repository.mark_sync_saved(item.event_id, {"test_receipt": True})
            result, payload, _, _ = self._run(path, ["sync-supabase", "--lake-id", self.lake_id])
            self.assertEqual(result, 0)
            self.assertEqual(payload["remote_status"], "REMOTE_SAVED")
            self.assertEqual(payload["sync"]["REMOTE_SAVED"], 0)
            self.assertEqual(payload["selected_outbox"]["REMOTE_SAVED"], 1)

    def test_sync_no_matching_event_does_not_claim_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.sqlite3"
            self._repository(path, queued=False)
            result, payload, _, _ = self._run(path, ["sync-supabase", "--lake-id", self.lake_id])
            self.assertEqual(result, 1)
            self.assertEqual(payload["remote_status"], "NOT_QUEUED")
            self.assertEqual(payload["sync"]["local_status"], "NO_MATCHING_OUTBOX_EVENTS")

    def test_refresh_reports_actual_creation_and_failure_outcomes(self) -> None:
        cases = (
            (RefreshSummary(lakes_processed=1, failures=1), "LOCAL_NOT_SAVED", "NOT_QUEUED_THIS_REFRESH", 1),
            (RefreshSummary(lakes_processed=1, unchanged_records=1), "LOCAL_UNCHANGED", "NOT_QUEUED_THIS_REFRESH", 0),
            (RefreshSummary(lakes_processed=1, records_created=1), "LOCAL_SAVED", "REMOTE_PENDING", 0),
        )
        for summary, local_status, remote_status, expected_exit in cases:
            with self.subTest(local_status=local_status), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "local.sqlite3"
                with patch.object(cli, "RefreshPipeline") as pipeline:
                    pipeline.return_value.run.return_value = summary
                    result, payload, _, _ = self._run(
                        path, ["refresh", "--sources", "", "--queue-supabase", "--lake-id", self.lake_id]
                    )
                self.assertEqual(result, expected_exit)
                self.assertEqual(payload["local_status"], local_status)
                self.assertEqual(payload["remote_status"], remote_status)

    def test_refresh_sync_supabase_automatically_syncs_selected_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.sqlite3"
            repository = self._repository(path)
            report = SyncReport("LOCAL_SAVED", 1, 0, 0, 1, False)
            with patch.object(cli, "RefreshPipeline") as pipeline:
                pipeline.return_value.run.return_value = RefreshSummary(
                    lakes_processed=1, records_created=1
                )
                result, payload, writer, service = self._run(
                    path,
                    ["refresh", "--sources", "", "--sync-supabase", "--lake-id", self.lake_id],
                    report=report,
                )
            self.assertEqual(result, 0)
            self.assertEqual(payload["local_status"], "LOCAL_SAVED")
            self.assertIn("supabase_sync", payload)
            self.assertEqual(payload["supabase_sync"]["REMOTE_SAVED"], 1)
            pipeline.assert_called_once()
            self.assertTrue(pipeline.call_args.kwargs["queue_supabase"])
            service.sync_pending.assert_called_once_with(
                [self.lake_id], limit=1, force=False
            )
            writer.connectivity_check.assert_called_once_with()

    def test_refresh_sync_supabase_keeps_local_record_when_remote_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.sqlite3"
            repository = self._repository(path)
            report = SyncReport("LOCAL_SAVED", 0, 1, 0, 1, False)
            with patch.object(cli, "RefreshPipeline") as pipeline:
                pipeline.return_value.run.return_value = RefreshSummary(
                    lakes_processed=1, records_created=1
                )
                result, payload, _, _ = self._run(
                    path,
                    ["refresh", "--sources", "", "--sync-supabase", "--lake-id", self.lake_id],
                    report=report,
                )
            self.assertEqual(result, 1)
            self.assertEqual(payload["local_status"], "LOCAL_SAVED")
            self.assertEqual(payload["supabase_sync"]["REMOTE_PENDING"], 1)
            self.assertEqual(Repository(path).sync_outbox_summary()["REMOTE_PENDING"], 1)

    def test_refresh_sync_supabase_is_idempotent_for_unchanged_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.sqlite3"
            repository = self._repository(path)
            before = repository.sync_outbox_summary()
            with patch.object(cli, "RefreshPipeline") as pipeline:
                pipeline.return_value.run.return_value = RefreshSummary(
                    lakes_processed=1, unchanged_records=1
                )
                result, payload, _, service = self._run(
                    path,
                    ["refresh", "--sources", "", "--sync-supabase", "--lake-id", self.lake_id],
                    report=SyncReport("LOCAL_SAVED", 0, 0, 0, 0, False),
                )
            self.assertEqual(result, 0)
            self.assertEqual(payload["local_status"], "LOCAL_UNCHANGED")
            self.assertEqual(Repository(path).sync_outbox_summary(), before)
            self.assertEqual(payload["remote_status"], "NOT_QUEUED_THIS_REFRESH")
            self.assertEqual(payload["supabase_sync"]["queued_events"], 0)
            self.assertEqual(payload["supabase_sync"]["REMOTE_SAVED"], 0)
            service.sync_pending.assert_not_called()

    def test_refresh_sync_supabase_scopes_multiple_lakes_and_does_not_flush_unrelated_events(self) -> None:
        second_lake = "PKGL-00002"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.sqlite3"
            repository = Repository(path)
            repository.initialize()
            repository.save_record_if_changed(self._record(), queue_supabase=True)
            repository.save_record_if_changed(
                self._record_for_lake(second_lake, "second-signature"), queue_supabase=True
            )
            unrelated = "PKGL-00003"
            repository.save_record_if_changed(
                self._record_for_lake(unrelated, "unrelated-signature"), queue_supabase=True
            )
            with patch.object(cli, "RefreshPipeline") as pipeline:
                pipeline.return_value.run.return_value = RefreshSummary(
                    lakes_processed=2, records_created=2
                )
                result, _, _, service = self._run(
                    path,
                    [
                        "refresh", "--sources", "", "--sync-supabase",
                        "--lake-id", self.lake_id, "--lake-id", second_lake,
                    ],
                    report=SyncReport("LOCAL_SAVED", 2, 0, 0, 2, False),
                )
            self.assertEqual(result, 0)
            service.sync_pending.assert_called_once_with(
                [self.lake_id, second_lake], limit=2, force=False
            )
            self.assertEqual(Repository(path).sync_outbox_summary()["REMOTE_PENDING"], 3)


if __name__ == "__main__":
    unittest.main()
