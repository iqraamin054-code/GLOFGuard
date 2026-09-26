from __future__ import annotations

import json
from pathlib import Path

from glofguard.config import PROJECT_ROOT
from glofguard.map_database import (
    EVIDENCE_VERSION,
    export_map_geojson,
    freeze_pilot_evidence,
    import_map_database,
)


def main() -> None:
    pilot_source = PROJECT_ROOT / "output" / "full_inventory_corrected_v2_pilot"
    evidence_root = PROJECT_ROOT / "evidence" / "pilots"
    evidence_directory, _ = freeze_pilot_evidence(
        pilot_source, evidence_root, version=EVIDENCE_VERSION
    )
    report_directory = PROJECT_ROOT / "output" / "map_database"
    database_path = PROJECT_ROOT / "data" / "map" / "glof_map.sqlite3"
    report = import_map_database(
        database_path=database_path,
        sqlite_schema_path=PROJECT_ROOT / "database" / "sqlite_schema.sql",
        reconciliation_path=(
            pilot_source / "source_inventory_reconciliation.csv"
        ),
        baseline_path=PROJECT_ROOT / "data" / "baseline_susceptibility.csv",
        pilot_status_path=(
            pilot_source / "corrected_representative_100_pilot_status.csv"
        ),
        pilot_summary_path=(
            pilot_source / "corrected_representative_100_pilot_summary.json"
        ),
        evidence_directory=evidence_directory,
        report_directory=report_directory,
    )
    metadata = export_map_geojson(
        database_path, PROJECT_ROOT / "data" / "map" / "lakes_map.geojson"
    )
    print(json.dumps({"database": report, "geojson": metadata}, indent=2))


if __name__ == "__main__":
    main()
