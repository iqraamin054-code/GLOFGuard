from __future__ import annotations

import unittest
import csv
from datetime import date
from pathlib import Path

import pandas as pd

from glofguard.schemas import REQUIRED_TIME_SERIES_COLUMNS, TIME_SERIES_COLUMNS
from glofguard.validation_split import time_geographic_split


class SchemaAndValidationTests(unittest.TestCase):
    def test_required_time_series_fields_and_no_fabricated_targets(self) -> None:
        expected = {
            "lake_id",
            "observation_date",
            "area_change_30d",
            "forecast_rainfall_next_72h",
            "risk_score",
            "risk_level",
            "prediction_timestamp",
            "model_version",
        }
        self.assertTrue(expected.issubset(REQUIRED_TIME_SERIES_COLUMNS))
        self.assertNotIn("glof_within_next_7_days", TIME_SERIES_COLUMNS)
        self.assertNotIn("glof_within_next_30_days", TIME_SERIES_COLUMNS)
        self.assertTrue(
            {
                "satellite_freshness_status",
                "observed_weather_freshness_status",
                "forecast_freshness_status",
                "historical_baseline_status",
            }.issubset(TIME_SERIES_COLUMNS)
        )
        schema_path = Path(__file__).resolve().parents[1] / "data" / "time_series_schema.csv"
        with schema_path.open(encoding="utf-8", newline="") as handle:
            self.assertEqual(next(csv.reader(handle)), TIME_SERIES_COLUMNS)

    def test_time_and_geographic_split_has_no_overlap(self) -> None:
        frame = pd.DataFrame(
            {
                "observation_date": [
                    "2022-01-01",
                    "2022-02-01",
                    "2022-03-01",
                    "2024-01-01",
                    "2024-02-01",
                ],
                "geographic_group": ["A", "B", "C", "C", "D"],
                "value": [1, 2, 3, 4, 5],
            }
        )
        train, test, audit = time_geographic_split(frame, date(2024, 1, 1))
        self.assertLess(train["observation_date"].max(), test["observation_date"].min())
        self.assertFalse(
            set(train["geographic_group"]) & set(test["geographic_group"])
        )
        self.assertEqual(audit["excluded_rows"], 1)


if __name__ == "__main__":
    unittest.main()
