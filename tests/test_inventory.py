from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from glofguard.inventory import (
    attach_prediction_availability,
    load_existing_inventory,
    load_official_inventory,
    reconcile_inventories,
    run_reconciliation,
    write_master_map,
)
from glofguard.storage import Repository


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class InventoryReconciliationTests(unittest.TestCase):
    def test_official_master_count_is_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly 3,044 rows"):
            load_official_inventory(FIXTURES / "pmd_official_sample.csv")

    def test_every_existing_row_gets_one_auditable_status(self) -> None:
        official = load_official_inventory(
            FIXTURES / "pmd_official_sample.csv", expected_count=4
        )
        existing = load_existing_inventory(
            FIXTURES / "existing_inventory_sample.csv"
        )
        report, coverage, summary = reconcile_inventories(
            official,
            existing,
            max_distance_m=750,
            ambiguity_margin_m=100,
            duplicate_distance_m=50,
        )
        self.assertEqual(
            report["status"].value_counts().to_dict(),
            {
                "matched": 2,
                "duplicate": 2,
                "newly_detected_candidate": 1,
                "unmatched": 1,
            },
        )
        self.assertEqual(summary["classification_total"], 6)
        self.assertEqual(summary["matched_official_lakes"], 2)
        self.assertEqual(summary["unmatched_official_lakes"], 2)
        self.assertEqual(len(coverage), 4)

    def test_map_keeps_all_master_lakes_and_fails_closed_on_predictions(self) -> None:
        official = load_official_inventory(
            FIXTURES / "pmd_official_sample.csv", expected_count=4
        )
        existing = load_existing_inventory(
            FIXTURES / "existing_inventory_sample.csv"
        )
        _, coverage, _ = reconcile_inventories(official, existing)
        coverage = attach_prediction_availability(coverage, None)
        self.assertTrue((coverage["prediction_availability"] == "Data unavailable").all())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.html"
            write_master_map(coverage, output, expected_count=4)
            document = output.read_text(encoding="utf-8")
            self.assertIn("Official PMD master: ${lakes.length.toLocaleString()} lakes", document)
            self.assertIn('if (lakes.length !== 4)', document)
            self.assertEqual(document.count('"id":"PMD-O'), 4)

    def test_successful_reconciliation_activates_official_master_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = Repository(root / "inventory.sqlite3")
            repository.initialize()
            summary = run_reconciliation(
                FIXTURES / "pmd_official_sample.csv",
                FIXTURES / "existing_inventory_sample.csv",
                root / "output",
                repository=repository,
                expected_count=4,
            )
            self.assertEqual(summary["official_master_count"], 4)
            self.assertEqual(repository.official_lake_count(), 4)
            self.assertEqual(
                [lake.lake_id for lake in repository.list_lakes()],
                ["PMD-O1", "PMD-O2", "PMD-O3", "PMD-O4"],
            )


if __name__ == "__main__":
    unittest.main()
