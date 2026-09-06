"""Versioned pilot evidence and map-ready spatial database integration."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .schemas import SAFETY_NOTICE


EVIDENCE_VERSION = "corrected-100-real-v1.0.0"
INVENTORY_VERSION = "hkh-pk-2020-reconciled-8808-v1.0.0"
BASELINE_MODEL_VERSION = "baseline-susceptibility-index-v1.0.0"
DYNAMIC_MODEL_VERSION = "environmental-conditions-index-v1.1.1-source-freshness"
PILOT_RUN_ID = "corrected-100-real-pilot-v1.0.0"

REQUIRED_EVIDENCE_FILES = (
    "CORRECTED_REPRESENTATIVE_100_PILOT_REPORT.md",
    "corrected_representative_100_pilot_selection.csv",
    "corrected_representative_100_pilot_status.csv",
    "corrected_representative_100_pilot_summary.json",
    "corrected_representative_100_pilot_usage.json",
    "corrected_representative_100_pilot_error_log.csv",
    "source_inventory_reconciliation.csv",
    "source_inventory_reconciliation_summary.json",
)

QUEUE_STATUSES = {
    "LIVE_COMPLETE",
    "LIVE_PARTIAL",
    "LIVE_PENDING",
    "SATELLITE_STALE",
    "SATELLITE_UNAVAILABLE",
    "PROCESSING_FAILED",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8"
    )
    temporary.replace(path)


def _atomic_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def _optional_float(value: Any) -> float | None:
    value = _clean(value)
    return float(value) if value not in (None, "") else None


def _optional_int(value: Any) -> int | None:
    value = _clean(value)
    return int(float(value)) if value not in (None, "") else None


def _text(value: Any) -> str | None:
    value = _clean(value)
    if value in (None, ""):
        return None
    return str(value)


def _boolean(value: Any) -> int:
    return int(str(_clean(value)).strip().lower() in {"1", "true", "yes"})


def _safe_mapping(row: pd.Series) -> dict[str, Any]:
    return {str(key): _clean(value) for key, value in row.to_dict().items()}


def freeze_pilot_evidence(
    source_dir: Path,
    evidence_root: Path,
    *,
    version: str = EVIDENCE_VERSION,
) -> tuple[Path, dict[str, Any]]:
    """Copy immutable pilot artifacts and verify every byte with SHA-256."""
    source_dir = source_dir.resolve()
    destination = (evidence_root / version).resolve()
    manifest_path = destination / "evidence_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("evidence_version") != version:
            raise ValueError("Existing evidence manifest has a different version")
        for item in manifest.get("files", []):
            frozen_path = destination / str(item["name"])
            if not frozen_path.exists() or _sha256(frozen_path) != item["sha256"]:
                raise ValueError(f"Frozen evidence verification failed: {frozen_path}")
        return destination, manifest

    missing = [name for name in REQUIRED_EVIDENCE_FILES if not (source_dir / name).exists()]
    if missing:
        raise FileNotFoundError("Missing pilot evidence: " + ", ".join(missing))
    destination.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, Any]] = []
    for name in REQUIRED_EVIDENCE_FILES:
        source = source_dir / name
        frozen = destination / name
        shutil.copy2(source, frozen)
        source_hash = _sha256(source)
        frozen_hash = _sha256(frozen)
        if source_hash != frozen_hash:
            raise IOError(f"Evidence copy hash mismatch for {name}")
        files.append(
            {
                "name": name,
                "sha256": frozen_hash,
                "bytes": frozen.stat().st_size,
                "original_path": str(source),
            }
        )
    manifest = {
        "evidence_version": version,
        "evidence_kind": "corrected representative 100-lake REAL pilot",
        "frozen_at": _utc_now(),
        "source_directory": str(source_dir),
        "files": files,
        "immutability_rule": (
            "Never overwrite these files. Create a new evidence version for any rerun."
        ),
        "full_inventory_launched": False,
        "safety_notice": SAFETY_NOTICE,
    }
    _atomic_json(manifest_path, manifest)
    _atomic_text(
        destination / "README.md",
        f"""# Frozen corrected 100-lake pilot evidence

Evidence version: `{version}`

This directory is immutable evidence. Every source artifact is copied byte-for-byte
from the completed corrected pilot and recorded in `evidence_manifest.json` with a
SHA-256 checksum. Never overwrite this directory; create a new version for a rerun.

The original pilot outputs outside this folder remain untouched.

