from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from glofguard.schemas import TimeSeriesRecord
from glofguard.security import redact_sensitive_text
from glofguard.storage import Repository
from glofguard.supabase_persistence import (
    HttpResponse,
    SupabaseAuthenticationError,
    SupabaseConfig,
    SupabaseConfigurationError,
    SupabaseRemoteError,
    SupabaseSyncService,
    SupabaseWriter,
    _RejectRedirects,
)


class ScriptedTransport:
    """Dependency-free transport that records no authorization values."""

    def __init__(self, outcomes: list[HttpResponse | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: object | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        parsed = urlparse(url)
        self.calls.append(
            {
                "method": method,
                "endpoint": parsed.path.rsplit("/", 1)[-1],
                "query": parse_qs(parsed.query),
                "header_names": tuple(sorted(headers)),
                "json_body": json_body,
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self.outcomes:
            raise AssertionError("unexpected Supabase request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class InMemoryTransport:
    """A tiny idempotent remote model used to check outgoing conflict keys."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.observation_ids: set[str] = set()
        self.freshness_keys: set[tuple[str, str]] = set()
        self.run_ids: set[str] = set()
        self.tables: dict[str, dict[tuple[object, ...], dict[str, object]]] = {}

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: object | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        parsed = urlparse(url)
        endpoint = parsed.path.rsplit("/", 1)[-1]
        query = parse_qs(parsed.query)
        self.calls.append(
            {
                "method": method,
                "endpoint": endpoint,
                "query": query,
                "header_names": tuple(sorted(headers)),
                "json_body": json_body,
            }
        )
        if endpoint == "lakes" and method == "GET":
            lake_id = query.get("lake_id", ["eq."])[0].removeprefix("eq.")
            return HttpResponse(200, [{"lake_id": lake_id}])
        table = self.tables.setdefault(endpoint, {})
        identity = {
            "ingestion_runs": ("run_id",),
            "environmental_observations": ("observation_id",),
            "source_freshness": ("observation_id", "source_name"),
            "processing_queue": ("lake_id",),
        }[endpoint]

        def matches(row: dict[str, object]) -> bool:
            for column, values in query.items():
                condition = values[0]
                if condition.startswith("eq.") and str(row.get(column)) != condition[3:]:
                    return False
                if condition == "not.is.null" and row.get(column) is None:
                    return False
            return True

        if method == "GET":
            columns = query.get("select", ["*"])[0].split(",")
            selected = [dict(row) for row in table.values() if matches(row)]
            if columns != ["*"]:
                selected = [{key: row[key] for key in columns if key in row} for row in selected]
            return HttpResponse(200, selected[:int(query.get("limit", ["1000"])[0])])
        if method == "PATCH":
            updated = []
            for row in table.values():
                if matches(row):
                    row.update(json_body)
                    updated.append(dict(row))
            return HttpResponse(200, updated)
        rows = json_body if isinstance(json_body, list) else []
        for row in rows:
            key = tuple(row[column] for column in identity)
            table.setdefault(key, json.loads(json.dumps(row)))
        if endpoint == "ingestion_runs" and method == "POST":
            self.run_ids.update(str(row["run_id"]) for row in rows if isinstance(row, dict))
        elif endpoint == "environmental_observations" and method == "POST":
            self.observation_ids.update(
                str(row["observation_id"]) for row in rows if isinstance(row, dict)
            )
        elif endpoint == "source_freshness" and method == "POST":
            self.freshness_keys.update(
                (str(row["observation_id"]), str(row["source_name"]))
                for row in rows
                if isinstance(row, dict)
            )
        return HttpResponse(201 if method == "POST" else 200, [])


class SupabasePersistenceTests(unittest.TestCase):
    lake_id = "PKGL-00995"

    def _config(self, *, retry_attempts: int = 1) -> SupabaseConfig:
        return SupabaseConfig(
            url="https://unit-test-project.supabase.co",
            secret_key="sb_secret_unit_test_only",
            expected_project_ref="unit-test-project",
            retry_attempts=retry_attempts,
            backoff_base_seconds=0.5,
            backoff_cap_seconds=2.0,
        )

    def _record(self, *, source_mode: str = "REAL") -> TimeSeriesRecord:
        return TimeSeriesRecord(
            lake_id=self.lake_id,
            observation_date=date(2026, 9, 8),
            latitude=35.4,
            longitude=74.6,
            area_current_km2=0.8,
            rainfall_last_24h=3.2,
            rainfall_last_7d=11.0,
            rainfall_last_30d=36.5,
            forecast_rainfall_next_72h=7.4,
            elevation=4120.0,
            satellite_observation_date=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
            weather_observation_date=datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
            forecast_creation_time=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
            historical_baseline_observation_date=date(2026, 9, 7),
            satellite_freshness_status="STALE",
            observed_weather_freshness_status="FRESH",
            forecast_freshness_status="FRESH",
            historical_baseline_status="AVAILABLE",
            data_quality_status="RELIABLE",
            risk_score=33.0,
            risk_level="Low",
            confidence_level="Medium",
            prediction_timestamp=datetime(2026, 9, 8, 10, 15, tzinfo=UTC),
            model_version="unit-test-model",
            input_signature="unit-test-signature",
            source_mode=source_mode,
        )

    def _queued_item(self, database: Path) -> tuple[Repository, object]:
        repository = Repository(database)
        repository.initialize()
        self.assertTrue(repository.save_record_if_changed(self._record(), queue_supabase=True))
        items = repository.pending_sync_outbox([self.lake_id], limit=1, force=True)
        self.assertEqual(len(items), 1)
        return repository, items[0]

    def test_missing_server_credentials_fail_closed_without_public_fallback(self) -> None:
        with self.assertRaisesRegex(SupabaseConfigurationError, "SUPABASE_URL"):
            SupabaseConfig.from_mapping({})
        with self.assertRaisesRegex(SupabaseConfigurationError, "SUPABASE_SECRET_KEY"):
            SupabaseConfig.from_mapping(
                {
                    "SUPABASE_URL": "https://unit-test-project.supabase.co",
                    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "public-value-is-not-a-server-key",
                }
            )
        with self.assertRaisesRegex(SupabaseConfigurationError, "sb_secret_"):
            SupabaseConfig.from_mapping(
                {
                    "SUPABASE_URL": "https://unit-test-project.supabase.co",
                    "SUPABASE_SECRET_KEY": "sb_publishable_not_accepted",
                }
            )

    def test_authenticated_401_marks_remote_failed_without_retry_or_mock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, _ = self._queued_item(Path(directory) / "state.sqlite3")
            sleeps: list[float] = []
            transport = ScriptedTransport(
                [HttpResponse(401, {"message": "authorization failed apikey=sb_secret_should_hide"})]
            )
            service = SupabaseSyncService(
                repository,
                SupabaseWriter(self._config(retry_attempts=3), transport=transport, sleep=sleeps.append),
            )

            report = service.sync_pending([self.lake_id], limit=1)

            self.assertEqual(report.remote_failed, 1)
            self.assertEqual(report.remote_pending, 0)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(sleeps, [])
            self.assertEqual(repository.sync_outbox_summary()["REMOTE_FAILED"], 1)
            with repository.connection() as connection:
                row = connection.execute(
                    "SELECT last_error_message FROM sync_outbox WHERE lake_id=?", (self.lake_id,)
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertNotIn("sb_secret_should_hide", str(row["last_error_message"]))
            self.assertEqual(self._record().source_mode, "REAL")

    def test_network_failure_retries_with_bounded_exponential_backoff(self) -> None:
        sleeps: list[float] = []
        transport = ScriptedTransport(
            [OSError("temporary network failure"), OSError("temporary network failure"), HttpResponse(200, [])]
        )
        writer = SupabaseWriter(self._config(retry_attempts=3), transport=transport, sleep=sleeps.append)

        result = writer.connectivity_check()

        self.assertTrue(result.schema_available)
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(sleeps, [0.5, 1.0])
        self.assertEqual(
            [call["endpoint"] for call in transport.calls], ["lakes", "lakes", "lakes"]
        )

    def test_replayed_item_uses_stable_ids_and_conflict_targets_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, item = self._queued_item(Path(directory) / "state.sqlite3")
            transport = InMemoryTransport()
            writer = SupabaseWriter(self._config(), transport=transport)

            first = writer.sync_item(item)  # type: ignore[arg-type]
            second = writer.sync_item(item)  # type: ignore[arg-type]

            self.assertEqual(first["observation_id"], second["observation_id"])
            self.assertEqual(len(transport.observation_ids), 1)
            self.assertEqual(len(transport.run_ids), 1)
            self.assertEqual(len(transport.freshness_keys), 4)
            observation_calls = [
                call for call in transport.calls
                if call["endpoint"] == "environmental_observations" and call["method"] == "POST"
            ]
            self.assertEqual(len(observation_calls), 2)
            self.assertEqual(
                [call["query"].get("on_conflict") for call in observation_calls],
                [["observation_id"], ["observation_id"]],
            )
            self.assertFalse(any(call["endpoint"] == "lakes" and call["method"] == "POST" for call in transport.calls))

    def test_outbox_resumes_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite3"
            repository, _ = self._queued_item(database)
            first_transport = ScriptedTransport(
                [HttpResponse(200, [{"lake_id": self.lake_id}]), OSError("connection lost")]
            )
            first_service = SupabaseSyncService(
                repository, SupabaseWriter(self._config(), transport=first_transport)
            )
            first_report = first_service.sync_pending([self.lake_id], limit=1)
            self.assertEqual(first_report.remote_pending, 1)

            resumed_repository = Repository(database)
            resumed_repository.initialize()
            resumed_transport = InMemoryTransport()
            resumed_service = SupabaseSyncService(
                resumed_repository, SupabaseWriter(self._config(), transport=resumed_transport)
            )
            resumed_report = resumed_service.sync_pending([self.lake_id], limit=1, force=True)
            no_op_report = resumed_service.sync_pending([self.lake_id], limit=1)

            self.assertEqual(resumed_report.remote_saved, 1)
            self.assertEqual(no_op_report.queued_events, 0)
            self.assertEqual(resumed_repository.sync_outbox_summary()["REMOTE_SAVED"], 1)
            self.assertEqual(len(resumed_transport.observation_ids), 1)

    def test_partial_remote_failure_keeps_item_retryable_then_completes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, _ = self._queued_item(Path(directory) / "state.sqlite3")
            class PartialFailure(InMemoryTransport):
                failed = False

                def request(self, method, url, **kwargs):
                    if urlparse(url).path.endswith("/source_freshness") and not self.failed:
                        self.failed = True
                        return HttpResponse(503, {"message": "source freshness temporarily unavailable"})
                    return super().request(method, url, **kwargs)

            first_transport = PartialFailure()
            first_service = SupabaseSyncService(
                repository, SupabaseWriter(self._config(), transport=first_transport)
            )

            failed_report = first_service.sync_pending([self.lake_id], limit=1)

            self.assertEqual(failed_report.remote_pending, 1)
            self.assertEqual(repository.sync_outbox_summary()["REMOTE_PENDING"], 1)
            self.assertEqual(len(first_transport.observation_ids), 1)
            self.assertEqual(len(first_transport.freshness_keys), 0)

            resumed_transport = first_transport
            resumed_service = SupabaseSyncService(
                repository, SupabaseWriter(self._config(), transport=resumed_transport)
            )
            completed_report = resumed_service.sync_pending([self.lake_id], limit=1, force=True)

            self.assertEqual(completed_report.remote_saved, 1)
            self.assertEqual(repository.sync_outbox_summary()["REMOTE_SAVED"], 1)
            self.assertEqual(len(resumed_transport.observation_ids), 1)
            self.assertEqual(len(resumed_transport.freshness_keys), 4)

    def test_source_specific_freshness_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, item = self._queued_item(Path(directory) / "state.sqlite3")
            writer = SupabaseWriter(self._config(), transport=InMemoryTransport())

            rows = {row["source_name"]: row for row in writer._bundle(item)["freshness"]}  # type: ignore[index,arg-type]

            self.assertEqual(rows["SENTINEL_2"]["freshness_status"], "STALE")
            self.assertEqual(rows["JAXA_GSMAP"]["freshness_status"], "FRESH")
            self.assertEqual(rows["NOAA_GFS"]["freshness_status"], "FRESH")
            self.assertEqual(rows["NASA_POWER"]["freshness_status"], "NOT_APPLICABLE")
            self.assertNotIn("combined_freshness", json.dumps(rows, sort_keys=True))

    def test_mock_records_never_enter_outbox(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(Path(directory) / "state.sqlite3")
            repository.initialize()

            with self.assertRaisesRegex(ValueError, "Only REAL"):
                repository.save_record_if_changed(self._record(source_mode="MOCK"), queue_supabase=True)

            self.assertEqual(repository.records(), [])
            self.assertEqual(repository.sync_outbox_summary()["REMOTE_PENDING"], 0)

    def test_existing_record_dry_run_preview_is_latest_and_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(Path(directory) / "state.sqlite3")
            repository.initialize()
            older = self._record()
            newer = replace(
                older,
                observation_date=date(2026, 9, 9),
                prediction_timestamp=datetime(2026, 9, 9, 10, 15, tzinfo=UTC),
                input_signature="unit-test-newer-signature",
            )
            self.assertTrue(repository.save_record_if_changed(older))
            self.assertTrue(repository.save_record_if_changed(newer))

            preview = repository.preview_latest_real_records([self.lake_id], limit=1)

            self.assertEqual(len(preview), 1)
            self.assertEqual(
                preview[0].payload["record"]["observation_date"], "2026-09-09"  # type: ignore[index]
            )
            self.assertEqual(repository.sync_outbox_summary()["REMOTE_PENDING"], 0)

    def test_secret_value_redaction_applies_to_sqlite_errors_and_repr(self) -> None:
        synthetic_secret = "sb_secret_synthetic_value"
        raw_error = (
            f"Authorization: Bearer {synthetic_secret}; password=synthetic-password; "
            "https://user:pass@example.invalid/path?token=synthetic-token"
        )
        redacted = redact_sensitive_text(raw_error)
        self.assertNotIn(synthetic_secret, redacted)
        self.assertNotIn("synthetic-password", redacted)
        self.assertNotIn("user:pass", redacted)

        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(Path(directory) / "state.sqlite3")
            repository.initialize()
            repository.record_failure(self.lake_id, "unit-test", RuntimeError(raw_error))
            with repository.connection() as connection:
                stored = connection.execute(
                    "SELECT error_message FROM ingestion_failures ORDER BY id DESC LIMIT 1"
                ).fetchone()["error_message"]
            self.assertNotIn(synthetic_secret, stored)
            self.assertNotIn("synthetic-password", stored)
        self.assertNotIn("sb_secret_unit_test_only", repr(self._config()))

    def test_configuration_rejects_credential_routing_and_unverified_identity(self) -> None:
        for url in (
            "https://example.invalid", "https://user:pass@unit-test-project.supabase.co",
            "https://unit-test-project.supabase.co/path", "https://unit-test-project.supabase.co?token=value",
            "https://unit-test-project.supabase.co#fragment", "http://unit-test-project.supabase.co",
            "https://unit-test-project.supabase.co:444",
        ):
            with self.subTest(url=url), self.assertRaises(SupabaseConfigurationError):
                replace(self._config(), url=url)
        with self.assertRaises(SupabaseConfigurationError):
            replace(self._config(), expected_project_ref="different-project")
        self.assertIsNone(replace(self._config(), expected_project_ref=None).safe_identity["project_ref_matches_expected"])
        self.assertIsNone(_RejectRedirects().redirect_request(None, None, 302, "", {}, "https://example.invalid"))

    def test_table_checks_do_not_assume_lake_id_column_and_preserve_403(self) -> None:
        transport = ScriptedTransport([HttpResponse(200, []) for _ in range(6)])
        tables = SupabaseWriter(self._config(), transport=transport).inspect_required_tables()
        self.assertEqual(tables["ingestion_runs"], "PRESENT")
        self.assertTrue(all(call["query"]["select"] == ["*"] for call in transport.calls))
        self.assertTrue(all("apikey" in call["header_names"] for call in transport.calls))
        self.assertTrue(all("Authorization" not in call["header_names"] for call in transport.calls))
        writer = SupabaseWriter(self._config(), transport=ScriptedTransport([HttpResponse(403, {})]))
        with self.assertRaises(SupabaseAuthenticationError) as caught:
            writer.connectivity_check()
        self.assertEqual(caught.exception.status_code, 403)

    def test_successful_http_without_persisted_observation_is_not_remote_saved(self) -> None:
        class DiscardObservation(InMemoryTransport):
            def request(self, method, url, **kwargs):
                if method == "POST" and urlparse(url).path.endswith("/environmental_observations"):
                    return HttpResponse(201, [])
                return super().request(method, url, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            repository, _ = self._queued_item(Path(directory) / "state.sqlite3")
            service = SupabaseSyncService(repository, SupabaseWriter(self._config(), transport=DiscardObservation()))
            report = service.sync_pending([self.lake_id], limit=1)
            self.assertEqual((report.remote_saved, report.remote_pending), (0, 1))
            self.assertEqual(repository.sync_outbox_summary()["REMOTE_SAVED"], 0)

    def test_conflicting_remote_values_and_duplicates_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, item = self._queued_item(Path(directory) / "state.sqlite3")
            transport = InMemoryTransport()
            writer = SupabaseWriter(self._config(), transport=transport)
            writer.sync_item(item)
            row = next(iter(transport.tables["environmental_observations"].values()))
            row["source_mode"] = "MOCK"
            with self.assertRaisesRegex(SupabaseRemoteError, "source_mode"):
                writer.sync_item(item)
            self.assertEqual(row["source_mode"], "MOCK")  # Refuse the conflict; do not overwrite history.
            with self.assertRaisesRegex(SupabaseRemoteError, "duplicate or extra"):
                SupabaseWriter(self._config(), transport=ScriptedTransport([
                    HttpResponse(200, [{"lake_id": self.lake_id}, {"lake_id": self.lake_id}]),
                ]))._verify_rows("lakes", {}, [{"lake_id": self.lake_id}], ("lake_id",))

    def test_zero_row_completion_patch_does_not_mark_remote_saved(self) -> None:
        class IgnoreCompletion(InMemoryTransport):
            def request(self, method, url, **kwargs):
                if method == "PATCH":
                    return HttpResponse(200, [])
                return super().request(method, url, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            repository, _ = self._queued_item(Path(directory) / "state.sqlite3")
            report = SupabaseSyncService(repository, SupabaseWriter(self._config(), transport=IgnoreCompletion())).sync_pending([self.lake_id], limit=1)
            self.assertEqual(report.remote_saved, 0)
            self.assertEqual(report.remote_pending, 1)

    def test_ambiguous_committed_write_resumes_without_duplicate(self) -> None:
        class LostAcknowledgement(InMemoryTransport):
            interrupted = False

            def request(self, method, url, **kwargs):
                response = super().request(method, url, **kwargs)
                if method == "POST" and urlparse(url).path.endswith("/environmental_observations") and not self.interrupted:
                    self.interrupted = True
                    raise OSError("connection lost after server committed")
                return response

        with tempfile.TemporaryDirectory() as directory:
            repository, _ = self._queued_item(Path(directory) / "state.sqlite3")
            transport = LostAcknowledgement()
            service = SupabaseSyncService(repository, SupabaseWriter(self._config(), transport=transport))
            self.assertEqual(service.sync_pending([self.lake_id], limit=1).remote_pending, 1)
            self.assertEqual(len(transport.observation_ids), 1)
            self.assertEqual(service.sync_pending([self.lake_id], limit=1, force=True).remote_saved, 1)
            self.assertEqual(len(transport.observation_ids), 1)
            self.assertEqual(len(transport.freshness_keys), 4)

    def test_replay_preserves_source_ages_and_completed_run_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, item = self._queued_item(Path(directory) / "state.sqlite3")
            transport = InMemoryTransport()
            first = SupabaseWriter(self._config(), transport=transport, now=lambda: datetime(2026, 9, 9, tzinfo=UTC))
            first.sync_item(item)
            completed = next(iter(transport.tables["ingestion_runs"].values()))["completed_at"]
            later = SupabaseWriter(self._config(), transport=transport, now=lambda: datetime(2026, 9, 13, tzinfo=UTC))
            receipt = later.sync_item(item)
            self.assertEqual(next(iter(transport.tables["ingestion_runs"].values()))["completed_at"], completed)
            self.assertEqual(first._bundle(item)["freshness"], later._bundle(item)["freshness"])
            self.assertTrue(receipt["read_back_verified"])
            self.assertEqual(receipt["source_mode"], "REAL")
            self.assertTrue(first._stored_value_matches("retrieved_at", "2026-09-08T10:15:00+00:00", "2026-09-08T15:15:00+05:00"))

    def test_payload_corruption_is_rejected_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _, item = self._queued_item(Path(directory) / "state.sqlite3")
            transport = ScriptedTransport([])
            with self.assertRaisesRegex(SupabaseRemoteError, "hash mismatch"):
                SupabaseWriter(self._config(), transport=transport).sync_item(replace(item, payload_sha256="corrupted"))
            self.assertEqual(transport.calls, [])

    def test_changed_input_creates_history_and_small_batches_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, _ = self._queued_item(Path(directory) / "state.sqlite3")
            repository.save_record_if_changed(replace(self._record(), input_signature="new-version"), queue_supabase=True)
            transport = InMemoryTransport()
            service = SupabaseSyncService(repository, SupabaseWriter(replace(self._config(), batch_size=1), transport=transport))
            self.assertEqual(service.sync_pending([self.lake_id], limit=1).remote_saved, 1)
            self.assertEqual(service.sync_pending([self.lake_id], limit=1).remote_saved, 1)
            self.assertEqual(len(transport.observation_ids), 2)
            self.assertTrue(all(len(call["json_body"]) == 1 for call in transport.calls if call["method"] == "POST"))


if __name__ == "__main__":
    unittest.main()
