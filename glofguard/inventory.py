"""Strict PMD-master inventory loading, reconciliation, reporting, and mapping."""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from .schemas import SAFETY_NOTICE
from .storage import Repository


EARTH_RADIUS_M = 6_371_008.8
OFFICIAL_ID_CANDIDATES = (
    "pmd_lake_id",
    "official_lake_id",
    "gl_id",
    "glid",
    "lake_code",
    "lake_id",
    "lake_no",
    "objectid_1",
    "objectid",
    "id",
)
SOURCE_ID_CANDIDATES = ("source_lake_id", "sample_id", "objectid_1", "lake_id", "id")
SOURCE_OFFICIAL_ID_CANDIDATES = (
    "pmd_lake_id",
    "official_lake_id",
    "gl_id",
    "glid",
    "lake_code",
)
LATITUDE_CANDIDATES = ("latitude", "lat", "lat_lake", "centroid_lat", "y")
LONGITUDE_CANDIDATES = ("longitude", "lon", "lng", "lon_lake", "centroid_lon", "x")
AREA_KM2_CANDIDATES = (
    "area_km2",
    "official_area_km2",
    "lake_area_km2",
    "area",
)
AREA_M2_CANDIDATES = ("shape_area", "area_m2", "lake_area_m2")
NAME_CANDIDATES = ("lake_name", "name", "gl_name")
BASIN_CANDIDATES = ("basin", "river_basin", "catchment", "basin_name")
TYPE_CANDIDATES = ("lake_type", "type", "gl_type", "classification")
ELEVATION_CANDIDATES = ("elevation_m", "elevation", "altitude_m", "altitude")
SETTLEMENT_DISTANCE_CANDIDATES = (
    "distance_to_nearest_settlement_km",
    "settlement_distance_km",
)
DYNAMIC_REQUIRED_FIELDS = (
    "area_current_km2",
    "temperature_current",
    "rainfall_last_7d",
    "forecast_rainfall_next_72h",
    "risk_score",
    "prediction_timestamp",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _column_map(frame: pd.DataFrame) -> dict[str, str]:
    return {str(column).strip().lower(): str(column) for column in frame.columns}


def _pick_column(
    frame: pd.DataFrame,
    candidates: Iterable[str],
    explicit: str | None = None,
    *,
    required: bool = False,
    label: str = "column",
) -> str | None:
    columns = _column_map(frame)
    if explicit:
        actual = columns.get(explicit.strip().lower())
        if actual is None:
            raise ValueError(f"{label} '{explicit}' was not found")
        return actual
    for candidate in candidates:
        if candidate in columns:
            return columns[candidate]
    if required:
        raise ValueError(
            f"Could not identify the {label}. Available columns: "
            + ", ".join(map(str, frame.columns))
        )
    return None


def _clean_identifier(value: object) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and math.isfinite(float(value)):
        number = float(value)
        return str(int(number)) if number.is_integer() else str(number)
    return str(value).strip()


def _normalise_identifier(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", _clean_identifier(value).upper())


def _optional_text(frame: pd.DataFrame, candidates: Iterable[str]) -> pd.Series:
    column = _pick_column(frame, candidates)
    if column is None:
        return pd.Series([None] * len(frame), index=frame.index, dtype="object")
    return frame[column].map(
        lambda value: None if pd.isna(value) or not str(value).strip() else str(value).strip()
    )


def _optional_numeric(frame: pd.DataFrame, candidates: Iterable[str]) -> pd.Series:
    column = _pick_column(frame, candidates)
    if column is None:
        return pd.Series([np.nan] * len(frame), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _read_inventory(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Inventory file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt", ".tsv"}:
        separator = "\t" if suffix == ".tsv" else ","
        return pd.read_csv(path, sep=separator)
    if suffix in {".json", ".geojson", ".gpkg", ".shp", ".zip"}:
        try:
            import geopandas as gpd
        except ImportError as exc:
            raise RuntimeError(
                "GeoPandas is required to read geospatial PMD inventory files"
            ) from exc
        source = f"zip://{path.resolve()}" if suffix == ".zip" else path
        frame = gpd.read_file(source)
        if frame.crs is None:
            raise ValueError(f"Geospatial inventory has no CRS: {path}")
        projected = frame if frame.crs.is_projected else frame.to_crs(epsg=6933)
        centroids = projected.geometry.centroid.to_crs(epsg=4326)
        result = frame.drop(columns="geometry").copy()
        result["__geometry_longitude"] = centroids.x.to_numpy()
        result["__geometry_latitude"] = centroids.y.to_numpy()
        if "Shape_Area" not in result.columns:
            result["__geometry_area_km2"] = projected.geometry.area.to_numpy() / 1_000_000
        return result
    raise ValueError(
        "Unsupported inventory format. Use CSV, TSV, GeoJSON, GPKG, SHP, or ZIP."
    )


def _coordinates(
    frame: pd.DataFrame,
    latitude_column: str | None,
    longitude_column: str | None,
) -> tuple[pd.Series, pd.Series]:
    lat = _pick_column(
        frame,
        ("__geometry_latitude", *LATITUDE_CANDIDATES),
        latitude_column,
        required=True,
        label="latitude column",
    )
    lon = _pick_column(
        frame,
        ("__geometry_longitude", *LONGITUDE_CANDIDATES),
        longitude_column,
        required=True,
        label="longitude column",
    )
    return pd.to_numeric(frame[lat], errors="coerce"), pd.to_numeric(
        frame[lon], errors="coerce"
    )


def _area_km2(frame: pd.DataFrame) -> pd.Series:
    geometry_area = _pick_column(frame, ("__geometry_area_km2",))
    km2 = _pick_column(frame, AREA_KM2_CANDIDATES)
    m2 = _pick_column(frame, AREA_M2_CANDIDATES)
    if geometry_area:
        return pd.to_numeric(frame[geometry_area], errors="coerce")
    if km2:
        return pd.to_numeric(frame[km2], errors="coerce")
    if m2:
        return pd.to_numeric(frame[m2], errors="coerce") / 1_000_000
    return pd.Series([np.nan] * len(frame), index=frame.index, dtype="float64")


def load_official_inventory(
    path: Path,
    *,
    expected_count: int = 3_044,
    id_column: str | None = None,
    latitude_column: str | None = None,
    longitude_column: str | None = None,
) -> pd.DataFrame:
    """Load a PMD master file and reject incomplete or fabricated substitutes."""
    raw = _read_inventory(path)
    if len(raw) != expected_count:
        raise ValueError(
            f"Official PMD master must contain exactly {expected_count:,} rows; "
            f"{path} contains {len(raw):,}. The 8,806-row derived inventory must not "
            "be trimmed or sampled to satisfy this check."
        )
    identifier = _pick_column(
        raw,
        OFFICIAL_ID_CANDIDATES,
        id_column,
        required=True,
        label="official lake identifier column",
    )
    latitude, longitude = _coordinates(raw, latitude_column, longitude_column)
    official_ids = raw[identifier].map(_clean_identifier)
    normalised_ids = official_ids.map(_normalise_identifier)
    if (normalised_ids == "").any():
        rows = (normalised_ids == "").to_numpy().nonzero()[0][:10] + 2
        raise ValueError(
            "Official lake identifiers are blank at CSV/source rows: "
            + ", ".join(map(str, rows))
        )
    duplicate_ids = official_ids[normalised_ids.duplicated(keep=False)].unique().tolist()
    if duplicate_ids:
        raise ValueError(
            "Official lake identifiers are not unique after normalization: "
            + ", ".join(map(str, duplicate_ids[:10]))
        )
    invalid_coordinates = (
        latitude.isna()
        | longitude.isna()
        | ~latitude.between(23.0, 38.5)
        | ~longitude.between(60.0, 80.5)
    )
    if invalid_coordinates.any():
        rows = invalid_coordinates.to_numpy().nonzero()[0][:10] + 2
        raise ValueError(
            "Official inventory has missing or implausible Pakistan coordinates at "
            "source rows: "
            + ", ".join(map(str, rows))
        )
    result = pd.DataFrame(
        {
            "official_lake_id": official_ids,
            "official_source_id": official_ids,
            "official_id_normalized": normalised_ids,
            "lake_name": _optional_text(raw, NAME_CANDIDATES),
            "basin": _optional_text(raw, BASIN_CANDIDATES),
            "lake_type": _optional_text(raw, TYPE_CANDIDATES),
            "latitude": latitude.astype(float),
            "longitude": longitude.astype(float),
            "official_area_km2": _area_km2(raw),
            "official_source_row": np.arange(2, len(raw) + 2),
        }
    )
    return result


def load_existing_inventory(
    path: Path,
    *,
    id_column: str | None = None,
    official_id_column: str | None = None,
    latitude_column: str | None = None,
    longitude_column: str | None = None,
) -> pd.DataFrame:
    raw = _read_inventory(path)
    identifier = _pick_column(
        raw,
        SOURCE_ID_CANDIDATES,
        id_column,
        label="source lake identifier column",
    )
    official_identifier = _pick_column(
        raw,
        SOURCE_OFFICIAL_ID_CANDIDATES,
        official_id_column,
        label="source PMD identifier column",
    )
    latitude, longitude = _coordinates(raw, latitude_column, longitude_column)
    if identifier:
        source_ids = raw[identifier].map(_clean_identifier)
    else:
        source_ids = pd.Series(
            [f"ROW-{number:05d}" for number in range(1, len(raw) + 1)],
            index=raw.index,
        )
    blank_ids = source_ids == ""
    if blank_ids.any():
        source_ids.loc[blank_ids] = [
            f"ROW-{index + 1:05d}" for index in source_ids.index[blank_ids]
        ]
    if identifier and identifier.strip().lower() == "sample_id":
        source_system_ids = source_ids.map(
            lambda value: f"PKGL-{int(float(value)):05d}"
            if re.fullmatch(r"\d+(?:\.0+)?", value)
            else value
        )
    else:
        source_system_ids = source_ids.copy()
    result = pd.DataFrame(
        {
            "source_row_number": np.arange(2, len(raw) + 2),
            "source_lake_id": source_ids,
            "source_system_lake_id": source_system_ids,
            "source_official_id": (
                raw[official_identifier].map(_clean_identifier)
                if official_identifier
                else None
            ),
            "source_official_id_normalized": (
                raw[official_identifier].map(_normalise_identifier)
                if official_identifier
                else ""
            ),
            "source_latitude": latitude,
            "source_longitude": longitude,
            "source_area_km2": _area_km2(raw),
            "elevation_m": _optional_numeric(raw, ELEVATION_CANDIDATES),
            "distance_to_nearest_settlement_km": _optional_numeric(
                raw, SETTLEMENT_DISTANCE_CANDIDATES
            ),
        }
    )
    result["valid_coordinates"] = (
        result["source_latitude"].between(23.0, 38.5)
        & result["source_longitude"].between(60.0, 80.5)
    )
    return result


def _unit_sphere(latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
    lat = np.radians(latitude.astype(float))
    lon = np.radians(longitude.astype(float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def _chord_to_metres(chord: np.ndarray | float) -> np.ndarray:
    values = np.clip(np.asarray(chord, dtype=float) / 2.0, 0.0, 1.0)
    return 2.0 * EARTH_RADIUS_M * np.arcsin(values)


def _distance_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    points = _unit_sphere(
        np.asarray([latitude_a, latitude_b]), np.asarray([longitude_a, longitude_b])
    )
    return float(_chord_to_metres(np.linalg.norm(points[0] - points[1])))


def _cluster_rows(
    frame: pd.DataFrame, indices: list[int], duplicate_distance_m: float
) -> list[list[int]]:
    if not indices:
        return []
    if len(indices) == 1:
        return [indices]
    coordinates = _unit_sphere(
        frame.loc[indices, "source_latitude"].to_numpy(),
        frame.loc[indices, "source_longitude"].to_numpy(),
    )
    chord_radius = 2.0 * math.sin(duplicate_distance_m / (2.0 * EARTH_RADIUS_M))
    tree = cKDTree(coordinates)
    parent = list(range(len(indices)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left, right in tree.query_pairs(chord_radius):
        union(left, right)
    groups: dict[int, list[int]] = {}
    for local_index, frame_index in enumerate(indices):
        groups.setdefault(find(local_index), []).append(frame_index)
    return list(groups.values())


def reconcile_inventories(
    official: pd.DataFrame,
    existing: pd.DataFrame,
    *,
    max_distance_m: float = 750.0,
    ambiguity_margin_m: float = 100.0,
    duplicate_distance_m: float = 50.0,
    identifier_sanity_distance_m: float = 5_000.0,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Reconcile without forcing the newer polygon count onto the PMD master."""
    if max_distance_m <= 0 or duplicate_distance_m < 0 or ambiguity_margin_m < 0:
        raise ValueError("Distance thresholds must be non-negative and max distance positive")
    report = existing.copy()
    for column, default in (
        ("status", "unmatched"),
        ("official_lake_id", None),
        ("match_method", None),
        ("match_distance_m", np.nan),
        ("nearest_official_lake_id", None),
        ("nearest_distance_m", np.nan),
        ("second_nearest_distance_m", np.nan),
        ("duplicate_of_source_lake_id", None),
        ("review_reason", None),
    ):
        report[column] = default

    official_points = _unit_sphere(
        official["latitude"].to_numpy(), official["longitude"].to_numpy()
    )
    tree = cKDTree(official_points)
    valid_indices = report.index[report["valid_coordinates"]].tolist()
    if valid_indices:
        source_points = _unit_sphere(
            report.loc[valid_indices, "source_latitude"].to_numpy(),
            report.loc[valid_indices, "source_longitude"].to_numpy(),
        )
        chord, nearest_index = tree.query(source_points, k=2)
        distances = _chord_to_metres(chord)
        for offset, frame_index in enumerate(valid_indices):
            first = int(nearest_index[offset, 0])
            report.at[frame_index, "nearest_official_lake_id"] = official.at[
                first, "official_lake_id"
            ]
            report.at[frame_index, "nearest_distance_m"] = float(distances[offset, 0])
            report.at[frame_index, "second_nearest_distance_m"] = float(
                distances[offset, 1]
            )

    official_by_id = dict(
        zip(official["official_id_normalized"], official.index, strict=True)
    )
    official_index_by_lake_id = dict(
        zip(official["official_lake_id"], official.index, strict=True)
    )
    candidate_groups: dict[str, list[int]] = {}
    new_candidate_indices: list[int] = []
    for index, row in report.iterrows():
        if not bool(row["valid_coordinates"]):
            report.at[index, "status"] = "unmatched"
            report.at[index, "review_reason"] = "invalid_or_missing_coordinates"
            continue
        official_index: int | None = None
        method: str | None = None
        distance: float | None = None
        source_official_id = str(row["source_official_id_normalized"] or "")
        if source_official_id and source_official_id in official_by_id:
            official_index = int(official_by_id[source_official_id])
            target = official.loc[official_index]
            distance = _distance_m(
                float(row["source_latitude"]),
                float(row["source_longitude"]),
                float(target["latitude"]),
                float(target["longitude"]),
            )
            if distance > identifier_sanity_distance_m:
                report.at[index, "status"] = "unmatched"
                report.at[index, "review_reason"] = "identifier_coordinate_conflict"
                continue
            method = "identifier"
        else:
            nearest = float(row["nearest_distance_m"])
            second = float(row["second_nearest_distance_m"])
            if nearest <= max_distance_m:
                if second <= max_distance_m and second - nearest < ambiguity_margin_m:
                    report.at[index, "status"] = "unmatched"
                    report.at[index, "review_reason"] = "ambiguous_coordinate_match"
                    continue
                official_id = str(row["nearest_official_lake_id"])
                official_index = int(official_index_by_lake_id[official_id])
                distance = nearest
                method = "coordinate"
            else:
                new_candidate_indices.append(index)
                continue
        official_id = str(official.at[official_index, "official_lake_id"])
        report.at[index, "official_lake_id"] = official_id
        report.at[index, "match_method"] = method
        report.at[index, "match_distance_m"] = distance
        candidate_groups.setdefault(official_id, []).append(index)

    for official_id, indices in candidate_groups.items():
        official_area = official.at[
            official_index_by_lake_id[official_id], "official_area_km2"
        ]

        def candidate_key(index: int) -> tuple[float, float, float, int]:
            row = report.loc[index]
            method_rank = 0.0 if row["match_method"] == "identifier" else 1.0
            distance = float(row["match_distance_m"])
            area = float(row["source_area_km2"]) if pd.notna(row["source_area_km2"]) else np.nan
            area_difference = (
                abs(area - float(official_area))
                if pd.notna(official_area) and math.isfinite(area)
                else math.inf
            )
            return method_rank, distance, area_difference, int(row["source_row_number"])

        primary = min(indices, key=candidate_key)
        report.at[primary, "status"] = "matched"
        report.at[primary, "review_reason"] = "primary_match"
        primary_source_id = str(report.at[primary, "source_lake_id"])
        for index in indices:
            if index == primary:
                continue
            report.at[index, "status"] = "duplicate"
            report.at[index, "duplicate_of_source_lake_id"] = primary_source_id
            report.at[index, "review_reason"] = "additional_row_for_same_official_lake"

    for cluster in _cluster_rows(report, new_candidate_indices, duplicate_distance_m):
        primary = max(
            cluster,
            key=lambda index: (
                float(report.at[index, "source_area_km2"])
                if pd.notna(report.at[index, "source_area_km2"])
                else -1.0,
                -int(report.at[index, "source_row_number"]),
            ),
        )
        report.at[primary, "status"] = "newly_detected_candidate"
        report.at[primary, "review_reason"] = (
            "newer_inventory_row_outside_official_match_radius_requires_expert_review"
        )
        primary_source_id = str(report.at[primary, "source_lake_id"])
        for index in cluster:
            if index == primary:
                continue
            report.at[index, "status"] = "duplicate"
            report.at[index, "duplicate_of_source_lake_id"] = primary_source_id
            report.at[index, "review_reason"] = "near_duplicate_new_detection_candidate"

    coverage = official.copy()
    coverage["coverage_status"] = "unmatched"
    coverage["matched_source_count"] = 0
    coverage["matched_source_lake_id"] = None
    coverage["matched_source_system_lake_id"] = None
    coverage["match_method"] = None
    coverage["match_distance_m"] = np.nan
    coverage["reference_area_km2"] = coverage["official_area_km2"]
    coverage["elevation_m"] = np.nan
    coverage["distance_to_nearest_settlement_km"] = np.nan
    coverage_index_by_lake_id = dict(
        zip(coverage["official_lake_id"], coverage.index, strict=True)
    )
    for official_id, group in report.loc[
        report["status"].isin(["matched", "duplicate"])
        & report["official_lake_id"].notna()
    ].groupby("official_lake_id"):
        primary = group.loc[group["status"] == "matched"].iloc[0]
        target = coverage_index_by_lake_id[official_id]
        coverage.at[target, "coverage_status"] = "matched"
        coverage.at[target, "matched_source_count"] = int(len(group))
        coverage.at[target, "matched_source_lake_id"] = primary["source_lake_id"]
        coverage.at[target, "matched_source_system_lake_id"] = primary[
            "source_system_lake_id"
        ]
        coverage.at[target, "match_method"] = primary["match_method"]
        coverage.at[target, "match_distance_m"] = primary["match_distance_m"]
        if pd.isna(coverage.at[target, "reference_area_km2"]):
            coverage.at[target, "reference_area_km2"] = primary["source_area_km2"]
        coverage.at[target, "elevation_m"] = primary["elevation_m"]
        coverage.at[target, "distance_to_nearest_settlement_km"] = primary[
            "distance_to_nearest_settlement_km"
        ]

    counts = report["status"].value_counts().to_dict()
    summary: dict[str, Any] = {
        "official_master_count": int(len(official)),
        "existing_row_count": int(len(existing)),
        "matched_official_lakes": int((coverage["coverage_status"] == "matched").sum()),
        "unmatched_official_lakes": int((coverage["coverage_status"] == "unmatched").sum()),
        "matched_source_rows": int(counts.get("matched", 0)),
        "duplicate_source_rows": int(counts.get("duplicate", 0)),
        "unmatched_source_rows": int(counts.get("unmatched", 0)),
        "newly_detected_candidate_rows": int(
            counts.get("newly_detected_candidate", 0)
        ),
        "classification_total": int(sum(counts.values())),
        "new_detection_status": "candidate_only_requires_expert_verification",
    }
    if summary["classification_total"] != len(existing):
        raise AssertionError("Every existing row must have exactly one reconciliation status")
    return report, coverage, summary


def _latest_records(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    frame = pd.read_csv(path)
    if frame.empty or "lake_id" not in frame:
        return {}
    sort_columns = [
        column
        for column in ("observation_date", "prediction_timestamp")
        if column in frame.columns
    ]
    if sort_columns:
        frame = frame.sort_values(sort_columns)
    latest = frame.drop_duplicates("lake_id", keep="last")
    return {
        str(row["lake_id"]): row.to_dict()
        for _, row in latest.iterrows()
    }


def attach_prediction_availability(
    coverage: pd.DataFrame, time_series_path: Path | None
) -> pd.DataFrame:
    """Attach risk only when critical real-data inputs are actually present."""
    result = coverage.copy()
    records = _latest_records(time_series_path)
    output_rows: list[dict[str, Any]] = []
    for _, lake in result.iterrows():
        official_id = str(lake["official_lake_id"])
        source_id = lake.get("matched_source_system_lake_id")
        record = records.get(official_id)
        if record is None and pd.notna(source_id):
            record = records.get(str(source_id))
        row = lake.to_dict()
        missing: list[str] = []
        if record is None:
            missing = list(DYNAMIC_REQUIRED_FIELDS)
            missing.append("no_prediction_record")
        else:
            for field in DYNAMIC_REQUIRED_FIELDS:
                value = record.get(field)
                if value is None or pd.isna(value) or str(value).strip() == "":
                    missing.append(field)
            if str(record.get("source_mode", "")).upper() != "REAL":
                missing.append("real_source_data")
            if str(record.get("data_quality_status", "")).upper() in {
                "MISSING",
                "MOCK",
                "MODEL_UNAVAILABLE",
            }:
                missing.append("acceptable_data_quality")
        available = record is not None and not missing
        row.update(
            {
                "prediction_availability": "Available" if available else "Data unavailable",
                "risk_score": record.get("risk_score") if available and record else None,
                "risk_level": record.get("risk_level") if available and record else "Data unavailable",
                "confidence_level": record.get("confidence_level") if available and record else None,
                "last_satellite_update": record.get("satellite_observation_date") if record else None,
                "last_weather_update": record.get("weather_observation_date") if record else None,
                "model_version": record.get("model_version") if available and record else None,
                "data_quality_warning": (
                    record.get("data_quality_warning")
                    if available and record
                    else "Missing required prediction features: " + ", ".join(dict.fromkeys(missing))
                ),
            }
        )
        output_rows.append(row)
    return pd.DataFrame(output_rows)


def _json_value(value: object) -> object:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {str(key): _json_value(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def write_master_map(
    coverage: pd.DataFrame,
    output_path: Path,
    *,
    expected_count: int = 3_044,
) -> None:
    if len(coverage) != expected_count:
        raise ValueError(
            f"Map generation refused: expected {expected_count:,} official lakes, "
            f"received {len(coverage):,}."
        )
    points = []
    for _, row in coverage.iterrows():
        points.append(
            {
                "id": _json_value(row["official_lake_id"]),
                "name": _json_value(row.get("lake_name")),
                "basin": _json_value(row.get("basin")),
                "lat": round(float(row["latitude"]), 6),
                "lon": round(float(row["longitude"]), 6),
                "coverage": _json_value(row.get("coverage_status")),
                "availability": _json_value(row.get("prediction_availability")),
                "score": _json_value(row.get("risk_score")),
                "level": _json_value(row.get("risk_level")),
                "confidence": _json_value(row.get("confidence_level")),
                "satellite": _json_value(row.get("last_satellite_update")),
                "weather": _json_value(row.get("last_weather_update")),
                "model": _json_value(row.get("model_version")),
                "warning": _json_value(row.get("data_quality_warning")),
            }
        )
    payload = json.dumps(points, ensure_ascii=True, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    safe_notice = html.escape(SAFETY_NOTICE)
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pakistan PMD Glacial Lake Master Coverage</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIINfQ3ynh17pzgD7d5TnYh2pKKWlfXvTLs=" crossorigin="">
  <style>
    html, body {{ height: 100%; margin: 0; font-family: system-ui, sans-serif; }}
    #map {{ height: 100%; background: #dce5e8; }}
    .summary, .legend {{ background: white; padding: 10px 12px; border-radius: 4px; box-shadow: 0 1px 5px rgba(0,0,0,.28); line-height: 1.45; }}
    .summary {{ max-width: 330px; }}
    .summary strong {{ display: block; margin-bottom: 4px; }}
    .notice {{ margin-top: 6px; font-size: 12px; }}
    .legend i {{ width: 11px; height: 11px; border-radius: 50%; display: inline-block; margin-right: 6px; }}
  </style>
</head>
<body>
<main id="map" aria-label="Map of all 3,044 official PMD glacial lakes in Pakistan"></main>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
const lakes = {payload};
if (lakes.length !== {expected_count}) throw new Error("Official PMD coverage count mismatch");
const palette = {{Low:"#16834b",Medium:"#d07a00",High:"#c7352d","Data unavailable":"#66727a"}};
const map = L.map("map", {{preferCanvas:true}}).setView([35.5, 74.8], 6);
L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{maxZoom:18, attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
const bounds = [];
const escapeHtml = value => String(value ?? "").replace(/[&<>\"']/g, char => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'\"':"&quot;","'":"&#39;"}}[char]));
for (const lake of lakes) {{
  const state = lake.availability === "Available" ? lake.level : "Data unavailable";
  const color = palette[state] || palette["Data unavailable"];
  const marker = L.circleMarker([lake.lat, lake.lon], {{radius:4, weight:1, color, fillColor:color, fillOpacity:.76}});
  const score = lake.availability === "Available" ? `${{Number(lake.score).toFixed(1)}} / 100 (${{escapeHtml(lake.level)}})` : "Data unavailable";
  marker.bindPopup(`<strong>${{escapeHtml(lake.id)}}</strong>${{lake.name ? `<br>${{escapeHtml(lake.name)}}` : ""}}<br>Basin: ${{escapeHtml(lake.basin || "Not supplied")}}<br>2020 inventory match: ${{escapeHtml(lake.coverage)}}<br>Risk indicator: ${{score}}<br>Confidence: ${{escapeHtml(lake.confidence || "Data unavailable")}}<br>Last satellite: ${{escapeHtml(lake.satellite || "Data unavailable")}}<br>Last weather: ${{escapeHtml(lake.weather || "Data unavailable")}}<br>Model: ${{escapeHtml(lake.model || "Data unavailable")}}<br>Data quality: ${{escapeHtml(lake.warning || "No warning")}}`);
  marker.addTo(map);
  bounds.push([lake.lat, lake.lon]);
}}
if (bounds.length) map.fitBounds(bounds, {{padding:[18,18]}});
const summary = L.control({{position:"topright"}});
summary.onAdd = () => {{
  const available = lakes.filter(lake => lake.availability === "Available").length;
  const div = L.DomUtil.create("section", "summary");
  div.innerHTML = `<strong>Official PMD master: ${{lakes.length.toLocaleString()}} lakes</strong><div>${{available.toLocaleString()}} with sufficient prediction features</div><div>${{(lakes.length-available).toLocaleString()}} marked Data unavailable</div><div class="notice">{safe_notice}</div>`;
  return div;
}};
summary.addTo(map);
const legend = L.control({{position:"bottomright"}});
legend.onAdd = () => {{
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = Object.entries(palette).map(([label,color]) => `<div><i style="background:${{color}}"></i>${{label}}</div>`).join("");
  return div;
}};
legend.addTo(map);
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")


def run_reconciliation(
    official_path: Path,
    existing_path: Path,
    output_dir: Path,
    *,
    repository: Repository | None = None,
    time_series_path: Path | None = None,
    expected_count: int = 3_044,
    max_distance_m: float = 750.0,
    ambiguity_margin_m: float = 100.0,
    duplicate_distance_m: float = 50.0,
    official_id_column: str | None = None,
    source_id_column: str | None = None,
    source_official_id_column: str | None = None,
) -> dict[str, Any]:
    official_path = official_path.resolve()
    existing_path = existing_path.resolve()
    official_hash = file_sha256(official_path)
    existing_hash = file_sha256(existing_path)
    official = load_official_inventory(
        official_path,
        expected_count=expected_count,
        id_column=official_id_column,
    )
    existing = load_existing_inventory(
        existing_path,
        id_column=source_id_column,
        official_id_column=source_official_id_column,
    )
    report, coverage, summary = reconcile_inventories(
        official,
        existing,
        max_distance_m=max_distance_m,
        ambiguity_margin_m=ambiguity_margin_m,
        duplicate_distance_m=duplicate_distance_m,
    )
    coverage = attach_prediction_availability(coverage, time_series_path)
    summary["official_lakes_with_prediction_data"] = int(
        (coverage["prediction_availability"] == "Available").sum()
    )
    summary["official_lakes_data_unavailable"] = int(
        (coverage["prediction_availability"] == "Data unavailable").sum()
    )
    parameters = {
        "expected_official_count": expected_count,
        "max_distance_m": max_distance_m,
        "ambiguity_margin_m": ambiguity_margin_m,
        "duplicate_distance_m": duplicate_distance_m,
        "official_id_column": official_id_column,
        "source_id_column": source_id_column,
        "source_official_id_column": source_official_id_column,
    }
    created_at = datetime.now(UTC).isoformat()
    reconciliation_id = hashlib.sha256(
        json.dumps(
            {
                "official": official_hash,
                "existing": existing_hash,
                "parameters": parameters,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:20]
    output_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(output_dir / "inventory_matching_report.csv", index=False)
    coverage.to_csv(output_dir / "pmd_master_coverage.csv", index=False)
    report.loc[report["status"] == "duplicate"].to_csv(
        output_dir / "duplicate_source_rows.csv", index=False
    )
    report.loc[report["status"] == "unmatched"].to_csv(
        output_dir / "unmatched_source_rows.csv", index=False
    )
    report.loc[report["status"] == "newly_detected_candidate"].to_csv(
        output_dir / "newly_detected_candidates.csv", index=False
    )
    coverage.loc[coverage["coverage_status"] == "unmatched"].to_csv(
        output_dir / "unmatched_official_lakes.csv", index=False
    )
    summary_document = {
        **summary,
        "reconciliation_id": reconciliation_id,
        "created_at": created_at,
        "official_source_path": str(official_path),
        "official_source_sha256": official_hash,
        "existing_source_path": str(existing_path),
        "existing_source_sha256": existing_hash,
        "parameters": parameters,
        "safety_notice": SAFETY_NOTICE,
    }
    (output_dir / "inventory_matching_summary.json").write_text(
        json.dumps(summary_document, indent=2), encoding="utf-8"
    )
    write_master_map(
        coverage, output_dir / "pakistan_pmd_lakes_map.html", expected_count=expected_count
    )
    if repository is not None:
        reconciliation = {
            "reconciliation_id": reconciliation_id,
            "created_at": created_at,
            "official_source_path": str(official_path),
            "official_source_sha256": official_hash,
            "existing_source_path": str(existing_path),
            "existing_source_sha256": existing_hash,
            "parameters": parameters,
            "summary": summary,
        }
        repository.replace_official_inventory(
            _records(coverage), _records(report), reconciliation
        )
    return summary_document