> {SAFETY_NOTICE}
""",
    )
    return destination, manifest


def _pilot_queue_status(row: pd.Series) -> str:
    processing = str(row.get("processing_status") or "").upper()
    satellite = str(row.get("satellite_area_freshness") or "").upper()
    if processing == "FAILED":
        return "PROCESSING_FAILED"
    if satellite == "UNAVAILABLE":
        return "SATELLITE_UNAVAILABLE"
    if satellite == "STALE":
        return "SATELLITE_STALE"
    if processing == "AVAILABLE":
        return "LIVE_COMPLETE"
    if processing == "PARTIAL":
        return "LIVE_PARTIAL"
    return "LIVE_PENDING"


def _source_rows(observation_id: str, row: pd.Series) -> Iterable[tuple[Any, ...]]:
    lake_id = str(row["lake_id"])
    yield (
        observation_id,
        lake_id,
        "SENTINEL_2",
        "AVAILABLE"
        if str(row["satellite_area_freshness"]).upper() != "UNAVAILABLE"
        else "UNAVAILABLE",
        str(row["satellite_area_freshness"]).upper(),
        _text(row.get("satellite_quality_status")),
        _text(row.get("latest_usable_satellite_date")),
        (
            _optional_float(row.get("satellite_area_age_days")) * 24
            if _optional_float(row.get("satellite_area_age_days")) is not None
            else None
        ),
        json.dumps(
            {
                "diagnostic_reason": _text(row.get("sentinel_diagnostic_reason")),
                "raw_scene_count": _optional_int(row.get("sentinel_raw_scene_count")),
                "metadata_clear_scene_count": _optional_int(
                    row.get("sentinel_metadata_clear_scene_count")
                ),
                "cloud_score_match_count": _optional_int(
                    row.get("sentinel_cloud_score_match_count")
                ),
                "candidates_measured": _optional_int(
                    row.get("sentinel_candidates_measured")
                ),
            },
            separators=(",", ":"),
            allow_nan=False,
        ),
    )
    yield (
        observation_id,
        lake_id,
        "JAXA_GSMAP",
        "AVAILABLE"
        if str(row.get("observed_weather_freshness")).upper() != "UNAVAILABLE"
        else "UNAVAILABLE",
        str(row.get("observed_weather_freshness") or "UNAVAILABLE").upper(),
        _text(row.get("observed_weather_data_quality")),
        _text(row.get("observed_weather_latest_at")),
        _optional_float(row.get("observed_weather_age_hours")),
        json.dumps(
            {
                "coverage_24h": _optional_float(
                    row.get("observed_weather_coverage_24h")
                ),
                "coverage_7d": _optional_float(
                    row.get("observed_weather_coverage_7d")
                ),
                "coverage_30d": _optional_float(
                    row.get("observed_weather_coverage_30d")
                ),
            },
            separators=(",", ":"),
            allow_nan=False,
        ),
    )
    yield (
        observation_id,
        lake_id,
        "NOAA_GFS",
        "AVAILABLE"
        if str(row.get("forecast_freshness")).upper() != "UNAVAILABLE"
        else "UNAVAILABLE",
        str(row.get("forecast_freshness") or "UNAVAILABLE").upper(),
        _text(row.get("forecast_data_quality")),
        _text(row.get("forecast_created_at")),
        _optional_float(row.get("forecast_age_hours")),
        json.dumps(
            {
                "precipitation_steps": _optional_int(
                    row.get("forecast_precipitation_steps")
                ),
                "temperature_steps_7d": _optional_int(
                    row.get("forecast_temperature_steps_7d")
                ),
                "forecast_horizon_hours": 168,
            },
            separators=(",", ":"),
            allow_nan=False,
        ),
    )
    baseline_available = (
        str(row.get("historical_baseline_availability")).upper() == "AVAILABLE"
    )
    yield (
        observation_id,
        lake_id,
        "NASA_POWER",
        "AVAILABLE" if baseline_available else "UNAVAILABLE",
        "NOT_APPLICABLE",
        "BASELINE_AVAILABLE" if baseline_available else "BASELINE_UNAVAILABLE",
        _text(row.get("historical_baseline_latest_date")),
        None,
        json.dumps(
            {
                "baseline_years": _optional_int(row.get("historical_baseline_years")),
                "publication_delay_controls_live_freshness": False,
            },
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def import_map_database(
    *,
    database_path: Path,
    sqlite_schema_path: Path,
    reconciliation_path: Path,
    baseline_path: Path,
    pilot_status_path: Path,
    pilot_summary_path: Path,
    evidence_directory: Path,
    report_directory: Path,
) -> dict[str, Any]:
    """Import all unique lakes and the immutable corrected pilot into SQLite."""
    reconciliation = pd.read_csv(reconciliation_path)
    baseline = pd.read_csv(baseline_path)
    pilot = pd.read_csv(pilot_status_path)
    pilot_summary = json.loads(pilot_summary_path.read_text(encoding="utf-8"))
    manifest_path = evidence_directory / "evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if len(reconciliation) != 8_808:
        raise ValueError(f"Expected 8,808 reconciliation rows, found {len(reconciliation)}")
    included = reconciliation[reconciliation["reconciliation_status"] == "INCLUDED"].copy()
    duplicates = reconciliation[
        reconciliation["reconciliation_status"] == "EXCLUDED_EXACT_DUPLICATE"
    ].copy()
    if len(included) != 8_806 or included["lake_id"].nunique() != 8_806:
        raise ValueError("Reconciliation does not contain 8,806 unique included lakes")
    if len(duplicates) != 2:
        raise ValueError("Reconciliation must retain exactly two duplicate mappings")
    if len(baseline) != 8_806 or baseline["lake_id"].nunique() != 8_806:
        raise ValueError("Baseline table must contain one row for every unique lake")
    if set(included["lake_id"].astype(str)) != set(baseline["lake_id"].astype(str)):
        raise ValueError("Baseline and reconciled lake identifiers differ")
    if len(pilot) != 100 or pilot["lake_id"].nunique() != 100:
        raise ValueError("Corrected pilot evidence must contain 100 unique lakes")
    if not pilot["source_mode"].astype(str).str.upper().eq("REAL").all():
        raise ValueError("Corrected pilot contains a non-REAL observation")
    if int(pilot_summary.get("mock_count", -1)) != 0:
        raise ValueError("Corrected pilot summary reports mock observations")
    if not set(pilot["lake_id"].astype(str)).issubset(set(included["lake_id"].astype(str))):
        raise ValueError("Corrected pilot contains a lake outside the unique inventory")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    now = _utc_now()
    manifest_hash = _sha256(manifest_path)
    try:
        connection.executescript(sqlite_schema_path.read_text(encoding="utf-8"))
        with connection:
            connection.execute(
                """INSERT INTO evidence_versions VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_version) DO NOTHING""",
                (
                    EVIDENCE_VERSION,
                    str(manifest["evidence_kind"]),
                    str(manifest["frozen_at"]),
                    manifest_hash,
                    str(evidence_directory.resolve()),
                    "Immutable corrected pilot evidence; previous pilot outputs preserved",
                ),
            )
            for _, row in included.iterrows():
                lake_id = str(row["lake_id"])
                map_id = int(row["processed_sample_id"])
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
                connection.execute(
                    """INSERT INTO lakes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lake_id) DO UPDATE SET
                        source_polygon_id=excluded.source_polygon_id,
                        latitude=excluded.latitude,
                        longitude=excluded.longitude,
                        elevation_m=excluded.elevation_m,
                        reference_area_km2=excluded.reference_area_km2,
                        distance_to_nearest_settlement_km=excluded.distance_to_nearest_settlement_km,
                        location_wkt=excluded.location_wkt,
                        source_geometry_valid=excluded.source_geometry_valid,
                        source_validation_warning=excluded.source_validation_warning,
                        inventory_version=excluded.inventory_version,
                        updated_at=excluded.updated_at""",
                    (
                        map_id,
                        lake_id,
                        int(row["source_polygon_id"]),
                        latitude,
                        longitude,
                        _optional_float(row.get("elevation_m")),
                        _optional_float(row.get("reference_area_km2")),
                        _optional_float(row.get("distance_to_nearest_settlement_km")),
                        f"POINT ({longitude:.8f} {latitude:.8f})",
                        _boolean(row.get("source_geometry_valid")),
                        _text(row.get("source_validation_warning")),
                        INVENTORY_VERSION,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO lakes_rtree VALUES (?, ?, ?, ?, ?)",
                    (map_id, longitude, longitude, latitude, latitude),
                )
                connection.execute(
                    """INSERT INTO processing_queue
                    (lake_id, processing_status, approval_required, priority,
                     attempt_count, updated_at)
                    VALUES (?, 'LIVE_PENDING', 1, 0, 0, ?)
                    ON CONFLICT(lake_id) DO NOTHING""",
                    (lake_id, now),
                )

            for _, row in baseline.iterrows():
                connection.execute(
                    """INSERT INTO baseline_susceptibility VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(lake_id) DO UPDATE SET
                        susceptibility_score=excluded.susceptibility_score,
                        susceptibility_level=excluded.susceptibility_level,
                        missing_static_fields=excluded.missing_static_fields,
                        interpretation=excluded.interpretation,
                        model_version=excluded.model_version,
                        calculated_at=excluded.calculated_at""",
                    (
                        str(row["lake_id"]),
                        _optional_float(row.get("baseline_susceptibility_score")),
                        _text(row.get("baseline_susceptibility_level")),
                        _text(row.get("missing_static_fields")),
                        str(row["interpretation"]),
                        BASELINE_MODEL_VERSION,
                        now,
                    ),
                )

            for _, row in reconciliation.iterrows():
                connection.execute(
                    """INSERT INTO inventory_reconciliation VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_row_number) DO UPDATE SET
                        reconciliation_status=excluded.reconciliation_status,
                        reconciliation_reason=excluded.reconciliation_reason,
                        eligible_for_processing=excluded.eligible_for_processing,
                        source_geometry_valid=excluded.source_geometry_valid,
                        source_validation_warning=excluded.source_validation_warning,
                        inventory_version=excluded.inventory_version""",
                    (
                        int(row["source_row_number"]),
                        int(row["source_polygon_id"]),
                        str(row["lake_id"]),
                        str(row["canonical_lake_id"]),
                        _optional_int(row.get("processed_sample_id")),
                        str(row["reconciliation_status"]),
                        str(row["reconciliation_reason"]),
                        _boolean(row.get("eligible_for_processing")),
                        str(row["geometry_sha256"]),
                        _boolean(row.get("source_geometry_valid")),
                        _text(row.get("source_validation_warning")),
                        _optional_float(row.get("source_centroid_latitude")),
                        _optional_float(row.get("source_centroid_longitude")),
                        _optional_float(row.get("source_area_km2")),
                        INVENTORY_VERSION,
                    ),
                )

            for _, row in duplicates.iterrows():
                connection.execute(
                    """INSERT INTO inventory_duplicate_mappings VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(duplicate_source_polygon_id) DO NOTHING""",
                    (
                        int(row["source_polygon_id"]),
                        str(row["canonical_lake_id"]),
                        int(row["processed_sample_id"]),
                        str(row["geometry_sha256"]),
                        str(row["reconciliation_reason"]),
                        INVENTORY_VERSION,
                    ),
                )

            retrieval_times = pd.to_datetime(
                pilot["data_retrieval_timestamp"], errors="coerce", utc=True
            ).dropna()
            started_at = retrieval_times.min().isoformat() if not retrieval_times.empty else None
            completed_at = retrieval_times.max().isoformat() if not retrieval_times.empty else None
            api_usage = {
                key: pilot_summary.get(key)
                for key in (
                    "sentinel_scene_lookup_calls",
                    "sentinel_scene_measurement_calls",
                    "gsmap_earth_engine_summary_calls",
                    "gfs_earth_engine_summary_calls",
                    "nasa_power_http_requests",
                )
            }
            connection.execute(
                """INSERT INTO ingestion_runs VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING""",
                (
                    PILOT_RUN_ID,
                    "REPRESENTATIVE_PILOT",
                    EVIDENCE_VERSION,
                    "REAL",
                    "COMPLETE",
                    started_at,
                    completed_at,
                    _text(pilot_summary.get("exact_command_used")),
                    100,
                    100,
                    int(pilot_summary.get("provider_failures", 0)),
                    int(pilot_summary.get("mock_count", 0)),
                    float(pilot_summary["runtime_seconds"]),
                    json.dumps(api_usage, separators=(",", ":"), allow_nan=False),
                    DYNAMIC_MODEL_VERSION,
                    manifest_hash,
                    now,
                ),
            )

            for _, row in pilot.iterrows():
                lake_id = str(row["lake_id"])
                observation_id = f"{PILOT_RUN_ID}:{lake_id}"
                retrieved_at = _text(row.get("data_retrieval_timestamp"))
                observation_date = (
                    retrieved_at[:10]
                    if retrieved_at
                    else _text(row.get("weather_observation_date"))
                )
                if not observation_date:
                    raise ValueError(f"Pilot lake {lake_id} has no observation date")
                connection.execute(
                    """INSERT INTO environmental_observations VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(observation_id) DO NOTHING""",
                    (
                        observation_id,
                        PILOT_RUN_ID,
                        lake_id,
                        observation_date[:10],
                        "REAL",
                        _optional_float(row.get("environmental_conditions_score")),
                        _text(row.get("environmental_conditions_level")),
                        str(row.get("score_interpretation") or "")
                        or "Environmental conditions index; not a validated GLOF probability",
                        _optional_float(row.get("current_area_km2")),
                        _text(row.get("latest_usable_satellite_date")),
                        _optional_float(row.get("satellite_cloud_percentage")),
                        _optional_float(row.get("current_temperature_c")),
                        _optional_float(row.get("rainfall_last_24h_mm")),
                        _optional_float(row.get("rainfall_last_7d_mm")),
                        _optional_float(row.get("rainfall_last_30d_mm")),
                        _text(row.get("observed_weather_latest_at")),
                        _optional_float(row.get("forecast_rainfall_next_72h_mm")),
                        _optional_float(row.get("forecast_temperature_next_24h_c")),
                        _optional_float(row.get("forecast_temperature_next_7d_c")),
                        _text(row.get("forecast_created_at")),
                        int(
                            str(row.get("historical_baseline_availability")).upper()
                            == "AVAILABLE"
                        ),
                        _text(row.get("historical_baseline_latest_date")),
                        _text(row.get("confidence")),
                        _text(row.get("failure_or_stale_warning")),
                        DYNAMIC_MODEL_VERSION,
                        EVIDENCE_VERSION,
                        retrieved_at,
                        json.dumps(
                            _safe_mapping(row),
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    ),
                )
                for source_row in _source_rows(observation_id, row):
                    connection.execute(
                        """INSERT INTO source_freshness VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(observation_id, source_name) DO NOTHING""",
                        source_row,
                    )
                mapped_status = _pilot_queue_status(row)
                if mapped_status not in QUEUE_STATUSES:
                    raise AssertionError(f"Unsupported map status: {mapped_status}")
                connection.execute(
                    """UPDATE processing_queue SET
                        processing_status=?,
                        attempt_count=?,
                        last_attempt_at=?,
                        last_successful_run_id=?,
                        last_error=?,
                        updated_at=?
                    WHERE lake_id=? AND (
                        last_successful_run_id IS NULL OR last_successful_run_id=?
                    )""",
                    (
                        mapped_status,
                        max(
                            _optional_int(row.get("sentinel_attempts")) or 0,
                            _optional_int(row.get("gsmap_attempts")) or 0,
                            _optional_int(row.get("gfs_attempts")) or 0,
                            _optional_int(row.get("nasa_power_attempts")) or 0,
                        ),
                        retrieved_at,
                        PILOT_RUN_ID,
                        _text(row.get("last_error_message")),
                        now,
                        lake_id,
                        PILOT_RUN_ID,
                    ),
                )
    finally:
        connection.close()

    report = verify_map_database(database_path)
    report.update(
        {
            "database_backend": "SQLite with RTree spatial index",
            "supabase_postgis_configured": False,
            "database_path": str(database_path.resolve()),
            "evidence_directory": str(evidence_directory.resolve()),
            "evidence_manifest_sha256": manifest_hash,
            "full_inventory_launched": False,
            "safety_notice": SAFETY_NOTICE,
        }
    )
    report_directory.mkdir(parents=True, exist_ok=True)
    _atomic_json(report_directory / "database_import_summary.json", report)
    queue_lines = "\n".join(
        f"- {status}: {count:,}"
        for status, count in report["processing_status_counts"].items()
    )
    _atomic_text(
        report_directory / "DATABASE_IMPORT_REPORT.md",
        f"""# Spatial Database Import Report

