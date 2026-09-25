from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from glofguard.training_labels import validate_verified_training_labels


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


if __name__ == "__main__":
    unittest.main()
