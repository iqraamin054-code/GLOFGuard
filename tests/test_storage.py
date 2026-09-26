from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from glofguard.storage import Repository
from glofguard.schemas import TimeSeriesRecord
from glofguard.types import QualityStatus, SatelliteObservation


class StorageTests(unittest.TestCase):
    def test_canonical_records_have_one_latest_row_per_lake_and_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(Path(directory) / "test.sqlite3")
            repository.initialize()
            first = TimeSeriesRecord(
                lake_id="L1",
                observation_date=date(2025, 1, 1),
                latitude=35.0,
                longitude=74.0,
                input_signature="first",
            )
            corrected = TimeSeriesRecord(
                lake_id="L1",
                observation_date=date(2025, 1, 1),
                latitude=35.0,
                longitude=74.0,
                rainfall_last_24h=4.5,
                input_signature="corrected",
            )
            next_day = TimeSeriesRecord(
                lake_id="L1",
                observation_date=date(2025, 1, 2),
                latitude=35.0,
                longitude=74.0,
                rainfall_last_24h=4.5,
                input_signature="corrected",
            )
            self.assertTrue(repository.save_record_if_changed(first))
            self.assertTrue(repository.save_record_if_changed(corrected))
            self.assertTrue(repository.save_record_if_changed(next_day))
            self.assertFalse(repository.save_record_if_changed(next_day))
            rows = repository.records()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["rainfall_last_24h"], 4.5)

    def test_rejected_cloudy_area_does_not_replace_reliable_area(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Repository(Path(directory) / "test.sqlite3")
            repository.initialize()
            reliable_time = datetime(2025, 1, 1, tzinfo=UTC)
            repository.store_satellite(
                SatelliteObservation(
                    lake_id="L1",
                    observed_at=reliable_time,
                    area_km2=0.5,
                    cloud_percentage=10,
                    clear_fraction=0.9,
                    quality_status=QualityStatus.RELIABLE,
                    source_image_id="REAL-1",
                    water_index="MNDWI",
                )
            )
            repository.store_satellite(
                SatelliteObservation(
                    lake_id="L1",
                    observed_at=reliable_time + timedelta(days=5),
                    area_km2=None,
                    cloud_percentage=80,
                    clear_fraction=0.2,
                    quality_status=QualityStatus.REJECTED,
                    source_image_id="CLOUDY-2",
                    water_index="MNDWI",
                    rejection_reason="cloud percentage above configured limit",
                )
            )
            history = repository.satellite_history("L1")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[-1].area_km2, 0.5)
            self.assertEqual(history[-1].source_image_id, "REAL-1")


if __name__ == "__main__":
    unittest.main()