Import state: **VERIFIED**

- Database backend: SQLite with RTree spatial index
- Supabase/PostGIS configured: No; `database/postgis_schema.sql` is ready for later use
- Unique lakes imported: {report['lakes']:,}
- Original reconciliation rows preserved: {report['reconciliation_rows']:,}
- Duplicate mappings preserved: {report['duplicate_mappings']:,}
- Baseline susceptibility rows: {report['baseline_rows']:,}
- Corrected REAL pilot observations: {report['environmental_observations']:,}
- Source-freshness rows: {report['source_freshness_rows']:,}
- Processing-queue rows: {report['processing_queue_rows']:,}
- Spatial-index rows: {report['spatial_index_rows']:,}
- Foreign-key violations: {report['foreign_key_violations']:,}
- Mock pilot observations: {report['mock_observations']:,}
- Full remaining-inventory processing launched: No

## Map statuses

{queue_lines}

`LIVE_PENDING`, `SATELLITE_UNAVAILABLE`, and other incomplete states are not
interpreted as safe. The environmental conditions score is stored and displayed
only as a research index, never as a GLOF probability.

## Evidence

- Evidence version: `{EVIDENCE_VERSION}`
- Evidence manifest SHA-256: `{manifest_hash}`
- Inventory version: `{INVENTORY_VERSION}`
- Dynamic data/model version: `{DYNAMIC_MODEL_VERSION}`

