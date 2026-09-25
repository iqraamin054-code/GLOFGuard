from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from glofguard.training_labels import (
    build_verified_training_labels,
    merge_verified_training_labels,
    validate_verified_training_labels,
)


class VerifiedTrainingLabelsTests(unittest.TestCase):
    def test_template_file_has_required_verified_columns(self) -> None:
        template = Path(__file__).resolve().parents[1] / "data" / "training" / "verified_training_labels_template.csv"
        self.assertTrue(template.exists(), "Verified training labels template is missing")

        required = {
            "lake_id",
            "observation_date",
            "glof_within_next_7_days",
            "glof_within_next_30_days",
            "label_provenance_status",
            "source_event_id",
            "source_event_name",
            "verification_date",
        }
        with template.open("r", encoding="utf-8", newline="") as handle:
            header = next(handle).strip().split(",")
        self.assertTrue(required.issubset(set(header)))

    def test_validator_accepts_verified_records_and_rejects_unverified_ones(self) -> None:
        content = """lake_id,observation_date,glof_within_next_7_days,glof_within_next_30_days,label_provenance_status,source_event_id,source_event_name,verification_date
PKGL-00001,2026-09-01,1,0,verified,EVT-001,Lake Alpha,2026-09-02
PKGL-00002,2026-09-01,0,1,verified,EVT-002,Lake Beta,2026-09-02
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "labels.csv"
            path.write_text(content, encoding="utf-8")
            result = validate_verified_training_labels(path)
            self.assertTrue(result["valid"])
            self.assertEqual(result["rows"], 2)

            bad = """lake_id,observation_date,glof_within_next_7_days,glof_within_next_30_days,label_provenance_status,source_event_id,source_event_name,verification_date
PKGL-00003,2026-09-01,1,0,experimental_proxy_unverified,EVT-003,Lake Gamma,2026-09-02
"""
            bad_path = Path(tmpdir) / "bad_labels.csv"
            bad_path.write_text(bad, encoding="utf-8")
            result = validate_verified_training_labels(bad_path)
            self.assertFalse(result["valid"])
            self.assertIn("verified", " ".join(result["errors"]))

    def test_dataframe_validation_and_merge_helper(self) -> None:
        verified = pd.DataFrame(
            [
                {
                    "lake_id": "PKGL-00001",
                    "observation_date": "2026-09-01",
                    "glof_within_next_7_days": 1,
                    "glof_within_next_30_days": 0,
                    "label_provenance_status": "verified",
                    "source_event_id": "EVT-001",
                    "source_event_name": "Lake Alpha",
                    "verification_date": "2026-09-02",
                },
                {
                    "lake_id": "PKGL-00002",
                    "observation_date": "2026-09-01",
                    "glof_within_next_7_days": 0,
                    "glof_within_next_30_days": 1,
                    "label_provenance_status": "verified",
                    "source_event_id": "EVT-002",
                    "source_event_name": "Lake Beta",
                    "verification_date": "2026-09-02",
                },
            ]
        )

        self.assertTrue(validate_verified_training_labels(verified)["valid"])

        model_data = pd.DataFrame(
            [
                {"lake_id": "PKGL-00001", "observation_date": "2026-09-01", "feature_a": 10.0},
                {"lake_id": "PKGL-00002", "observation_date": "2026-09-01", "feature_a": 20.0},
                {"lake_id": "PKGL-00003", "observation_date": "2026-09-01", "feature_a": 30.0},
            ]
        )

        merged = merge_verified_training_labels(model_data, verified)
        self.assertEqual(merged["glof_within_next_7_days"].dropna().tolist(), [1, 0])
        self.assertTrue(pd.isna(merged.loc[2, "glof_within_next_7_days"]))

    def test_build_verified_training_labels_from_events(self) -> None:
        observations = pd.DataFrame(
            [
                {"lake_id": "PKGL-00001", "observation_date": "2026-09-01"},
                {"lake_id": "PKGL-00001", "observation_date": "2026-09-20"},
                {"lake_id": "PKGL-00002", "observation_date": "2026-09-05"},
            ]
        )
        events = pd.DataFrame(
            [
                {"lake_id": "PKGL-00001", "event_date": "2026-09-04", "event_id": "EVT-001", "event_name": "Lake Alpha event"},
                {"lake_id": "PKGL-00001", "event_date": "2026-09-25", "event_id": "EVT-002", "event_name": "Lake Alpha later event"},
                {"lake_id": "PKGL-00002", "event_date": "2026-09-12", "event_id": "EVT-003", "event_name": "Lake Beta event"},
            ]
        )

        labels = build_verified_training_labels(observations, events)
        self.assertTrue(validate_verified_training_labels(labels)["valid"])
        self.assertEqual(labels.loc[0, "glof_within_next_7_days"], 1)
        self.assertEqual(labels.loc[0, "source_event_id"], "EVT-001")
        self.assertEqual(labels.loc[1, "glof_within_next_7_days"], 1)
        self.assertEqual(labels.loc[1, "glof_within_next_30_days"], 1)
        self.assertEqual(labels.loc[2, "glof_within_next_7_days"], 1)
        self.assertEqual(labels.loc[2, "glof_within_next_30_days"], 1)


if __name__ == "__main__":
    unittest.main()
