from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from glofguard.cli import load_baseline_lakes
from glofguard.config import PROJECT_ROOT, Settings
from glofguard.pipeline import RefreshPipeline
from glofguard.providers.mock import (
    MockGfsProvider,
    MockGsmapProvider,
    MockPowerProvider,
    MockSentinel2Provider,
)
from glofguard.schemas import SAFETY_NOTICE
from glofguard.storage import Repository


class MockPipelineTests(unittest.TestCase):
    def test_mock_refresh_is_marked_and_idempotent_for_unchanged_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(
                Settings.from_env(PROJECT_ROOT / "does-not-exist.env"),
                data_mode="mock",
                allow_mock_data=True,
                database_path=root / "mock.sqlite3",
                output_csv_path=root / "mock.csv",
                model_version="transparent-conditions-index-test",
            )
            repository = Repository(settings.database_path)
            repository.initialize()
            lakes = load_baseline_lakes(
                PROJECT_ROOT / "tests" / "fixtures" / "baseline_lakes_sample.csv"
            )
            repository.upsert_lakes(lakes[:1])
            fixed_now = datetime(2025, 8, 20, 12, tzinfo=UTC)
            pipeline = RefreshPipeline(
                settings,
                repository,
                MockSentinel2Provider(),
                MockGsmapProvider(),
                MockGfsProvider(),
                MockPowerProvider(),
                now=lambda: fixed_now,
            )
            first = pipeline.run()
            second = pipeline.run()
            self.assertEqual(first.records_created, 1)
            self.assertEqual(second.unchanged_records, 1)
            rows = repository.records()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["source_mode"], "MOCK")
            self.assertEqual(row["safety_notice"], SAFETY_NOTICE)
            self.assertEqual(row["confidence_level"], "Low")
            self.assertIn("MOCK DATA", row["data_quality_warning"])
            self.assertIsNotNone(row["risk_score"])
            self.assertGreaterEqual(row["risk_score"], 0)
            self.assertLessEqual(row["risk_score"], 100)
            self.assertIn(row["satellite_freshness_status"], {"FRESH", "STALE"})
            self.assertIn(
                row["observed_weather_freshness_status"], {"FRESH", "STALE"}
            )
            self.assertIn(row["forecast_freshness_status"], {"FRESH", "STALE"})
            self.assertEqual(row["historical_baseline_status"], "AVAILABLE")
            self.assertTrue(settings.output_csv_path.exists())

    def test_required_safety_notice_is_exact(self) -> None:
        self.assertEqual(
            SAFETY_NOTICE,
            "This research prototype provides environmental risk indicators and is not "
            "an official emergency-warning system. High-risk results require expert verification.",
        )


if __name__ == "__main__":
    unittest.main()
