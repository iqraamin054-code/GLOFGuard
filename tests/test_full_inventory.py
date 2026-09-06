from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from glofguard.config import PROJECT_ROOT, Settings
from glofguard.full_inventory import (
    FullInventoryConfig,
    FullInventoryRunner,
    select_representative_pilot,
    write_representative_pilot_report,
)
from glofguard.storage import Repository
from glofguard.types import (
    DailyWeather,
    ForecastSummary,
    ObservedRainfallSummary,
    QualityStatus,
    SatelliteObservation,
)


FIXED_NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def sample_inventory() -> pd.DataFrame:
    rows = []
    for index in range(1, 4):
        lake_id = f"PKGL-{index:05d}"
        rows.append(
            {
                "source_row_number": index,
                "source_polygon_id": index,
                "lake_id": lake_id,
                "canonical_lake_id": lake_id,
                "processed_sample_id": index,
                "latitude": 36.0 + index / 100,
                "longitude": 74.0 + index / 100,
                "reference_area_km2": 0.1 * index,
                "elevation_m": 4_000 + index,
                "distance_to_nearest_settlement_km": 5.0 + index,
                "source_centroid_latitude": 36.0 + index / 100,
                "source_centroid_longitude": 74.0 + index / 100,
                "source_area_km2": 0.1 * index,
                "geometry_sha256": f"hash-{index}",
                "source_geometry_valid": True,
                "source_validation_warning": None,
                "reconciliation_status": "INCLUDED",
                "reconciliation_reason": "test unique row",
                "eligible_for_processing": True,
            }
        )
    duplicate = dict(rows[0])
    duplicate.update(
        {
            "source_row_number": 4,
            "source_polygon_id": 4,
            "lake_id": "SOURCE-POLYGON-00004",
            "reconciliation_status": "EXCLUDED_EXACT_DUPLICATE",
            "reconciliation_reason": "Exact duplicate of source polygon 1",
            "eligible_for_processing": False,
        }
    )
    rows.append(duplicate)
    return pd.DataFrame(rows)


def source_summary() -> dict[str, object]:
    return {
        "total_source_polygons": 4,
        "processed_unique_records": 3,
        "excluded_exact_duplicate_polygons": 1,
        "invalid_source_geometries_retained": 0,
        "source_archive_sha256": "source-test-hash",
        "processed_csv_sha256": "processed-test-hash",
    }


class SuccessfulSatellite:
    def __init__(self) -> None:
        self.scene_lookup_calls = 0
        self.scene_measurement_calls = 0

    def latest(self, lake, start, end):
        self.scene_lookup_calls += 1
        self.scene_measurement_calls += 1
        return SatelliteObservation(
            lake_id=lake.lake_id,
            observed_at=FIXED_NOW - timedelta(days=2),
            area_km2=lake.reference_area_km2,
            cloud_percentage=8.0,
            clear_fraction=0.92,
            quality_status=QualityStatus.RELIABLE,
            source_image_id=f"REAL-{lake.lake_id}",
            water_index="MNDWI",
        )


class SuccessfulPower:
    def __init__(self) -> None:
        self.request_count = 0

    def daily(self, lake, start, end):
        self.request_count += 1
        return [
            DailyWeather(
                lake_id=lake.lake_id,
                observation_date=start + timedelta(days=offset),
                temperature_c=4.0,
                rainfall_mm=2.0,
            )
            for offset in range((end - start).days + 1)
        ]


class FailingSatellite:
    def __init__(self) -> None:
        self.scene_lookup_calls = 0
        self.scene_measurement_calls = 0

    def latest(self, lake, start, end):
        self.scene_lookup_calls += 1
        raise RuntimeError("simulated Earth Engine request failure")


class MockReturningSatellite(SuccessfulSatellite):
    def latest(self, lake, start, end):
        observation = super().latest(lake, start, end)
        return SatelliteObservation(
            **{
                **observation.__dict__,
                "source": "MOCK Sentinel test provider",
                "is_mock": True,
            }
        )