> {SAFETY_NOTICE}
""",
    )
    return report


def verify_map_database(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        def count(table: str) -> int:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        status_counts = {
            str(row["processing_status"]): int(row["count"])
            for row in connection.execute(
                """SELECT processing_status, COUNT(*) AS count
                FROM processing_queue GROUP BY processing_status ORDER BY processing_status"""
            )
        }
        mock_observations = int(
            connection.execute(
                "SELECT COUNT(*) FROM environmental_observations WHERE source_mode <> 'REAL'"
            ).fetchone()[0]
        )
        unprocessed_with_score = int(
            connection.execute(
                """SELECT COUNT(*) FROM map_lake_details
                WHERE processing_status='LIVE_PENDING'
                  AND environmental_conditions_score IS NOT NULL"""
            ).fetchone()[0]
        )
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        report = {
            "lakes": count("lakes"),
            "reconciliation_rows": count("inventory_reconciliation"),
            "duplicate_mappings": count("inventory_duplicate_mappings"),
            "baseline_rows": count("baseline_susceptibility"),
            "environmental_observations": count("environmental_observations"),
            "source_freshness_rows": count("source_freshness"),
            "ingestion_runs": count("ingestion_runs"),
            "processing_queue_rows": count("processing_queue"),
            "spatial_index_rows": count("lakes_rtree"),
            "processing_status_counts": status_counts,
            "mock_observations": mock_observations,
            "pending_lakes_with_environmental_score": unprocessed_with_score,
            "foreign_key_violations": len(foreign_keys),
        }
        expected = {
            "lakes": 8_806,
            "reconciliation_rows": 8_808,
            "duplicate_mappings": 2,
            "baseline_rows": 8_806,
            "environmental_observations": 100,
            "source_freshness_rows": 400,
            "processing_queue_rows": 8_806,
            "spatial_index_rows": 8_806,
            "mock_observations": 0,
            "pending_lakes_with_environmental_score": 0,
            "foreign_key_violations": 0,
        }
        mismatches = {
            key: {"expected": value, "actual": report[key]}
            for key, value in expected.items()
            if report[key] != value
        }
        if sum(status_counts.values()) != 8_806:
            mismatches["processing_status_total"] = {
                "expected": 8_806,
                "actual": sum(status_counts.values()),
            }
        if mismatches:
            raise ValueError("Spatial database verification failed: " + json.dumps(mismatches))
        return report
    finally:
        connection.close()


def export_map_geojson(database_path: Path, output_path: Path) -> dict[str, Any]:
    """Export every unique lake as map-ready GeoJSON without inventing live values."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM map_lake_details ORDER BY map_id"
        ).fetchall()
        features = []
        for row in rows:
            values = dict(row)
            longitude = float(values.pop("longitude"))
            latitude = float(values.pop("latitude"))
            values.pop("map_id", None)
            if values.get("processing_status") == "LIVE_PENDING":
                values["environmental_conditions_score"] = None
                values["environmental_conditions_level"] = None
                values["confidence"] = "Pending"
                values["data_completeness_warning"] = (
                    "Live environmental processing is pending; no safety conclusion is available"
                )
            values = {key: value for key, value in values.items() if value is not None}
            features.append(
                {
                    "type": "Feature",
                    "id": values["lake_id"],
                    "geometry": {
                        "type": "Point",
                        "coordinates": [longitude, latitude],
                    },
                    "properties": values,
                }
            )
    finally:
        connection.close()
    payload = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "total_lakes": len(features),
            "inventory_version": INVENTORY_VERSION,
            "pilot_evidence_version": EVIDENCE_VERSION,
            "dynamic_model_version": DYNAMIC_MODEL_VERSION,
            "generated_at": _utc_now(),
            "environmental_score_is_probability": False,
            "safety_notice": SAFETY_NOTICE,
        },
    }
    _atomic_json(output_path, payload)
    return payload["metadata"]