class SuccessfulGsmap:
    def __init__(self) -> None:
        self.summary_calls = 0

    def summary(self, lake, now):
        self.summary_calls += 1
        return ObservedRainfallSummary(
            lake_id=lake.lake_id,
            observation_time=FIXED_NOW - timedelta(hours=2),
            rainfall_last_24h_mm=4.0,
            rainfall_last_7d_mm=20.0,
            rainfall_last_30d_mm=60.0,
            available_hours_24h=24,
            available_hours_7d=168,
            available_hours_30d=720,
            product_status="provisional",
        )


class SuccessfulGfs:
    def __init__(self) -> None:
        self.summary_calls = 0

    def summary(self, lake, now):
        self.summary_calls += 1
        return ForecastSummary(
            lake_id=lake.lake_id,
            forecast_created_at=FIXED_NOW - timedelta(hours=6),
            forecast_rainfall_next_72h_mm=12.0,
            forecast_temperature_next_24h_c=5.0,
            forecast_temperature_next_7d_c=4.0,
            precipitation_steps=12,
            temperature_steps_24h=24,
            temperature_steps_7d=168,
            forecast_horizon_hours=168,
        )


class HistoricalPower:
    def __init__(self) -> None:
        self.request_count = 0

    def daily(self, lake, start, end):
        self.request_count += 1
        rows = []
        for year in (2022, 2023, 2024, 2025, 2026):
            final_day = 29 if year == 2026 else 31
            for day in range(1, final_day + 1):
                observation_date = date(year, 8, day)
                if start <= observation_date <= end:
                    rows.append(
                        DailyWeather(
                            lake_id=lake.lake_id,
                            observation_date=observation_date,
                            temperature_c=3.0 + (year - 2022) * 0.2,
                            rainfall_mm=1.0,
                        )
                    )
        return rows


class FullInventoryTests(unittest.TestCase):
    def make_runner(
        self,
        root: Path,
        repository: Repository,
        satellite,
        power,
        *,
        max_lakes: int = 1,
        max_retries: int = 3,
    ) -> FullInventoryRunner:
        settings = replace(
            Settings.from_env(PROJECT_ROOT / "does-not-exist.env"),
            data_mode="real",
            allow_mock_data=False,
            database_path=repository.path,
            satellite_stale_days=20,
            weather_stale_hours=36,
        )
        return FullInventoryRunner(
            settings=settings,
            repository=repository,
            inventory=sample_inventory(),
            source_summary=source_summary(),
            satellite_provider=satellite,
            power_provider=power,
            config=FullInventoryConfig(
                as_of_date=date(2026, 9, 1),
                batch_size=1,
                max_lakes=max_lakes,
                max_retries=max_retries,
                backoff_base_seconds=0,
                backoff_cap_seconds=0,
                power_lookback_days=30,
            ),
            output_dir=root / "output",
            sleep=lambda seconds: None,
            now=lambda: FIXED_NOW,
        )

    def test_checkpoint_resume_and_duplicate_prevention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Repository(root / "inventory.sqlite3")
            repository.initialize()

            for expected_successes in (1, 2, 3):
                satellite = SuccessfulSatellite()
                power = SuccessfulPower()
                runner = self.make_runner(root, repository, satellite, power)
                summary = runner.run()
                self.assertEqual(summary["successfully_processed_lakes"], expected_successes)
                self.assertEqual(satellite.scene_lookup_calls, 1)
                self.assertEqual(power.request_count, 1)
                successful_rows = [
                    row
                    for row in repository.full_inventory_statuses(runner.run_id)
                    if row["processing_status"] == "SUCCESS"
                ]
                self.assertTrue(
                    all(row["source_mode"] == "REAL" for row in successful_rows)
                )

            satellite = SuccessfulSatellite()
            power = SuccessfulPower()
            runner = self.make_runner(root, repository, satellite, power)
            final_summary = runner.run()
            self.assertEqual(final_summary["report_state"], "COMPLETE")
            self.assertEqual(satellite.scene_lookup_calls, 0)
            self.assertEqual(power.request_count, 0)

            statuses = repository.full_inventory_statuses(runner.run_id)
            self.assertEqual(len(statuses), 4)
            self.assertEqual(len({row["lake_id"] for row in statuses}), 4)
            self.assertEqual(
                sum(row["processing_status"] == "EXCLUDED_DUPLICATE" for row in statuses),
                1,
            )
            with repository.connection() as connection:
                satellite_count = connection.execute(
                    "SELECT COUNT(*) FROM satellite_observations"
                ).fetchone()[0]
            self.assertEqual(satellite_count, 3)
            self.assertTrue((root / "output" / "full_inventory_checkpoint.json").exists())

    def test_failed_requests_are_retried_and_recorded_without_mock_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Repository(root / "inventory.sqlite3")
            repository.initialize()
            satellite = FailingSatellite()
            runner = self.make_runner(
                root, repository, satellite, SuccessfulPower(), max_retries=3
            )
            summary = runner.run()
            statuses = repository.full_inventory_statuses(runner.run_id)
            failed = next(row for row in statuses if row["lake_id"] == "PKGL-00001")
            self.assertEqual(summary["failed_lakes"], 1)
            self.assertEqual(satellite.scene_lookup_calls, 3)
            self.assertEqual(failed["processing_status"], "FAILED")
            self.assertEqual(failed["source_mode"], "UNAVAILABLE")
            self.assertIn("no mock fallback", failed["failure_or_stale_warning"].lower())
            self.assertEqual(len(repository.full_inventory_errors(runner.run_id)), 3)

    def test_mock_provider_result_becomes_per_lake_failure_and_run_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Repository(root / "inventory.sqlite3")
            repository.initialize()
            runner = self.make_runner(
                root,
                repository,
                MockReturningSatellite(),
                SuccessfulPower(),
                max_lakes=2,
            )
            summary = runner.run()
            statuses = repository.full_inventory_statuses(runner.run_id)
            failed = [row for row in statuses if row["processing_status"] == "FAILED"]
            self.assertEqual(summary["failed_lakes"], 2)
            self.assertEqual(len(failed), 2)
            self.assertTrue(all(row["source_mode"] == "UNAVAILABLE" for row in failed))
            self.assertEqual(summary["mock_count"], 0)

    def test_representative_100_selection_is_deterministic_and_covers_all_bands(self) -> None:
        rows = []
        lake_number = 1
        for latitude_band in range(3):
            for longitude_band in range(3):
                for elevation_band in range(3):
                    for area_band in range(3):
                        for replicate in range(3):
                            rows.append(
                                {
                                    "lake_id": f"SYN-{lake_number:04d}",
                                    "canonical_lake_id": f"SYN-{lake_number:04d}",
                                    "source_polygon_id": lake_number,
                                    "latitude": 34 + latitude_band + replicate / 1000,
                                    "longitude": 72 + longitude_band + replicate / 1000,
                                    "elevation_m": 3000 + 700 * elevation_band + replicate,
                                    "reference_area_km2": (
                                        0.0001 * (10 ** area_band) + replicate / 1_000_000
                                    ),
                                    "eligible_for_processing": True,
                                }
                            )
                            lake_number += 1
        inventory = pd.DataFrame(rows)
        first = select_representative_pilot(inventory, sample_size=100, seed=42)
        second = select_representative_pilot(inventory, sample_size=100, seed=42)
        self.assertEqual(first["lake_id"].tolist(), second["lake_id"].tolist())
        self.assertEqual(len(first), 100)
        self.assertEqual(first["lake_id"].nunique(), 100)
        self.assertEqual(first["pilot_stratum"].nunique(), 81)
        for column in (
            "latitude_band",
            "longitude_band",
            "elevation_band",
            "area_band",
        ):
            self.assertEqual(first[column].nunique(), 3)

    def test_representative_pilot_report_counts_real_stale_failed_and_mock(self) -> None:
        rows = []
        for index in range(100):
            rows.append(
                {
                    "pilot_order": index + 1,
                    "lake_id": f"P-{index:03d}",
                    "source_polygon_id": index + 1,
                    "latitude": 35 + index / 1000,
                    "longitude": 74 + index / 1000,
                    "elevation_m": 3500 + index,
                    "reference_area_km2": 0.001 + index / 100000,
                    "latitude_band": "CENTRAL",
                    "longitude_band": "CENTRAL",
                    "elevation_band": "MEDIUM",
                    "area_band": "MEDIUM",
                    "pilot_stratum": "CENTRAL|CENTRAL|MEDIUM|MEDIUM",
                    "stratum_population": 100,
                    "stratum_pilot_count": 100,
                    "expansion_weight": 1.0,
                    "selection_seed": 42,
                }
            )
        selection = pd.DataFrame(rows)
        statuses = []
        for index, lake_id in enumerate(selection["lake_id"]):
            if index < 60:
                processing_status, source_mode = "SUCCESS", "REAL"
            elif index < 80:
                processing_status, source_mode = "STALE", "REAL"
            elif index < 90:
                processing_status, source_mode = "NO_USABLE_IMAGERY", "UNAVAILABLE"
            else:
                processing_status, source_mode = "FAILED", "UNAVAILABLE"
            statuses.append(
                {
                    "lake_id": lake_id,
                    "processing_status": processing_status,
                    "source_mode": source_mode,
                }
            )
        usage = {
            "processed_lake_attempts": 100,
            "elapsed_seconds": 1000.0,
            "sentinel_scene_lookup_calls": 100,
            "sentinel_scene_measurement_calls": 400,
            "nasa_power_http_requests": 100,
        }
        with tempfile.TemporaryDirectory() as directory:
            summary = write_representative_pilot_report(
                output_dir=Path(directory),
                selection=selection,
                statuses=pd.DataFrame(statuses),
                source_summary={"processed_unique_records": 8806},
                usage=usage,
                errors=pd.DataFrame(),
                exact_command="test command",
            )
            self.assertEqual(summary["fresh_real_observations"], 60)
            self.assertEqual(summary["stale_real_observations"], 20)
            self.assertEqual(summary["no_usable_imagery"], 10)
            self.assertEqual(summary["provider_failures"], 10)
            self.assertEqual(summary["mock_count"], 0)
            self.assertEqual(
                summary["estimated_full_run_real_usable_rate_percent"], 80.0
            )
            self.assertTrue(
                (Path(directory) / "REPRESENTATIVE_100_PILOT_REPORT.md").exists()
            )

    def test_power_publication_delay_does_not_control_live_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Repository(root / "corrected.sqlite3")
            repository.initialize()
            settings = replace(
                Settings.from_env(PROJECT_ROOT / "does-not-exist.env"),
                data_mode="real",
                allow_mock_data=False,
                database_path=repository.path,
                satellite_stale_days=20,
                weather_stale_hours=36,
                forecast_stale_hours=12,
                sentinel_area_lookback_days=180,
            )
            runner = FullInventoryRunner(
                settings=settings,
                repository=repository,
                inventory=sample_inventory(),
                source_summary=source_summary(),
                satellite_provider=SuccessfulSatellite(),
                power_provider=HistoricalPower(),
                observed_weather_provider=SuccessfulGsmap(),
                forecast_provider=SuccessfulGfs(),
                config=FullInventoryConfig(
                    as_of_date=date(2026, 9, 1),
                    batch_size=1,
                    max_lakes=1,
                    max_retries=1,
                    backoff_base_seconds=0,
                    backoff_cap_seconds=0,
                    power_lookback_days=30,
                ),
                output_dir=root / "output",
                sleep=lambda seconds: None,
                now=lambda: FIXED_NOW,
            )
            runner.run()
            status = next(
                row
                for row in repository.full_inventory_statuses(runner.run_id)
                if row["lake_id"] == "PKGL-00001"
            )
            self.assertEqual(status["processing_status"], "AVAILABLE")
            self.assertEqual(status["source_mode"], "REAL")
            self.assertEqual(status["satellite_area_freshness"], "FRESH")
            self.assertEqual(status["observed_weather_freshness"], "FRESH")
            self.assertEqual(status["forecast_freshness"], "FRESH")
            self.assertEqual(
                status["historical_baseline_availability"], "AVAILABLE"
            )
            self.assertEqual(status["historical_baseline_latest_date"], "2026-08-29")


if __name__ == "__main__":
    unittest.main()
