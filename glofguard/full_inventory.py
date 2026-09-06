"""Safe, resumable real-data processing for the full 2020 lake inventory."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from .config import Settings
from .features import area_change_percent
from .schemas import SAFETY_NOTICE
from .storage import Repository
from .types import (
    DailyWeather,
    ForecastSummary,
    Lake,
    ObservedRainfallSummary,
    QualityStatus,
    SatelliteObservation,
)
from .validation import validate_daily_weather, validate_lake, validate_satellite


STATUS_COLUMNS = [
    "lake_id",
    "source_polygon_id",
    "canonical_lake_id",
    "latitude",
    "longitude",
    "processing_status",
    "latest_usable_satellite_date",
    "current_area_km2",
    "satellite_cloud_percentage",
    "satellite_quality_status",
    "satellite_area_freshness",
    "satellite_area_age_days",
    "sentinel_search_window_days",
    "sentinel_diagnostic_reason",
    "sentinel_raw_scene_count",
    "sentinel_metadata_clear_scene_count",
    "sentinel_cloud_score_match_count",
    "sentinel_candidates_measured",
    "weather_observation_date",
    "current_temperature_c",
    "current_rainfall_mm",
    "rainfall_last_7d_mm",
    "rainfall_last_24h_mm",
    "rainfall_last_30d_mm",
    "observed_weather_source",
    "observed_weather_latest_at",
    "observed_weather_freshness",
    "observed_weather_data_quality",
    "observed_weather_age_hours",
    "observed_weather_coverage_24h",
    "observed_weather_coverage_7d",
    "observed_weather_coverage_30d",
    "forecast_source",
    "forecast_created_at",
    "forecast_freshness",
    "forecast_data_quality",
    "forecast_age_hours",
    "forecast_rainfall_next_72h_mm",
    "forecast_temperature_next_24h_c",
    "forecast_temperature_next_7d_c",
    "forecast_horizon_hours",
    "forecast_precipitation_steps",
    "forecast_temperature_steps_7d",
    "historical_baseline_source",
    "historical_baseline_availability",
    "historical_baseline_latest_date",
    "historical_baseline_years",
    "historical_temperature_7d_average_c",
    "historical_temperature_anomaly_c",
    "historical_rainfall_30d_mm",
    "historical_rainfall_anomaly_30d_mm",
    "source_mode",
    "data_retrieval_timestamp",
    "environmental_conditions_score",
    "environmental_conditions_level",
    "score_interpretation",
    "confidence",
    "failure_or_stale_warning",
    "sentinel_attempts",
    "nasa_power_attempts",
    "gsmap_attempts",
    "gfs_attempts",
    "last_error_source",
    "last_error_type",
    "last_error_message",
    "source_geometry_valid",
    "source_validation_warning",
]

TERMINAL_SUCCESS = {"SUCCESS", "STALE", "NO_USABLE_IMAGERY"}
RETRYABLE_FAILURES = {"FAILED"}
SCORE_INTERPRETATION = (
    "Transparent environmental conditions index (0-100); not a validated GLOF "
    "probability or official warning"
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_safe(value: object) -> object:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _safe_record(row: dict[str, object]) -> dict[str, object]:
    return {key: _json_safe(value) for key, value in row.items()}


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _distance_m(
    latitude_a: np.ndarray,
    longitude_a: np.ndarray,
    latitude_b: np.ndarray,
    longitude_b: np.ndarray,
) -> np.ndarray:
    lat1 = np.radians(latitude_a.astype(float))
    lon1 = np.radians(longitude_a.astype(float))
    lat2 = np.radians(latitude_b.astype(float))
    lon2 = np.radians(longitude_b.astype(float))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = np.sin(delta_lat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(
        delta_lon / 2
    ) ** 2
    return 6_371_008.8 * 2 * np.arctan2(
        np.sqrt(np.clip(value, 0, 1)), np.sqrt(np.clip(1 - value, 0, 1))
    )


def reconcile_source_inventory(
    source_archive: Path,
    processed_csv: Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Map all 8,808 polygons to 8,806 records without hiding exclusions."""
    try:
        import geopandas as gpd
        from shapely.validation import explain_validity
    except ImportError as exc:
        raise RuntimeError("GeoPandas and Shapely are required for source reconciliation") from exc

    source_archive = source_archive.resolve()
    processed_csv = processed_csv.resolve()
    polygons = gpd.read_file(f"zip://{source_archive}!HKH-PK/HKH-PK.shp")
    processed = pd.read_csv(processed_csv)
    required = {
        "sample_id",
        "latitude",
        "longitude",
        "area",
        "elevation",
        "distance_to_nearest_settlement_km",
    }
    missing = sorted(required - set(processed.columns))
    if missing:
        raise ValueError("Processed inventory is missing: " + ", ".join(missing))
    if len(polygons) != 8_808:
        raise ValueError(f"Expected 8,808 source polygons, found {len(polygons):,}")
    if len(processed) != 8_806:
        raise ValueError(f"Expected 8,806 processed rows, found {len(processed):,}")
    if polygons.crs is None:
        raise ValueError("Source shapefile has no declared coordinate reference system")

    geometry_hashes = polygons.geometry.map(
        lambda geometry: hashlib.sha256(geometry.wkb).hexdigest()
    )
    is_duplicate = geometry_hashes.duplicated(keep="first")
    first_position_by_hash: dict[str, int] = {}
    canonical_position: list[int] = []
    for position, geometry_hash in enumerate(geometry_hashes):
        first_position_by_hash.setdefault(geometry_hash, position)
        canonical_position.append(first_position_by_hash[geometry_hash])

    centroids = gpd.GeoSeries(polygons.geometry.centroid, crs=polygons.crs).to_crs(4326)
    geometry_valid = polygons.geometry.is_valid.to_numpy()
    geometry_reasons = [
        None if valid else explain_validity(geometry)
        for valid, geometry in zip(geometry_valid, polygons.geometry, strict=True)
    ]
    processed_position_by_raw: dict[int, int] = {}
    next_processed = 0
    for position, duplicate in enumerate(is_duplicate):
        if not duplicate:
            processed_position_by_raw[position] = next_processed
            next_processed += 1
    if next_processed != len(processed):
        raise AssertionError("Source de-duplication did not reproduce the processed row count")

    rows: list[dict[str, object]] = []
    for position, source_row in polygons.iterrows():
        source_id = int(source_row["OBJECTID_1"])
        canonical_raw_position = canonical_position[position]
        canonical_source_id = int(polygons.iloc[canonical_raw_position]["OBJECTID_1"])
        duplicate = bool(is_duplicate.iloc[position])
        processed_position = processed_position_by_raw[canonical_raw_position]
        processed_row = processed.iloc[processed_position]
        canonical_lake_id = f"PKGL-{int(processed_row['sample_id']):05d}"
        lake_id = (
            f"SOURCE-POLYGON-{source_id:05d}"
            if duplicate
            else canonical_lake_id
        )
        reconciliation_status = "EXCLUDED_EXACT_DUPLICATE" if duplicate else "INCLUDED"
        reason = (
            f"Exact duplicate geometry of source polygon OBJECTID_1={canonical_source_id}; "
            f"canonical processed record is {canonical_lake_id}"
            if duplicate
            else "Unique source geometry mapped in source order after exact de-duplication"
        )
        rows.append(
            {
                "source_row_number": position + 1,
                "source_polygon_id": source_id,
                "lake_id": lake_id,
                "canonical_lake_id": canonical_lake_id,
                "processed_sample_id": int(processed_row["sample_id"]),
                "latitude": float(processed_row["latitude"]),
                "longitude": float(processed_row["longitude"]),
                "reference_area_km2": float(processed_row["area"]),
                "elevation_m": float(processed_row["elevation"]),
                "distance_to_nearest_settlement_km": float(
                    processed_row["distance_to_nearest_settlement_km"]
                ),
                "source_centroid_latitude": float(centroids.y.iloc[position]),
                "source_centroid_longitude": float(centroids.x.iloc[position]),
                "source_area_km2": float(source_row["Shape_Area"]) / 1_000_000,
                "geometry_sha256": str(geometry_hashes.iloc[position]),
                "source_geometry_valid": bool(geometry_valid[position]),
                "source_validation_warning": geometry_reasons[position],
                "reconciliation_status": reconciliation_status,
                "reconciliation_reason": reason,
                "eligible_for_processing": not duplicate,
            }
        )

    reconciliation = pd.DataFrame(rows)
    included = reconciliation[reconciliation["eligible_for_processing"]]
    coordinate_error = _distance_m(
        included["latitude"].to_numpy(),
        included["longitude"].to_numpy(),
        included["source_centroid_latitude"].to_numpy(),
        included["source_centroid_longitude"].to_numpy(),
    )
    area_error = np.abs(
        included["reference_area_km2"].to_numpy()
        - included["source_area_km2"].to_numpy()
    )
    if float(coordinate_error.max()) > 1.0 or float(area_error.max()) > 0.000001:
        raise ValueError(
            "Processed rows do not reproduce the de-duplicated polygon order within "
            "the 1 m coordinate / 1 m2 area tolerances"
        )
    duplicate_rows = reconciliation.loc[
        reconciliation["reconciliation_status"] == "EXCLUDED_EXACT_DUPLICATE"
    ]
    summary: dict[str, object] = {
        "total_source_polygons": int(len(reconciliation)),
        "processed_unique_records": int(included.shape[0]),
        "excluded_exact_duplicate_polygons": int(duplicate_rows.shape[0]),
        "invalid_source_geometries_retained": int(
            (~reconciliation["source_geometry_valid"]).sum()
        ),
        "maximum_centroid_difference_m": round(float(coordinate_error.max()), 4),
        "maximum_area_difference_km2": float(area_error.max()),
        "source_archive_sha256": _file_sha256(source_archive),
        "processed_csv_sha256": _file_sha256(processed_csv),
        "duplicate_source_polygon_ids": duplicate_rows["source_polygon_id"].tolist(),
        "duplicate_canonical_lake_ids": duplicate_rows["canonical_lake_id"].tolist(),
    }
    return reconciliation, summary


class RateLimiter:
    def __init__(
        self,
        minimum_interval_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("Rate-limit interval cannot be negative")
        self.minimum_interval_seconds = minimum_interval_seconds
        self.monotonic = monotonic
        self.sleep = sleep
        self._last_call: float | None = None

    def wait(self) -> None:
        now = self.monotonic()
        if self._last_call is not None:
            remaining = self.minimum_interval_seconds - (now - self._last_call)
            if remaining > 0:
                self.sleep(remaining)
                now = self.monotonic()
        self._last_call = now


@dataclass(frozen=True)
class FullInventoryConfig:
    as_of_date: date
    batch_size: int = 5
    max_lakes: int | None = 5
    rate_limit_seconds: float = 0.5
    max_retries: int = 3
    backoff_base_seconds: float = 2.0
    backoff_cap_seconds: float = 30.0
    power_lookback_days: int = 30
    retry_failed: bool = True

    def validate(self) -> None:
        if self.batch_size < 1:
            raise ValueError("Batch size must be at least 1")
        if self.max_lakes is not None and self.max_lakes < 1:
            raise ValueError("Maximum lakes must be at least 1")
        if self.max_retries < 1:
            raise ValueError("Maximum retries must be at least 1")
        if self.backoff_base_seconds < 0 or self.backoff_cap_seconds < 0:
            raise ValueError("Backoff values cannot be negative")
        if self.power_lookback_days < 7:
            raise ValueError("NASA POWER lookback must be at least 7 days")


@dataclass(frozen=True)
class RetryResult:
    succeeded: bool
    value: object | None
    attempts: int
    last_error: Exception | None


def _error_chain(error: Exception) -> str:
    messages = [f"{type(error).__name__}: {error}"]
    cause = error.__cause__
    while cause is not None and len(messages) < 4:
        messages.append(f"{type(cause).__name__}: {cause}")
        cause = cause.__cause__
    return " <- ".join(messages)


def _condition_level(score: float) -> str:
    if score < 33:
        return "Low"
    if score < 67:
        return "Medium"
    return "High"


def environmental_conditions_score(
    *,
    temperature_7d_c: float | None,
    rainfall_current_mm: float | None,
    rainfall_7d_mm: float | None,
    area_change_30d_percent: float | None,
    stale: bool,
) -> tuple[float | None, str | None, str]:
    """Transparent current-conditions index; deliberately not a probability."""
    rules = (
        (temperature_7d_c, 0.25, lambda value: min(1.0, max(0.0, value) / 10.0)),
        (rainfall_current_mm, 0.25, lambda value: min(1.0, max(0.0, value) / 50.0)),
        (rainfall_7d_mm, 0.30, lambda value: min(1.0, max(0.0, value) / 150.0)),
        (
            area_change_30d_percent,
            0.20,
            lambda value: min(1.0, max(0.0, value) / 20.0),
        ),
    )
    available = [
        (float(value), weight, transform)
        for value, weight, transform in rules
        if value is not None and math.isfinite(float(value))
    ]
    if not available:
        return None, None, "Low"
    available_weight = sum(weight for _, weight, _ in available)
    score = 100 * sum(weight * transform(value) for value, weight, transform in available)
    score /= available_weight
    completeness = available_weight / sum(weight for _, weight, _ in rules)
    confidence = "High" if completeness >= 0.9 and not stale else (
        "Medium" if completeness >= 0.6 and not stale else "Low"
    )
    return round(score, 2), _condition_level(score), confidence


def corrected_environmental_conditions_score(
    *,
    rainfall_24h_mm: float | None,
    rainfall_7d_mm: float | None,
    forecast_rainfall_72h_mm: float | None,
    temperature_anomaly_c: float | None,
    area_change_30d_percent: float | None,
) -> tuple[float | None, str | None]:
    """Transparent multi-source conditions index; never an event probability."""
    rules = (
        (rainfall_24h_mm, 0.25, lambda value: min(1.0, max(0.0, value) / 50.0)),
        (rainfall_7d_mm, 0.20, lambda value: min(1.0, max(0.0, value) / 150.0)),
        (
            forecast_rainfall_72h_mm,
            0.25,
            lambda value: min(1.0, max(0.0, value) / 100.0),
        ),
        (
            temperature_anomaly_c,
            0.15,
            lambda value: min(1.0, max(0.0, value) / 8.0),
        ),
        (
            area_change_30d_percent,
            0.15,
            lambda value: min(1.0, max(0.0, value) / 20.0),
        ),
    )
    available = [
        (float(value), weight, transform)
        for value, weight, transform in rules
        if value is not None and math.isfinite(float(value))
    ]
    if not available:
        return None, None
    available_weight = sum(weight for _, weight, _ in available)
    score = 100 * sum(weight * transform(value) for value, weight, transform in available)
    score /= available_weight
    return round(score, 2), _condition_level(score)


def historical_baseline_features(
    rows: Iterable[DailyWeather],
) -> dict[str, object]:
    """Calculate POWER rolling context without applying live-data freshness rules."""
    complete = sorted(
        [
            row
            for row in rows
            if not row.is_mock
            and row.temperature_c is not None
            and row.rainfall_mm is not None
        ],
        key=lambda row: row.observation_date,
    )
    if not complete:
        return {
            "availability": "UNAVAILABLE",
            "latest_date": None,
            "years": 0,
            "temperature_7d_average_c": None,
            "temperature_anomaly_c": None,
            "rainfall_30d_mm": None,
            "rainfall_anomaly_30d_mm": None,
        }
    latest = complete[-1].observation_date
    last_7 = [
        row for row in complete if latest - timedelta(days=6) <= row.observation_date <= latest
    ]
    last_30 = [
        row
        for row in complete
        if latest - timedelta(days=29) <= row.observation_date <= latest
    ]
    prior_same_month = [
        row
        for row in complete
        if row.observation_date.year < latest.year
        and row.observation_date.month == latest.month
    ]
    prior_years = sorted({row.observation_date.year for row in prior_same_month})
    enough = len(prior_years) >= 3
    temperature_7d = (
        sum(float(row.temperature_c) for row in last_7) / len(last_7)
        if len(last_7) >= 5
        else None
    )
    rainfall_30d = (
        sum(float(row.rainfall_mm) for row in last_30)
        if len(last_30) >= 24
        else None
    )
    historical_temperature = (
        sum(float(row.temperature_c) for row in prior_same_month)
        / len(prior_same_month)
        if enough and prior_same_month
        else None
    )
    monthly_rainfall: list[float] = []
    for year in prior_years:
        values = [
            float(row.rainfall_mm)
            for row in prior_same_month
            if row.observation_date.year == year
        ]
        if len(values) >= 24:
            monthly_rainfall.append(sum(values) * 30 / len(values))
    historical_rainfall_30d = (
        sum(monthly_rainfall) / len(monthly_rainfall)
        if len(monthly_rainfall) >= 3
        else None
    )
    return {
        "availability": "AVAILABLE" if enough else "INSUFFICIENT_HISTORY",
        "latest_date": latest,
        "years": len(prior_years),
        "temperature_7d_average_c": temperature_7d,
        "temperature_anomaly_c": (
            temperature_7d - historical_temperature
            if temperature_7d is not None and historical_temperature is not None
            else None
        ),
        "rainfall_30d_mm": rainfall_30d,
        "rainfall_anomaly_30d_mm": (
            rainfall_30d - historical_rainfall_30d
            if rainfall_30d is not None and historical_rainfall_30d is not None
            else None
        ),
    }


def _latest_real_weather(rows: Iterable[DailyWeather]) -> list[DailyWeather]:
    return sorted(
        [row for row in rows if not row.is_mock], key=lambda row: row.observation_date
    )


class FullInventoryRunner:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: Repository,
        inventory: pd.DataFrame,
        source_summary: dict[str, object],
        satellite_provider: Any,
        power_provider: Any,
        config: FullInventoryConfig,
        output_dir: Path,
        observed_weather_provider: Any | None = None,
        forecast_provider: Any | None = None,
        command_used: str | None = None,
        selected_lake_ids: Iterable[str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        config.validate()
        if settings.data_mode != "real":
            raise ValueError("Full inventory processing requires GLOF_DATA_MODE=real")
        self.settings = settings
        self.repository = repository
        self.inventory = inventory.copy()
        self.source_summary = source_summary
        self.satellite = satellite_provider
        self.power = power_provider
        self.observed_weather = observed_weather_provider
        self.forecast = forecast_provider
        self.corrected_live_design = (
            observed_weather_provider is not None and forecast_provider is not None
        )
        self.config = config
        self.output_dir = output_dir.resolve()
        self.command_used = command_used
        self.selected_lake_ids = (
            list(dict.fromkeys(str(value) for value in selected_lake_ids))
            if selected_lake_ids is not None
            else None
        )
        if self.selected_lake_ids is not None:
            eligible_ids = set(
                self.inventory.loc[
                    self.inventory["eligible_for_processing"], "canonical_lake_id"
                ].astype(str)
            )
            unknown = sorted(set(self.selected_lake_ids) - eligible_ids)
            if unknown:
                raise ValueError(
                    "Selected pilot IDs are not eligible inventory lakes: "
                    + ", ".join(unknown[:10])
                )
        self.sleep = sleep
        self.now = now or (lambda: datetime.now(UTC))
        identity = {
            "as_of_date": config.as_of_date.isoformat(),
            "source_archive_sha256": source_summary["source_archive_sha256"],
            "processed_csv_sha256": source_summary["processed_csv_sha256"],
            "score_version": (
                "environmental-conditions-index-v1.1.1-source-freshness"
                if self.corrected_live_design
                else "environmental-conditions-index-v1.0.1"
            ),
            "sources": (
                [
                    "Sentinel-2 SR Harmonized",
                    "JAXA GSMaP V8 operational / Earth Engine",
                    "NOAA GFS0P25 / Earth Engine",
                    "NASA POWER historical baseline",
                ]
                if self.corrected_live_design
                else ["Sentinel-2 SR Harmonized", "NASA POWER daily"]
            ),
        }
        if self.corrected_live_design:
            identity["live_design"] = {
                "sentinel_recent_window_days": settings.sentinel_lookback_days,
                "sentinel_area_search_window_days": settings.sentinel_area_lookback_days,
                "sentinel_area_candidate_cap": settings.sentinel_area_max_candidate_scenes,
                "satellite_stale_days": settings.satellite_stale_days,
                "observed_weather_stale_hours": settings.weather_stale_hours,
                "forecast_stale_hours": settings.forecast_stale_hours,
                "nasa_power_baseline_years": settings.nasa_power_baseline_years,
                "gsmap_collection": "JAXA/GPM_L3/GSMaP/v8/operational",
                "gfs_collection": "NOAA/GFS0P25",
            }
        self.run_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        self.identity = identity
        self.checkpoint_path = self.output_dir / "full_inventory_checkpoint.json"

    def _initial_status(self, row: pd.Series) -> dict[str, object]:
        excluded = not bool(row["eligible_for_processing"])
        status = "EXCLUDED_DUPLICATE" if excluded else "PENDING"
        warning = str(row["reconciliation_reason"]) if excluded else (
            str(row["source_validation_warning"])
            if pd.notna(row["source_validation_warning"])
            else None
        )
        values: dict[str, object] = {
            "lake_id": row["lake_id"],
            "source_polygon_id": int(row["source_polygon_id"]),
            "canonical_lake_id": row["canonical_lake_id"],
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "processing_status": status,
            "latest_usable_satellite_date": None,
            "current_area_km2": None,
            "satellite_cloud_percentage": None,
            "satellite_quality_status": None,
            "satellite_area_freshness": "UNAVAILABLE",
            "satellite_area_age_days": None,
            "sentinel_search_window_days": None,
            "sentinel_diagnostic_reason": None,
            "sentinel_raw_scene_count": None,
            "sentinel_metadata_clear_scene_count": None,
            "sentinel_cloud_score_match_count": None,
            "sentinel_candidates_measured": None,
            "weather_observation_date": None,
            "current_temperature_c": None,
            "current_rainfall_mm": None,
            "rainfall_last_7d_mm": None,
            "rainfall_last_24h_mm": None,
            "rainfall_last_30d_mm": None,
            "observed_weather_source": None,
            "observed_weather_latest_at": None,
            "observed_weather_freshness": "UNAVAILABLE",
            "observed_weather_data_quality": "MISSING",
            "observed_weather_age_hours": None,
            "observed_weather_coverage_24h": None,
            "observed_weather_coverage_7d": None,
            "observed_weather_coverage_30d": None,
            "forecast_source": None,
            "forecast_created_at": None,
            "forecast_freshness": "UNAVAILABLE",
            "forecast_data_quality": "MISSING",
            "forecast_age_hours": None,
            "forecast_rainfall_next_72h_mm": None,
            "forecast_temperature_next_24h_c": None,
            "forecast_temperature_next_7d_c": None,
            "forecast_horizon_hours": None,
            "forecast_precipitation_steps": None,
            "forecast_temperature_steps_7d": None,
            "historical_baseline_source": "NASA POWER",
            "historical_baseline_availability": "UNAVAILABLE",
            "historical_baseline_latest_date": None,
            "historical_baseline_years": None,
            "historical_temperature_7d_average_c": None,
            "historical_temperature_anomaly_c": None,
            "historical_rainfall_30d_mm": None,
            "historical_rainfall_anomaly_30d_mm": None,
            "source_mode": "UNAVAILABLE",
            "data_retrieval_timestamp": None,
            "environmental_conditions_score": None,
            "environmental_conditions_level": None,
            "score_interpretation": SCORE_INTERPRETATION,
            "confidence": "Low",
            "failure_or_stale_warning": warning,
            "sentinel_attempts": 0,
            "nasa_power_attempts": 0,
            "gsmap_attempts": 0,
            "gfs_attempts": 0,
            "last_error_source": None,
            "last_error_type": None,
            "last_error_message": None,
            "source_geometry_valid": bool(row["source_geometry_valid"]),
            "source_validation_warning": _json_safe(row["source_validation_warning"]),
        }
        return _safe_record(values)

    def initialize(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.repository.initialize()
        self.repository.start_full_inventory_run(self.run_id, self.identity)
        existing = {
            str(row["lake_id"])
            for row in self.repository.full_inventory_statuses(self.run_id)
        }
        missing_rows = [
            self._initial_status(row)
            for _, row in self.inventory.iterrows()
            if str(row["lake_id"]) not in existing
        ]
        if missing_rows:
            self.repository.upsert_full_inventory_statuses(self.run_id, missing_rows)

    def _record_error(
        self,
        lake_id: str,
        source: str,
        attempt: int,
        error: Exception,
    ) -> None:
        occurred_at = self.now().astimezone(UTC).isoformat()
        message = _error_chain(error)[:4000]
        event_id = hashlib.sha256(
            f"{self.run_id}|{lake_id}|{source}|{attempt}|{message}".encode("utf-8")
        ).hexdigest()
        event = {
            "event_id": event_id,
            "run_id": self.run_id,
            "lake_id": lake_id,
            "source": source,
            "attempt_number": attempt,
            "occurred_at": occurred_at,
            "error_type": type(error).__name__,
            "error_message": message,
        }
        self.repository.store_full_inventory_error(event)
        self.repository.record_failure(lake_id, source, error)

    def _retry(
        self, lake_id: str, source: str, operation: Callable[[], object]
    ) -> RetryResult:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                return RetryResult(True, operation(), attempt, None)
            except Exception as error:
                last_error = error
                self._record_error(lake_id, source, attempt, error)
                if attempt < self.config.max_retries:
                    delay = min(
                        self.config.backoff_cap_seconds,
                        self.config.backoff_base_seconds * (2 ** (attempt - 1)),
                    )
                    if delay:
                        self.sleep(delay)
        return RetryResult(False, None, self.config.max_retries, last_error)

    def _process_lake_corrected(
        self, source_row: pd.Series, lake: Lake
    ) -> dict[str, object]:
        validate_lake(lake)
        current = self.now().astimezone(UTC)
        as_of = self.config.as_of_date
        warnings: list[str] = []

        sentinel_start = as_of - timedelta(
            days=self.settings.sentinel_area_lookback_days
        )
        sentinel_end = as_of + timedelta(days=1)
        sentinel_result = self._retry(
            lake.lake_id,
            "Sentinel-2",
            lambda: self.satellite.latest(lake, sentinel_start, sentinel_end),
        )
        if sentinel_result.succeeded and sentinel_result.value is not None:
            observation = sentinel_result.value
            if not isinstance(observation, SatelliteObservation):
                raise TypeError("Sentinel provider returned an unexpected result type")
            if observation.is_mock:
                raise ValueError("Mock Sentinel data is forbidden in a REAL run")
            validate_satellite(observation, now=current)
            self.repository.store_satellite(observation)
        elif not sentinel_result.succeeded:
            warnings.append("Sentinel-2 retrieval failed after retries")

        diagnostics = (
            self.satellite.diagnostics_for(lake.lake_id)
            if hasattr(self.satellite, "diagnostics_for")
            else {}
        )
        if not bool(source_row["source_geometry_valid"]):
            warnings.append(
                "Source polygon is invalid, but Earth Engine receives the validated "
                "processed centroid and buffer rather than that polygon geometry"
            )
        reliable_satellite = [
            row
            for row in self.repository.satellite_history(lake.lake_id)
            if not row.is_mock
        ]
        usable_satellite = reliable_satellite[-1] if reliable_satellite else None
        latest_attempt = self.repository.latest_satellite_attempt(
            lake.lake_id, real_only=True
        )
        satellite_age_days = (
            (current - usable_satellite.observed_at).total_seconds() / 86_400
            if usable_satellite is not None
            else None
        )
        satellite_freshness = (
            "UNAVAILABLE"
            if satellite_age_days is None
            else (
                "FRESH"
                if satellite_age_days <= self.settings.satellite_stale_days
                else "STALE"
            )
        )
        if usable_satellite is None:
            warnings.append(
                "Satellite area unavailable: "
                + str(diagnostics.get("diagnostic_reason") or "no reliable measurement")
            )
        elif satellite_freshness == "STALE":
            warnings.append(
                f"Satellite area is {satellite_age_days:.1f} days old and stale"
            )

        gsmap_result = self._retry(
            lake.lake_id,
            "JAXA GSMaP / Earth Engine",
            lambda: self.observed_weather.summary(lake, current),
        )
        observed: ObservedRainfallSummary | None = None
        if gsmap_result.succeeded and gsmap_result.value is not None:
            if not isinstance(gsmap_result.value, ObservedRainfallSummary):
                raise TypeError("GSMaP provider returned an unexpected result type")
            observed = gsmap_result.value
            if observed.is_mock:
                raise ValueError("Mock GSMaP data is forbidden in a REAL run")
            self.repository.store_environmental_source_snapshot(
                lake_id=lake.lake_id,
                source_type="OBSERVED_PRECIPITATION",
                source_timestamp=observed.observation_time.isoformat(),
                source=observed.source,
                source_mode="REAL",
                payload=_safe_record(asdict(observed)),
            )
        elif not gsmap_result.succeeded:
            warnings.append("JAXA GSMaP retrieval failed after retries")
        observed_age_hours = (
            (current - observed.observation_time).total_seconds() / 3600
            if observed is not None
            else None
        )
        observed_freshness = (
            "UNAVAILABLE"
            if observed_age_hours is None
            else (
                "FRESH"
                if observed_age_hours <= self.settings.weather_stale_hours
                else "STALE"
            )
        )
        observed_coverages = (
            [
                observed.available_hours_24h / 24,
                observed.available_hours_7d / 168,
                observed.available_hours_30d / 720,
            ]
            if observed is not None
            else []
        )
        observed_quality = (
            "MISSING"
            if observed is None
            else (
                "COMPLETE"
                if all(
                    coverage >= self.settings.observed_weather_min_coverage
                    for coverage in observed_coverages
                )
                else "PARTIAL"
            )
        )
        if observed is None:
            warnings.append("Recent observed rainfall is unavailable from GSMaP")
        elif observed_freshness == "STALE":
            warnings.append(
                f"GSMaP observed rainfall is {observed_age_hours:.1f} hours old"
            )
        if observed is not None and observed_quality == "PARTIAL":
            warnings.append(
                "GSMaP rolling rainfall coverage is partial: "
                f"{observed.available_hours_24h}/24 hours, "
                f"{observed.available_hours_7d}/168 hours, and "
                f"{observed.available_hours_30d}/720 hours"
            )

        gfs_result = self._retry(
            lake.lake_id,
            "NOAA GFS / Earth Engine",
            lambda: self.forecast.summary(lake, current),
        )
        forecast: ForecastSummary | None = None
        if gfs_result.succeeded and gfs_result.value is not None:
            if not isinstance(gfs_result.value, ForecastSummary):
                raise TypeError("GFS provider returned an unexpected result type")
            forecast = gfs_result.value
            if forecast.is_mock:
                raise ValueError("Mock GFS data is forbidden in a REAL run")
            self.repository.store_environmental_source_snapshot(
                lake_id=lake.lake_id,
                source_type="FORECAST",
                source_timestamp=forecast.forecast_created_at.isoformat(),
                source=forecast.source,
                source_mode="REAL",
                payload=_safe_record(asdict(forecast)),
            )
        elif not gfs_result.succeeded:
            warnings.append("NOAA GFS retrieval failed after retries")
        forecast_age_hours = (
            (current - forecast.forecast_created_at).total_seconds() / 3600
            if forecast is not None
            else None
        )
        forecast_freshness = (
            "UNAVAILABLE"
            if forecast_age_hours is None
            else (
                "FRESH"
                if forecast_age_hours <= self.settings.forecast_stale_hours
                else "STALE"
            )
        )
        forecast_quality = (
            "MISSING"
            if forecast is None
            else (
                "COMPLETE"
                if forecast.precipitation_steps == 12
                and forecast.temperature_steps_24h >= 24
                and forecast.temperature_steps_7d >= 136
                else "PARTIAL"
            )
        )
        if forecast is None:
            warnings.append("GFS forecast is unavailable")
        elif forecast_freshness == "STALE":
            warnings.append(f"GFS forecast cycle is {forecast_age_hours:.1f} hours old")
        if forecast is not None and forecast_quality == "PARTIAL":
            warnings.append(
                "GFS forecast horizons are incomplete: "
                f"{forecast.precipitation_steps}/12 precipitation steps and "
                f"{forecast.temperature_steps_7d}/136 temperature steps"
            )

        existing_power = _latest_real_weather(
            self.repository.daily_weather(lake.lake_id)
        )
        baseline_start = as_of - timedelta(
            days=365 * self.settings.nasa_power_baseline_years + 3
        )
        complete_existing = [
            row
            for row in existing_power
            if row.temperature_c is not None and row.rainfall_mm is not None
        ]
        has_historical_baseline = bool(
            complete_existing
            and complete_existing[0].observation_date <= baseline_start + timedelta(days=45)
        )
        power_start = (
            max(row.observation_date for row in existing_power) + timedelta(days=1)
            if has_historical_baseline and existing_power
            else baseline_start
        )
        power_end = as_of - timedelta(days=1)
        if power_start <= power_end:
            power_result = self._retry(
                lake.lake_id,
                "NASA POWER historical baseline",
                lambda: self.power.daily(lake, power_start, power_end),
            )
        else:
            power_result = RetryResult(True, [], 0, None)
        if power_result.succeeded:
            daily_rows = list(power_result.value or [])
            if any(row.is_mock for row in daily_rows):
                raise ValueError("Mock NASA POWER data is forbidden in a REAL run")
            if daily_rows:
                validate_daily_weather(daily_rows, today=as_of)
                self.repository.store_daily_weather(daily_rows)
        else:
            warnings.append(
                "NASA POWER baseline download failed; retained prior REAL history"
            )
        baseline = historical_baseline_features(
            self.repository.daily_weather(lake.lake_id)
        )
        if baseline["availability"] != "AVAILABLE":
            warnings.append(
                "NASA POWER historical baseline is unavailable or has fewer than "
                "three prior comparison years"
            )

        area_change_30d = area_change_percent(
            usable_satellite, reliable_satellite, 30
        )
        score, level = corrected_environmental_conditions_score(
            rainfall_24h_mm=(
                observed.rainfall_last_24h_mm
                if observed is not None
                and observed.available_hours_24h / 24
                >= self.settings.observed_weather_min_coverage
                else None
            ),
            rainfall_7d_mm=(
                observed.rainfall_last_7d_mm
                if observed is not None
                and observed.available_hours_7d / 168
                >= self.settings.observed_weather_min_coverage
                else None
            ),
            forecast_rainfall_72h_mm=(
                forecast.forecast_rainfall_next_72h_mm
                if forecast is not None and forecast.precipitation_steps == 12
                else None
            ),
            temperature_anomaly_c=baseline["temperature_anomaly_c"],
            area_change_30d_percent=area_change_30d,
        )
        freshness = {
            "satellite": satellite_freshness,
            "observed": observed_freshness,
            "forecast": forecast_freshness,
        }
        fresh_count = sum(value == "FRESH" for value in freshness.values())
        available_count = sum(value != "UNAVAILABLE" for value in freshness.values())
        if (
            fresh_count == 3
            and observed_quality == "COMPLETE"
            and forecast_quality == "COMPLETE"
            and baseline["availability"] == "AVAILABLE"
        ):
            confidence = "High"
        elif available_count >= 2:
            confidence = "Medium"
        else:
            confidence = "Low"
        if (
            available_count == 3
            and observed_quality == "COMPLETE"
            and forecast_quality == "COMPLETE"
        ):
            processing_status = "AVAILABLE"
        elif available_count > 0 or baseline["availability"] == "AVAILABLE":
            processing_status = "PARTIAL"
        elif any(
            not result.succeeded
            for result in (sentinel_result, gsmap_result, gfs_result, power_result)
        ):
            processing_status = "FAILED"
        else:
            processing_status = "UNAVAILABLE"
        source_mode = (
            "REAL"
            if available_count > 0 or baseline["availability"] == "AVAILABLE"
            else "UNAVAILABLE"
        )
        if processing_status == "AVAILABLE" and source_mode != "REAL":
            raise AssertionError("Available observations must have source_mode=REAL")

        errors = [
            ("Sentinel-2", sentinel_result.last_error),
            ("JAXA GSMaP", gsmap_result.last_error),
            ("NOAA GFS", gfs_result.last_error),
            ("NASA POWER", power_result.last_error),
        ]
        last_error_source, last_error = next(
            ((source, error) for source, error in errors if error is not None),
            (None, None),
        )
        satellite_quality = (
            latest_attempt.quality_status.value if latest_attempt is not None else "MISSING"
        )
        if (
            latest_attempt is not None
            and latest_attempt.quality_status == QualityStatus.REJECTED
            and usable_satellite is not None
        ):
            satellite_quality = "REJECTED_USING_LAST_RELIABLE"

        result = {
            "lake_id": lake.lake_id,
            "source_polygon_id": int(source_row["source_polygon_id"]),
            "canonical_lake_id": lake.lake_id,
            "latitude": lake.latitude,
            "longitude": lake.longitude,
            "processing_status": processing_status,
            "latest_usable_satellite_date": (
                usable_satellite.observed_at.isoformat() if usable_satellite else None
            ),
            "current_area_km2": usable_satellite.area_km2 if usable_satellite else None,
            "satellite_cloud_percentage": (
                latest_attempt.cloud_percentage if latest_attempt else None
            ),
            "satellite_quality_status": satellite_quality,
            "satellite_area_freshness": satellite_freshness,
            "satellite_area_age_days": (
                round(satellite_age_days, 3) if satellite_age_days is not None else None
            ),
            "sentinel_search_window_days": diagnostics.get("search_window_days"),
            "sentinel_diagnostic_reason": diagnostics.get("diagnostic_reason"),
            "sentinel_raw_scene_count": diagnostics.get("raw_scene_count"),
            "sentinel_metadata_clear_scene_count": diagnostics.get(
                "metadata_clear_scene_count"
            ),
            "sentinel_cloud_score_match_count": diagnostics.get(
                "cloud_score_match_count"
            ),
            "sentinel_candidates_measured": diagnostics.get("candidates_measured"),
            "weather_observation_date": (
                observed.observation_time.date().isoformat() if observed else None
            ),
            "current_temperature_c": (
                forecast.forecast_temperature_next_24h_c if forecast else None
            ),
            "current_rainfall_mm": (
                observed.rainfall_last_24h_mm if observed else None
            ),
            "rainfall_last_7d_mm": (
                observed.rainfall_last_7d_mm if observed else None
            ),
            "rainfall_last_24h_mm": (
                observed.rainfall_last_24h_mm if observed else None
            ),
            "rainfall_last_30d_mm": (
                observed.rainfall_last_30d_mm if observed else None
            ),
            "observed_weather_source": observed.source if observed else None,
            "observed_weather_latest_at": (
                observed.observation_time.isoformat() if observed else None
            ),
            "observed_weather_freshness": observed_freshness,
            "observed_weather_data_quality": observed_quality,
            "observed_weather_age_hours": (
                round(observed_age_hours, 3) if observed_age_hours is not None else None
            ),
            "observed_weather_coverage_24h": (
                observed.available_hours_24h / 24 if observed else None
            ),
            "observed_weather_coverage_7d": (
                observed.available_hours_7d / 168 if observed else None
            ),
            "observed_weather_coverage_30d": (
                observed.available_hours_30d / 720 if observed else None
            ),
            "forecast_source": forecast.source if forecast else None,
            "forecast_created_at": (
                forecast.forecast_created_at.isoformat() if forecast else None
            ),
            "forecast_freshness": forecast_freshness,
            "forecast_data_quality": forecast_quality,
            "forecast_age_hours": (
                round(forecast_age_hours, 3) if forecast_age_hours is not None else None
            ),
            "forecast_rainfall_next_72h_mm": (
                forecast.forecast_rainfall_next_72h_mm if forecast else None
            ),
            "forecast_temperature_next_24h_c": (
                forecast.forecast_temperature_next_24h_c if forecast else None
            ),
            "forecast_temperature_next_7d_c": (
                forecast.forecast_temperature_next_7d_c if forecast else None
            ),
            "forecast_horizon_hours": (
                forecast.forecast_horizon_hours if forecast else None
            ),
            "forecast_precipitation_steps": (
                forecast.precipitation_steps if forecast else None
            ),
            "forecast_temperature_steps_7d": (
                forecast.temperature_steps_7d if forecast else None
            ),
            "historical_baseline_source": "NASA POWER",
            "historical_baseline_availability": baseline["availability"],
            "historical_baseline_latest_date": (
                baseline["latest_date"].isoformat()
                if baseline["latest_date"] is not None
                else None
            ),
            "historical_baseline_years": baseline["years"],
            "historical_temperature_7d_average_c": baseline[
                "temperature_7d_average_c"
            ],
            "historical_temperature_anomaly_c": baseline["temperature_anomaly_c"],
            "historical_rainfall_30d_mm": baseline["rainfall_30d_mm"],
            "historical_rainfall_anomaly_30d_mm": baseline[
                "rainfall_anomaly_30d_mm"
            ],
            "source_mode": source_mode,
            "data_retrieval_timestamp": current.isoformat(),
            "environmental_conditions_score": score,
            "environmental_conditions_level": level,
            "score_interpretation": SCORE_INTERPRETATION,
            "confidence": confidence,
            "failure_or_stale_warning": "; ".join(dict.fromkeys(warnings)) or None,
            "sentinel_attempts": sentinel_result.attempts,
            "nasa_power_attempts": power_result.attempts,
            "gsmap_attempts": gsmap_result.attempts,
            "gfs_attempts": gfs_result.attempts,
            "last_error_source": last_error_source,
            "last_error_type": type(last_error).__name__ if last_error else None,
            "last_error_message": _error_chain(last_error) if last_error else None,
            "source_geometry_valid": bool(source_row["source_geometry_valid"]),
            "source_validation_warning": _json_safe(
                source_row["source_validation_warning"]
            ),
        }
        return _safe_record(result)

    def _process_lake(self, source_row: pd.Series, lake: Lake) -> dict[str, object]:
        if self.corrected_live_design:
            return self._process_lake_corrected(source_row, lake)
        validate_lake(lake)
        current = self.now().astimezone(UTC)
        as_of = self.config.as_of_date
        warnings: list[str] = []
        sentinel_start = as_of - timedelta(days=self.settings.sentinel_lookback_days)
        sentinel_end = as_of + timedelta(days=1)
        sentinel_result = self._retry(
            lake.lake_id,
            "Sentinel-2",
            lambda: self.satellite.latest(lake, sentinel_start, sentinel_end),
        )
        if sentinel_result.succeeded:
            observation = sentinel_result.value
            if observation is not None:
                if not isinstance(observation, SatelliteObservation):
                    raise TypeError("Sentinel provider returned an unexpected result type")
                if observation.is_mock:
                    raise ValueError("Mock Sentinel data is forbidden in a full real-data run")
                validate_satellite(observation, now=current)
                self.repository.store_satellite(observation)
                if observation.quality_status == QualityStatus.REJECTED:
                    warnings.append(
                        "Latest Sentinel-2 attempt was rejected: "
                        + str(observation.rejection_reason or "unusable imagery")
                    )
            else:
                warnings.append("No Sentinel-2 scene was available in the configured lookback")
        else:
            warnings.append("Sentinel-2 request failed after retries")

        weather_end = as_of - timedelta(days=1)
        weather_start = weather_end - timedelta(days=self.config.power_lookback_days - 1)
        power_result = self._retry(
            lake.lake_id,
            "NASA POWER",
            lambda: self.power.daily(lake, weather_start, weather_end),
        )
        if power_result.succeeded:
            daily_rows = list(power_result.value or [])
            if any(row.is_mock for row in daily_rows):
                raise ValueError("Mock NASA POWER data is forbidden in a full real-data run")
            if not daily_rows:
                warnings.append("NASA POWER returned no daily observations")
            else:
                validate_daily_weather(daily_rows, today=as_of)
                self.repository.store_daily_weather(daily_rows)
        else:
            warnings.append("NASA POWER request failed after retries")

        reliable_satellite = [
            row for row in self.repository.satellite_history(lake.lake_id) if not row.is_mock
        ]
        usable_satellite = reliable_satellite[-1] if reliable_satellite else None
        latest_attempt = self.repository.latest_satellite_attempt(
            lake.lake_id, real_only=True
        )
        weather = _latest_real_weather(self.repository.daily_weather(lake.lake_id))
        usable_weather = [
            row
            for row in weather
            if row.temperature_c is not None and row.rainfall_mm is not None
        ]
        latest_weather = usable_weather[-1] if usable_weather else None

        satellite_stale = bool(
            usable_satellite
            and current - usable_satellite.observed_at
            > timedelta(days=self.settings.satellite_stale_days)
        )
        weather_stale = bool(
            latest_weather
            and 24 * (as_of - latest_weather.observation_date).days
            > self.settings.weather_stale_hours
        )
        if (
            latest_attempt is not None
            and latest_attempt.quality_status == QualityStatus.REJECTED
            and usable_satellite is not None
        ):
            warnings.append(
                "Newest Sentinel-2 attempt was rejected; retained the last reliable area"
            )
        if usable_satellite is None:
            warnings.append("No usable real Sentinel-2 lake-area observation")
        elif satellite_stale:
            warnings.append("Latest usable Sentinel-2 observation is stale")
        if latest_weather is None:
            warnings.append("No usable real NASA POWER weather observation")
        elif weather_stale:
            warnings.append("Latest NASA POWER weather observation is stale")
        if weather and latest_weather is not None and weather[-1] != latest_weather:
            warnings.append(
                "NASA POWER returned newer rows with missing temperature or rainfall; "
                f"latest usable weather is {latest_weather.observation_date.isoformat()}"
            )

        seven_day_start = as_of - timedelta(days=7)
        recent_weather = [
            row
            for row in weather
            if seven_day_start <= row.observation_date < as_of
        ]
        temperatures = [
            float(row.temperature_c)
            for row in recent_weather
            if row.temperature_c is not None
        ]
        rainfall_values = [
            float(row.rainfall_mm)
            for row in recent_weather
            if row.rainfall_mm is not None
        ]
        temperature_7d = (
            float(sum(temperatures) / len(temperatures)) if temperatures else None
        )
        rainfall_7d = float(sum(rainfall_values)) if len(rainfall_values) >= 5 else None
        area_change_30d = area_change_percent(
            usable_satellite, reliable_satellite, 30
        )
        stale = satellite_stale or weather_stale
        score, level, confidence = environmental_conditions_score(
            temperature_7d_c=temperature_7d,
            rainfall_current_mm=(
                latest_weather.rainfall_mm if latest_weather is not None else None
            ),
            rainfall_7d_mm=rainfall_7d,
            area_change_30d_percent=area_change_30d,
            stale=stale,
        )
        if usable_satellite is None:
            score, level, confidence = None, None, "Low"

        if usable_satellite is None and sentinel_result.succeeded:
            processing_status = "NO_USABLE_IMAGERY"
        elif (not sentinel_result.succeeded and usable_satellite is None) or (
            not power_result.succeeded and latest_weather is None
        ) or latest_weather is None:
            processing_status = "FAILED"
        elif stale:
            processing_status = "STALE"
        else:
            processing_status = "SUCCESS"

        source_mode = (
            "REAL"
            if usable_satellite is not None and latest_weather is not None
            else "UNAVAILABLE"
        )
        if source_mode == "UNAVAILABLE":
            warnings.append(
                "No mock fallback was used; missing or failed real data remain unavailable"
            )
        if processing_status == "SUCCESS" and source_mode != "REAL":
            raise AssertionError("Successful observations must have source_mode=REAL")

        last_error = sentinel_result.last_error or power_result.last_error
        last_error_source = (
            "Sentinel-2" if sentinel_result.last_error else (
                "NASA POWER" if power_result.last_error else None
            )
        )
        satellite_quality = (
            latest_attempt.quality_status.value if latest_attempt is not None else "MISSING"
        )
        if (
            latest_attempt is not None
            and latest_attempt.quality_status == QualityStatus.REJECTED
            and usable_satellite is not None
        ):
            satellite_quality = "REJECTED_USING_LAST_RELIABLE"

        result = {
            "lake_id": lake.lake_id,
            "source_polygon_id": int(source_row["source_polygon_id"]),
            "canonical_lake_id": lake.lake_id,
            "latitude": lake.latitude,
            "longitude": lake.longitude,
            "processing_status": processing_status,
            "latest_usable_satellite_date": (
                usable_satellite.observed_at.isoformat() if usable_satellite else None
            ),
            "current_area_km2": usable_satellite.area_km2 if usable_satellite else None,
            "satellite_cloud_percentage": (
                latest_attempt.cloud_percentage if latest_attempt else None
            ),
            "satellite_quality_status": satellite_quality,
            "weather_observation_date": (
                latest_weather.observation_date.isoformat() if latest_weather else None
            ),
            "current_temperature_c": (
                latest_weather.temperature_c if latest_weather else None
            ),
            "current_rainfall_mm": latest_weather.rainfall_mm if latest_weather else None,
            "rainfall_last_7d_mm": rainfall_7d,
            "source_mode": source_mode,
            "data_retrieval_timestamp": current.isoformat(),
            "environmental_conditions_score": score,
            "environmental_conditions_level": level,
            "score_interpretation": SCORE_INTERPRETATION,
            "confidence": confidence,
            "failure_or_stale_warning": "; ".join(dict.fromkeys(warnings)) or None,
            "sentinel_attempts": sentinel_result.attempts,
            "nasa_power_attempts": power_result.attempts,
            "last_error_source": last_error_source,
            "last_error_type": type(last_error).__name__ if last_error else None,
            "last_error_message": _error_chain(last_error) if last_error else None,
            "source_geometry_valid": bool(source_row["source_geometry_valid"]),
            "source_validation_warning": _json_safe(
                source_row["source_validation_warning"]
            ),
        }
        return _safe_record(result)

    def _unexpected_failure_status(
        self,
        source_row: pd.Series,
        previous_status: dict[str, object],
        error: Exception,
    ) -> dict[str, object]:
        """Keep prior real fields while turning an unexpected error into a lake failure."""
        lake_id = str(source_row["canonical_lake_id"])
        self._record_error(lake_id, "Full inventory runner", 1, error)
        result = {
            column: previous_status.get(column)
            for column in STATUS_COLUMNS
        }
        warning = (
            "Unexpected per-lake processing failure; no mock fallback was used: "
            + _error_chain(error)
        )
        existing_warning = previous_status.get("failure_or_stale_warning")
        result.update(
            {
                "lake_id": lake_id,
                "source_polygon_id": int(source_row["source_polygon_id"]),
                "canonical_lake_id": lake_id,
                "latitude": float(source_row["latitude"]),
                "longitude": float(source_row["longitude"]),
                "processing_status": "FAILED",
                "data_retrieval_timestamp": self.now().astimezone(UTC).isoformat(),
                "failure_or_stale_warning": (
                    f"{existing_warning}; {warning}" if existing_warning else warning
                ),
                "last_error_source": "Full inventory runner",
                "last_error_type": type(error).__name__,
                "last_error_message": _error_chain(error),
                "source_geometry_valid": bool(source_row["source_geometry_valid"]),
                "source_validation_warning": _json_safe(
                    source_row["source_validation_warning"]
                ),
            }
        )
        # A malformed provider response must never mark mock material as real.
        if result.get("source_mode") not in {"REAL", "UNAVAILABLE"}:
            result["source_mode"] = "UNAVAILABLE"
        return _safe_record(result)

    def _write_checkpoint(
        self,
        *,
        session_started: datetime,
        processed_in_session: int,
        last_lake_id: str | None,
    ) -> None:
        statuses = self.repository.full_inventory_statuses(self.run_id)
        counts = pd.Series(
            [row["processing_status"] for row in statuses], dtype="object"
        ).value_counts().to_dict()
        payload: dict[str, object] = {
            "run_id": self.run_id,
            "identity": self.identity,
            "operational_config": {
                **asdict(self.config),
                "as_of_date": self.config.as_of_date.isoformat(),
            },
            "session_started_at": session_started.isoformat(),
            "updated_at": self.now().astimezone(UTC).isoformat(),
            "processed_in_session": processed_in_session,
            "last_lake_id": last_lake_id,
            "status_counts": {str(key): int(value) for key, value in counts.items()},
            "resume_source": "SQLite full_inventory_lake_status table",
        }
        _atomic_json(self.checkpoint_path, payload)

    def _status_frame(self) -> pd.DataFrame:
        frame = pd.DataFrame(self.repository.full_inventory_statuses(self.run_id))
        for column in STATUS_COLUMNS:
            if column not in frame:
                frame[column] = None
        order = dict(zip(self.inventory["lake_id"], self.inventory["source_row_number"]))
        frame["_source_order"] = frame["lake_id"].map(order)
        return frame.sort_values("_source_order").drop(columns="_source_order")[STATUS_COLUMNS]

    def write_outputs(
        self,
        *,
        elapsed_seconds: float,
        processed_in_session: int,
        api_counts: dict[str, int],
    ) -> dict[str, object]:
        status = self._status_frame()
        errors = pd.DataFrame(self.repository.full_inventory_errors(self.run_id))
        if errors.empty:
            errors = pd.DataFrame(
                columns=[
                    "event_id",
                    "run_id",
                    "lake_id",
                    "source",
                    "attempt_number",
                    "occurred_at",
                    "error_type",
                    "error_message",
                ]
            )
        _atomic_csv(self.output_dir / "full_inventory_status.csv", status)
        _atomic_csv(self.output_dir / "per_lake_error_log.csv", errors)
        problem_statuses = {
            "EXCLUDED_DUPLICATE",
            "FAILED",
            "NO_USABLE_IMAGERY",
            "STALE",
            "PARTIAL",
            "UNAVAILABLE",
        }
        problems = status[status["processing_status"].isin(problem_statuses)]
        _atomic_csv(self.output_dir / "excluded_or_failed_lakes.csv", problems)
        source_warnings = self.inventory[
            ~self.inventory["source_geometry_valid"]
        ].copy()
        _atomic_csv(self.output_dir / "source_geometry_warnings.csv", source_warnings)

        counts = status["processing_status"].value_counts().to_dict()
        successful = int(counts.get("SUCCESS", 0)) + int(counts.get("AVAILABLE", 0))
        # Corrected live records use the separate source-specific freshness
        # fields below; this legacy combined status is retained only for v1 rows.
        stale = int(counts.get("STALE", 0))
        eligible = int(self.source_summary["processed_unique_records"])
        total = int(self.source_summary["total_source_polygons"])
        real_count = int((status["source_mode"] == "REAL").sum())
        mock_count = int((status["source_mode"] == "MOCK").sum())
        satellite_freshness_counts = (
            status["satellite_area_freshness"].fillna("UNAVAILABLE").value_counts()
        )
        observed_freshness_counts = (
            status["observed_weather_freshness"].fillna("UNAVAILABLE").value_counts()
        )
        observed_quality_counts = (
            status["observed_weather_data_quality"].fillna("MISSING").value_counts()
        )
        forecast_freshness_counts = (
            status["forecast_freshness"].fillna("UNAVAILABLE").value_counts()
        )
        forecast_quality_counts = (
            status["forecast_data_quality"].fillna("MISSING").value_counts()
        )
        baseline_availability_counts = (
            status["historical_baseline_availability"]
            .fillna("UNAVAILABLE")
            .value_counts()
        )
        processed_mask = ~status["processing_status"].isin(
            ["PENDING", "EXCLUDED_DUPLICATE"]
        )
        satellite_unavailable = int(
            (
                processed_mask
                & (status["satellite_area_freshness"] == "UNAVAILABLE")
            ).sum()
        )
        satellite_dates = pd.to_datetime(
            status["latest_usable_satellite_date"], errors="coerce", utc=True
        ).dropna()
        weather_dates = pd.to_datetime(
            status["weather_observation_date"], errors="coerce", utc=True
        ).dropna()
        elapsed_per_lake = (
            elapsed_seconds / processed_in_session if processed_in_session else None
        )
        remaining = int(counts.get("PENDING", 0)) + int(counts.get("FAILED", 0))
        projected_seconds = (
            elapsed_per_lake * remaining if elapsed_per_lake is not None else None
        )
        usage = {
            "run_id": self.run_id,
            "processed_in_session": processed_in_session,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "average_seconds_per_lake": (
                round(elapsed_per_lake, 3) if elapsed_per_lake is not None else None
            ),
            **api_counts,
            "remaining_retryable_lakes": remaining,
            "projected_remaining_seconds_at_observed_rate": (
                round(projected_seconds, 1) if projected_seconds is not None else None
            ),
            "static_full_run_upper_bound": {
                "nasa_power_http_requests": eligible * self.config.max_retries,
                "sentinel_scene_lookup_getinfo_calls": eligible
                * self.config.max_retries,
                "sentinel_scene_measurement_getinfo_calls": eligible
                * (
                    self.settings.sentinel_area_max_candidate_scenes
                    if self.corrected_live_design
                    else self.settings.sentinel_max_candidate_scenes
                )
                * self.config.max_retries,
                "gsmap_summary_getinfo_calls": (
                    eligible * self.config.max_retries
                    if self.corrected_live_design
                    else 0
                ),
                "gfs_summary_getinfo_calls": (
                    eligible * self.config.max_retries
                    if self.corrected_live_design
                    else 0
                ),
                "note": "Upper bound assumes every request consumes all retries and all candidate scenes.",
            },
        }
        _atomic_json(self.output_dir / "api_usage_estimate.json", usage)

        summary: dict[str, object] = {
            "run_id": self.run_id,
            "report_state": "COMPLETE" if int(counts.get("PENDING", 0)) == 0 else "INTERIM",
            "total_source_lakes": total,
            "eligible_unique_lakes": eligible,
            "successfully_processed_lakes": successful,
            "failed_lakes": int(counts.get("FAILED", 0)),
            "lakes_without_usable_imagery": (
                satellite_unavailable
                if self.corrected_live_design
                else int(counts.get("NO_USABLE_IMAGERY", 0))
            ),
            "stale_observations": stale,
            "excluded_exact_duplicates": int(counts.get("EXCLUDED_DUPLICATE", 0)),
            "pending_lakes": int(counts.get("PENDING", 0)),
            "real_count": real_count,
            "mock_count": mock_count,
            "earliest_satellite_observation": (
                satellite_dates.min().isoformat() if not satellite_dates.empty else None
            ),
            "latest_satellite_observation": (
                satellite_dates.max().isoformat() if not satellite_dates.empty else None
            ),
            "earliest_weather_observation": (
                weather_dates.min().isoformat() if not weather_dates.empty else None
            ),
            "latest_weather_observation": (
                weather_dates.max().isoformat() if not weather_dates.empty else None
            ),
            "eligible_success_coverage_percentage": round(
                100 * successful / eligible, 3
            ),
            "overall_source_success_coverage_percentage": round(
                100 * successful / total, 3
            ),
            "error_events": int(len(errors)),
            "exact_command_used": self.command_used,
            "satellite_area_freshness": {
                str(key): int(value)
                for key, value in satellite_freshness_counts.items()
            },
            "observed_weather_freshness": {
                str(key): int(value)
                for key, value in observed_freshness_counts.items()
            },
            "observed_weather_data_quality": {
                str(key): int(value) for key, value in observed_quality_counts.items()
            },
            "forecast_freshness": {
                str(key): int(value)
                for key, value in forecast_freshness_counts.items()
            },
            "forecast_data_quality": {
                str(key): int(value) for key, value in forecast_quality_counts.items()
            },
            "historical_baseline_availability": {
                str(key): int(value)
                for key, value in baseline_availability_counts.items()
            },
        }
        reason_counts = (
            errors.groupby(["source", "error_type"]).size().sort_values(ascending=False)
            if not errors.empty
            else pd.Series(dtype=int)
        )
        reason_lines = [
            f"- {source} / {error_type}: {int(count)} attempt(s)"
            for (source, error_type), count in reason_counts.items()
        ] or ["- No provider exceptions recorded."]
        report = f"""# Full Inventory Data Quality Report

Report state: **{summary['report_state']}**  
Run ID: `{self.run_id}`  
As-of date: `{self.config.as_of_date.isoformat()}`
Exact command used: `{self.command_used or 'Not recorded'}`

## Coverage summary

| Metric | Count |
|---|---:|
| Total source lake polygons | {total:,} |
| Eligible unique lake records | {eligible:,} |
| Successfully processed lakes | {successful:,} |
| Failed lakes | {summary['failed_lakes']:,} |
| Lakes without usable imagery | {summary['lakes_without_usable_imagery']:,} |
| Stale observations | {stale:,} |
| Exact duplicate source polygons excluded | {summary['excluded_exact_duplicates']:,} |
| Pending lakes | {summary['pending_lakes']:,} |
| REAL observations | {real_count:,} |
| MOCK observations | {mock_count:,} |

## Source-specific availability and freshness

- Satellite-area freshness: {summary['satellite_area_freshness']}
- Observed-weather freshness: {summary['observed_weather_freshness']}
- Observed-weather data quality: {summary['observed_weather_data_quality']}
- Forecast freshness: {summary['forecast_freshness']}
- Forecast data quality: {summary['forecast_data_quality']}
- Historical-baseline availability: {summary['historical_baseline_availability']}

Eligible success coverage: **{summary['eligible_success_coverage_percentage']:.3f}%**  
Overall source success coverage: **{summary['overall_source_success_coverage_percentage']:.3f}%**

## Observation dates

- Earliest usable satellite observation: {summary['earliest_satellite_observation'] or 'Unavailable'}
- Latest usable satellite observation: {summary['latest_satellite_observation'] or 'Unavailable'}
- Earliest weather observation: {summary['earliest_weather_observation'] or 'Unavailable'}
- Latest weather observation: {summary['latest_weather_observation'] or 'Unavailable'}

## Reconciliation of 8,808 versus 8,806

The original archive contains 8,808 polygon rows but only 8,806 unique geometries. Source polygon `OBJECTID_1=6960` is an exact geometry duplicate of `6121`, and `OBJECTID_1=6961` is an exact geometry duplicate of `6122`. The canonical earlier rows map to `PKGL-06121` and `PKGL-06122`. The later copies remain in the audit outputs as `EXCLUDED_DUPLICATE`; they are not silently deleted and do not trigger duplicate API requests.

The source also contains {self.source_summary['invalid_source_geometries_retained']:,} geometries reported invalid by Shapely. They remain eligible because the processed centroid and area fields are available. Every warning is listed in `source_geometry_warnings.csv`.

## Provider failure reasons

{chr(10).join(reason_lines)}

Every provider exception is retained in `per_lake_error_log.csv`. Every excluded, failed, unavailable-imagery, or stale record is listed in `excluded_or_failed_lakes.csv`.

## Score interpretation

`environmental_conditions_score` is a transparent 0-100 index based on available recent temperature, observed rainfall, and lake-area change. It is **not a validated GLOF probability**, does not assert that a flood will occur, and must not be used as an official warning.

> {SAFETY_NOTICE}
"""
        report_path = self.output_dir / "FULL_DATA_QUALITY_REPORT.md"
        temporary = report_path.with_suffix(report_path.suffix + ".tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(report_path)
        _atomic_json(self.output_dir / "full_inventory_summary.json", summary)
        return summary

    def run(self) -> dict[str, object]:
        self.initialize()
        session_started = self.now().astimezone(UTC)
        start_monotonic = time.monotonic()
        statuses = {
            str(row["lake_id"]): row
            for row in self.repository.full_inventory_statuses(self.run_id)
        }
        row_by_lake_id = {
            str(row["canonical_lake_id"]): row
            for _, row in self.inventory[self.inventory["eligible_for_processing"]].iterrows()
        }
        candidate_order = (
            self.selected_lake_ids
            if self.selected_lake_ids is not None
            else list(row_by_lake_id)
        )
        candidates: list[pd.Series] = []
        for lake_id in candidate_order:
            row = row_by_lake_id[lake_id]
            if not bool(row["eligible_for_processing"]):
                continue
            current_status = str(statuses[str(row["lake_id"])]["processing_status"])
            if current_status == "PENDING" or (
                self.config.retry_failed and current_status in RETRYABLE_FAILURES
            ):
                candidates.append(row)
        if self.config.max_lakes is not None:
            candidates = candidates[: self.config.max_lakes]

        lake_by_id = {
            str(row["canonical_lake_id"]): Lake(
                lake_id=str(row["canonical_lake_id"]),
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                elevation_m=float(row["elevation_m"]),
                distance_to_nearest_settlement_km=float(
                    row["distance_to_nearest_settlement_km"]
                ),
                reference_area_km2=float(row["reference_area_km2"]),
            )
            for _, row in self.inventory[self.inventory["eligible_for_processing"]].iterrows()
        }
        self.repository.upsert_lakes(lake_by_id.values())
        starting_api_counts = {
            "sentinel_scene_lookup_calls": int(
                getattr(self.satellite, "scene_lookup_calls", 0)
            ),
            "sentinel_scene_measurement_calls": int(
                getattr(self.satellite, "scene_measurement_calls", 0)
            ),
            "nasa_power_http_requests": int(getattr(self.power, "request_count", 0)),
            "gsmap_earth_engine_summary_calls": int(
                getattr(self.observed_weather, "summary_calls", 0)
            ),
            "gfs_earth_engine_summary_calls": int(
                getattr(self.forecast, "summary_calls", 0)
            ),
        }
        processed = 0
        last_lake_id: str | None = None
        for batch_start in range(0, len(candidates), self.config.batch_size):
            batch = candidates[batch_start : batch_start + self.config.batch_size]
            for source_row in batch:
                lake_id = str(source_row["canonical_lake_id"])
                try:
                    result = self._process_lake(source_row, lake_by_id[lake_id])
                except Exception as error:
                    result = self._unexpected_failure_status(
                        source_row,
                        statuses[lake_id],
                        error,
                    )
                self.repository.upsert_full_inventory_statuses(self.run_id, [result])
                statuses[lake_id] = result
                processed += 1
                last_lake_id = lake_id
                self._write_checkpoint(
                    session_started=session_started,
                    processed_in_session=processed,
                    last_lake_id=last_lake_id,
                )
            api_counts = {
                "sentinel_scene_lookup_calls": int(
                    getattr(self.satellite, "scene_lookup_calls", 0)
                )
                - starting_api_counts["sentinel_scene_lookup_calls"],
                "sentinel_scene_measurement_calls": int(
                    getattr(self.satellite, "scene_measurement_calls", 0)
                )
                - starting_api_counts["sentinel_scene_measurement_calls"],
                "nasa_power_http_requests": int(getattr(self.power, "request_count", 0))
                - starting_api_counts["nasa_power_http_requests"],
                "gsmap_earth_engine_summary_calls": int(
                    getattr(self.observed_weather, "summary_calls", 0)
                )
                - starting_api_counts["gsmap_earth_engine_summary_calls"],
                "gfs_earth_engine_summary_calls": int(
                    getattr(self.forecast, "summary_calls", 0)
                )
                - starting_api_counts["gfs_earth_engine_summary_calls"],
            }
            self.write_outputs(
                elapsed_seconds=time.monotonic() - start_monotonic,
                processed_in_session=processed,
                api_counts=api_counts,
            )

        api_counts = {
            "sentinel_scene_lookup_calls": int(
                getattr(self.satellite, "scene_lookup_calls", 0)
            )
            - starting_api_counts["sentinel_scene_lookup_calls"],
            "sentinel_scene_measurement_calls": int(
                getattr(self.satellite, "scene_measurement_calls", 0)
            )
            - starting_api_counts["sentinel_scene_measurement_calls"],
            "nasa_power_http_requests": int(getattr(self.power, "request_count", 0))
            - starting_api_counts["nasa_power_http_requests"],
            "gsmap_earth_engine_summary_calls": int(
                getattr(self.observed_weather, "summary_calls", 0)
            )
            - starting_api_counts["gsmap_earth_engine_summary_calls"],
            "gfs_earth_engine_summary_calls": int(
                getattr(self.forecast, "summary_calls", 0)
            )
            - starting_api_counts["gfs_earth_engine_summary_calls"],
        }
        elapsed_seconds = time.monotonic() - start_monotonic
        summary = self.write_outputs(
            elapsed_seconds=elapsed_seconds,
            processed_in_session=processed,
            api_counts=api_counts,
        )
        self.repository.set_full_inventory_run_status(
            self.run_id,
            "COMPLETE" if summary["report_state"] == "COMPLETE" else "CHECKPOINTED",
        )
        return {
            **summary,
            **api_counts,
            "processed_in_session": processed,
            "elapsed_seconds": round(elapsed_seconds, 3),
        }


def prepare_reconciliation_outputs(
    source_archive: Path, processed_csv: Path, output_dir: Path
) -> tuple[pd.DataFrame, dict[str, object]]:
    inventory, summary = reconcile_source_inventory(source_archive, processed_csv)
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_csv(output_dir / "source_inventory_reconciliation.csv", inventory)
    _atomic_csv(
        output_dir / "source_geometry_warnings.csv",
        inventory[~inventory["source_geometry_valid"]],
    )
    _atomic_json(output_dir / "source_inventory_reconciliation_summary.json", summary)
    return inventory, summary


PILOT_BAND_LABELS = {
    "latitude_band": ["SOUTH", "CENTRAL", "NORTH"],
    "longitude_band": ["WEST", "CENTRAL", "EAST"],
    "elevation_band": ["LOW", "MEDIUM", "HIGH"],
    "area_band": ["SMALL", "MEDIUM", "LARGE"],
}


def _tercile_band(values: pd.Series, labels: list[str]) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().sum() == 0:
        raise ValueError("Representative pilot stratification field has no usable values")
    numeric = numeric.fillna(float(numeric.median()))
    percentile = numeric.rank(method="average", pct=True)
    codes = np.clip(np.ceil(percentile * 3).astype(int) - 1, 0, 2)
    return pd.Series([labels[int(code)] for code in codes], index=values.index)


def select_representative_pilot(
    inventory: pd.DataFrame,
    *,
    sample_size: int = 100,
    seed: int = 20260901,
) -> pd.DataFrame:
    """Select a deterministic, coverage-first stratified inventory sample."""
    eligible = inventory[inventory["eligible_for_processing"]].copy()
    if sample_size < 1 or sample_size > len(eligible):
        raise ValueError(
            f"Pilot size must be between 1 and {len(eligible):,}, found {sample_size}"
        )
    required = {
        "lake_id",
        "canonical_lake_id",
        "source_polygon_id",
        "latitude",
        "longitude",
        "elevation_m",
        "reference_area_km2",
    }
    missing = sorted(required - set(eligible.columns))
    if missing:
        raise ValueError("Pilot selection inventory is missing: " + ", ".join(missing))

    eligible["latitude_band"] = _tercile_band(
        eligible["latitude"], PILOT_BAND_LABELS["latitude_band"]
    )
    eligible["longitude_band"] = _tercile_band(
        eligible["longitude"], PILOT_BAND_LABELS["longitude_band"]
    )
    eligible["elevation_band"] = _tercile_band(
        eligible["elevation_m"], PILOT_BAND_LABELS["elevation_band"]
    )
    eligible["area_band"] = _tercile_band(
        np.log1p(pd.to_numeric(eligible["reference_area_km2"], errors="coerce")),
        PILOT_BAND_LABELS["area_band"],
    )
    band_columns = [
        "latitude_band",
        "longitude_band",
        "elevation_band",
        "area_band",
    ]
    eligible["pilot_stratum"] = eligible[band_columns].agg("|".join, axis=1)
    population = eligible.groupby("pilot_stratum", sort=True).size().astype(int)
    if len(population) > sample_size:
        raise ValueError(
            f"Pilot size {sample_size} cannot cover all {len(population)} populated strata"
        )

    allocations = {stratum: 1 for stratum in population.index}
    remaining = sample_size - len(allocations)
    while remaining:
        available = [
            stratum
            for stratum, count in population.items()
            if allocations[stratum] < int(count)
        ]
        if not available:
            raise AssertionError("Pilot allocation exhausted before reaching sample size")
        chosen = max(
            available,
            key=lambda stratum: (
                sample_size * int(population[stratum]) / len(eligible)
                - allocations[stratum],
                int(population[stratum]),
                stratum,
            ),
        )
        allocations[chosen] += 1
        remaining -= 1

    selected_groups: list[pd.DataFrame] = []
    for stratum, allocation in sorted(allocations.items()):
        group = eligible[eligible["pilot_stratum"] == stratum].copy()
        group["_selection_hash"] = group["canonical_lake_id"].map(
            lambda lake_id: hashlib.sha256(
                f"pilot-selection-v1|{seed}|{lake_id}".encode("utf-8")
            ).hexdigest()
        )
        selected_groups.append(group.sort_values("_selection_hash").head(allocation))
    selected = pd.concat(selected_groups, ignore_index=True)
    selected["stratum_population"] = selected["pilot_stratum"].map(population)
    selected["stratum_pilot_count"] = selected["pilot_stratum"].map(allocations)
    selected["expansion_weight"] = (
        selected["stratum_population"] / selected["stratum_pilot_count"]
    )
    selected["selection_seed"] = seed
    selected["_pilot_order_hash"] = selected["canonical_lake_id"].map(
        lambda lake_id: hashlib.sha256(
            f"pilot-order-v1|{seed}|{lake_id}".encode("utf-8")
        ).hexdigest()
    )
    selected = selected.sort_values("_pilot_order_hash").reset_index(drop=True)
    selected.insert(0, "pilot_order", np.arange(1, len(selected) + 1))
    selected["lake_id"] = selected["canonical_lake_id"].astype(str)
    output_columns = [
        "pilot_order",
        "lake_id",
        "source_polygon_id",
        "latitude",
        "longitude",
        "elevation_m",
        "reference_area_km2",
        *band_columns,
        "pilot_stratum",
        "stratum_population",
        "stratum_pilot_count",
        "expansion_weight",
        "selection_seed",
    ]
    result = selected[output_columns].copy()
    if len(result) != sample_size or result["lake_id"].nunique() != sample_size:
        raise AssertionError("Representative pilot selection is not unique and complete")
    return result


def update_representative_pilot_usage(
    output_dir: Path,
    selection: pd.DataFrame,
    session_result: dict[str, object],
    *,
    artifact_stem: str = "representative_100_pilot",
) -> dict[str, object]:
    """Accumulate usage across resumed pilot invocations."""
    lake_ids = selection.sort_values("pilot_order")["lake_id"].astype(str).tolist()
    selection_signature = hashlib.sha256("\n".join(lake_ids).encode("utf-8")).hexdigest()
    path = output_dir / f"{artifact_stem}_usage.json"
    if path.exists():
        cumulative = json.loads(path.read_text(encoding="utf-8"))
        if cumulative.get("selection_signature") != selection_signature:
            raise ValueError("Existing pilot usage file belongs to a different selection")
    else:
        cumulative = {
            "selection_signature": selection_signature,
            "sample_size": len(selection),
            "sessions": 0,
            "processed_lake_attempts": 0,
            "elapsed_seconds": 0.0,
            "sentinel_scene_lookup_calls": 0,
            "sentinel_scene_measurement_calls": 0,
            "nasa_power_http_requests": 0,
            "gsmap_earth_engine_summary_calls": 0,
            "gfs_earth_engine_summary_calls": 0,
        }
    cumulative["sessions"] = int(cumulative["sessions"]) + 1
    for target, source in (
        ("processed_lake_attempts", "processed_in_session"),
        ("sentinel_scene_lookup_calls", "sentinel_scene_lookup_calls"),
        ("sentinel_scene_measurement_calls", "sentinel_scene_measurement_calls"),
        ("nasa_power_http_requests", "nasa_power_http_requests"),
        (
            "gsmap_earth_engine_summary_calls",
            "gsmap_earth_engine_summary_calls",
        ),
        ("gfs_earth_engine_summary_calls", "gfs_earth_engine_summary_calls"),
    ):
        cumulative[target] = int(cumulative.get(target, 0)) + int(
            session_result.get(source, 0)
        )
    cumulative["elapsed_seconds"] = round(
        float(cumulative["elapsed_seconds"])
        + float(session_result.get("elapsed_seconds", 0.0)),
        3,
    )
    cumulative["updated_at"] = datetime.now(UTC).isoformat()
    _atomic_json(path, cumulative)
    return cumulative


def write_representative_pilot_report(
    *,
    output_dir: Path,
    selection: pd.DataFrame,
    statuses: pd.DataFrame,
    source_summary: dict[str, object],
    usage: dict[str, object],
    errors: pd.DataFrame,
    exact_command: str,
) -> dict[str, object]:
    """Write pilot-only outcomes and stratified full-run projections."""
    overlapping_status_columns = [
        column
        for column in statuses.columns
        if column != "lake_id" and column in selection.columns
    ]
    selected_status = selection.merge(
        statuses.drop(columns=overlapping_status_columns),
        on="lake_id",
        how="left",
        validate="1:1",
    )
    if selected_status["processing_status"].isna().any():
        raise ValueError("One or more selected pilot lakes have no checkpoint status")
    fresh_real = (
        (selected_status["processing_status"] == "SUCCESS")
        & (selected_status["source_mode"] == "REAL")
    )
    stale_real = (
        (selected_status["processing_status"] == "STALE")
        & (selected_status["source_mode"] == "REAL")
    )
    real_usable = fresh_real | stale_real
    selected_status["pilot_real_usable"] = real_usable
    selected_status["pilot_fresh_real"] = fresh_real
    pending = int((selected_status["processing_status"] == "PENDING").sum())
    failed = int((selected_status["processing_status"] == "FAILED").sum())
    no_imagery = int(
        (selected_status["processing_status"] == "NO_USABLE_IMAGERY").sum()
    )
    mock_count = int((selected_status["source_mode"] == "MOCK").sum())

    weighted_real = 0.0
    weighted_fresh = 0.0
    population_total = 0
    for _, group in selected_status.groupby("pilot_stratum"):
        population_count = int(group["stratum_population"].iloc[0])
        population_total += population_count
        weighted_real += population_count * float(group["pilot_real_usable"].mean())
        weighted_fresh += population_count * float(group["pilot_fresh_real"].mean())
    weighted_real_rate = 100 * weighted_real / population_total
    weighted_fresh_rate = 100 * weighted_fresh / population_total

    attempted = int(usage["processed_lake_attempts"])
    elapsed = float(usage["elapsed_seconds"])
    seconds_per_attempt = elapsed / attempted if attempted else None
    eligible = int(source_summary["processed_unique_records"])
    estimated_full_seconds = (
        seconds_per_attempt * eligible if seconds_per_attempt is not None else None
    )
    estimates: dict[str, float | int | None] = {}
    for key in (
        "sentinel_scene_lookup_calls",
        "sentinel_scene_measurement_calls",
        "nasa_power_http_requests",
    ):
        estimates[f"estimated_full_{key}"] = (
            round(int(usage[key]) * eligible / attempted)
            if attempted
            else None
        )

    error_columns = [
        "event_id",
        "run_id",
        "lake_id",
        "source",
        "attempt_number",
        "occurred_at",
        "error_type",
        "error_message",
    ]
    if errors.empty:
        errors = pd.DataFrame(columns=error_columns)
    selected_ids = set(selection["lake_id"].astype(str))
    pilot_errors = (
        errors[errors["lake_id"].astype(str).isin(selected_ids)].copy()
        if not errors.empty
        else errors.copy()
    )
    error_text = " ".join(
        pilot_errors.get("error_message", pd.Series(dtype=str)).fillna("").astype(str)
    ).lower()
    quota_keywords = ("quota", "rate limit", "too many requests", "billing", "429")
    quota_problem = any(keyword in error_text for keyword in quota_keywords)
    charge_statement = (
        "A quota/rate-limit/billing-related provider error was recorded; review the "
        "pilot error log and Google Cloud console before expansion."
        if quota_problem
        else "No quota, rate-limit, or billing error was returned by either provider. "
        "Actual account charges are not visible to this local process and must be "
        "confirmed in the Google Cloud Billing console."
    )

    summary: dict[str, object] = {
        "report_state": "COMPLETE" if pending == 0 else "INTERIM",
        "sample_size": int(len(selection)),
        "populated_strata_covered": int(selection["pilot_stratum"].nunique()),
        "fresh_real_observations": int(fresh_real.sum()),
        "stale_real_observations": int(stale_real.sum()),
        "no_usable_imagery": no_imagery,
        "provider_failures": failed,
        "mock_count": mock_count,
        "pending": pending,
        "runtime_seconds": round(elapsed, 3),
        "runtime_hours": round(elapsed / 3600, 3),
        "processed_lake_attempts": attempted,
        "sentinel_scene_lookup_calls": int(usage["sentinel_scene_lookup_calls"]),
        "sentinel_scene_measurement_calls": int(
            usage["sentinel_scene_measurement_calls"]
        ),
        "nasa_power_http_requests": int(usage["nasa_power_http_requests"]),
        "estimated_full_run_real_usable_rate_percent": round(weighted_real_rate, 2),
        "estimated_full_run_fresh_rate_percent": round(weighted_fresh_rate, 2),
        "estimated_full_run_duration_hours": (
            round(estimated_full_seconds / 3600, 2)
            if estimated_full_seconds is not None
            else None
        ),
        **estimates,
        "quota_problem_observed": quota_problem,
        "charge_or_quota_statement": charge_statement,
        "exact_command_used": exact_command,
    }
    _atomic_csv(
        output_dir / "representative_100_pilot_selection.csv", selection
    )
    _atomic_csv(
        output_dir / "representative_100_pilot_status.csv", selected_status
    )
    _atomic_csv(
        output_dir / "representative_100_pilot_error_log.csv", pilot_errors
    )
    _atomic_json(output_dir / "representative_100_pilot_summary.json", summary)

    report = f"""# Representative 100-Lake REAL-Data Pilot Report

Report state: **{summary['report_state']}**  
Exact command: `{exact_command}`

## Selection design

The deterministic sample contains {len(selection):,} unique lakes and covers {summary['populated_strata_covered']:,} populated strata formed from three latitude bands, three longitude bands, three elevation bands, and three log-area bands. Every populated stratum receives at least one lake; remaining positions are allocated in proportion to stratum population. The stratified full-run estimates use the recorded expansion weights.

Selected latitude range: {selection['latitude'].min():.5f} to {selection['latitude'].max():.5f}  
Selected longitude range: {selection['longitude'].min():.5f} to {selection['longitude'].max():.5f}  
Selected elevation range: {selection['elevation_m'].min():.1f} to {selection['elevation_m'].max():.1f} m  
Selected reference-area range: {selection['reference_area_km2'].min():.8f} to {selection['reference_area_km2'].max():.8f} km2

## Outcomes

| Outcome | Lakes |
|---|---:|
| Fresh REAL observations | {summary['fresh_real_observations']:,} |
| Stale REAL observations | {summary['stale_real_observations']:,} |
| No usable imagery | {no_imagery:,} |
| Provider failures | {failed:,} |
| MOCK observations | {mock_count:,} |
| Pending | {pending:,} |

## Runtime and measured API usage

- Runtime across resumed pilot sessions: {summary['runtime_seconds']:.3f} seconds ({summary['runtime_hours']:.3f} hours)
- Lakes attempted during pilot sessions: {attempted:,}
- Sentinel scene lookups: {summary['sentinel_scene_lookup_calls']:,}
- Sentinel scene measurements: {summary['sentinel_scene_measurement_calls']:,}
- NASA POWER HTTP requests: {summary['nasa_power_http_requests']:,}

## Stratified full-run estimates

- Estimated REAL-usable rate (fresh plus stale): **{summary['estimated_full_run_real_usable_rate_percent']:.2f}%**
- Estimated fresh-REAL rate: **{summary['estimated_full_run_fresh_rate_percent']:.2f}%**
- Estimated duration for {eligible:,} unique lakes: **{summary['estimated_full_run_duration_hours']} hours**
- Estimated Sentinel lookups: {summary['estimated_full_sentinel_scene_lookup_calls']}
- Estimated Sentinel scene measurements: {summary['estimated_full_sentinel_scene_measurement_calls']}
- Estimated NASA POWER requests: {summary['estimated_full_nasa_power_http_requests']}

These are sample-based operational estimates, not guarantees. Weather and image availability can vary geographically and over time.

## Charges and quotas

{charge_statement}

## Safety and interpretation

`environmental_conditions_score` is not a validated GLOF probability and does not predict that a flood will occur. No mock data is used as a fallback for failed REAL requests.

> {SAFETY_NOTICE}
"""
    report_path = output_dir / "REPRESENTATIVE_100_PILOT_REPORT.md"
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(report, encoding="utf-8")
    temporary.replace(report_path)
    return summary


def write_corrected_representative_pilot_report(
    *,
    output_dir: Path,
    selection: pd.DataFrame,
    statuses: pd.DataFrame,
    source_summary: dict[str, object],
    usage: dict[str, object],
    errors: pd.DataFrame,
    exact_command: str,
    previous_pilot_status: pd.DataFrame | None = None,
) -> dict[str, object]:
    """Report corrected source-specific freshness without a combined stale flag."""
    overlap = [
        column
        for column in statuses.columns
        if column != "lake_id" and column in selection.columns
    ]
    selected = selection.merge(
        statuses.drop(columns=overlap), on="lake_id", how="left", validate="1:1"
    )
    if selected["processing_status"].isna().any():
        raise ValueError("Corrected pilot has selected lakes without checkpoint status")
    pending = int((selected["processing_status"] == "PENDING").sum())
    mock_count = int((selected["source_mode"] == "MOCK").sum())
    provider_failures = int((selected["processing_status"] == "FAILED").sum())

    def counts(column: str) -> dict[str, int]:
        return {
            str(key): int(value)
            for key, value in selected[column].fillna("UNAVAILABLE").value_counts().items()
        }

    satellite_counts = counts("satellite_area_freshness")
    observed_counts = counts("observed_weather_freshness")
    observed_quality_counts = counts("observed_weather_data_quality")
    forecast_counts = counts("forecast_freshness")
    forecast_quality_counts = counts("forecast_data_quality")
    baseline_counts = counts("historical_baseline_availability")
    diagnostic_counts = counts("sentinel_diagnostic_reason")
    unavailable_satellite = selected["satellite_area_freshness"].eq("UNAVAILABLE")
    geometry_valid = selected["source_geometry_valid"].astype(str).str.lower().eq("true")
    raw_scenes = pd.to_numeric(selected["sentinel_raw_scene_count"], errors="coerce").fillna(0)
    metadata_clear = pd.to_numeric(
        selected["sentinel_metadata_clear_scene_count"], errors="coerce"
    ).fillna(0)
    cloud_score_matches = pd.to_numeric(
        selected["sentinel_cloud_score_match_count"], errors="coerce"
    ).fillna(0)
    cause_tests = {
        "invalid_geometry": int((unavailable_satellite & ~geometry_valid).sum()),
        "no_scene_coverage": int((unavailable_satellite & raw_scenes.eq(0)).sum()),
        "all_scenes_failed_metadata_cloud_filter": int(
            (unavailable_satellite & raw_scenes.gt(0) & metadata_clear.eq(0)).sum()
        ),
        "cloud_score_join_unavailable": int(
            (
                unavailable_satellite
                & metadata_clear.gt(0)
                & cloud_score_matches.eq(0)
            ).sum()
        ),
        "pixel_cloud_or_shadow_filtering": int(
            (
                unavailable_satellite
                & selected["sentinel_diagnostic_reason"].eq(
                    "PIXEL_CLOUD_OR_SHADOW_FILTERING"
                )
            ).sum()
        ),
        "recovered_only_by_extended_date_window": int(
            selected["sentinel_diagnostic_reason"].eq(
                "USABLE_RECOVERED_FROM_EXTENDED_WINDOW"
            ).sum()
        ),
    }
    fully_available = selected["processing_status"] == "AVAILABLE"
    selected["pilot_fully_available"] = fully_available

    weighted_available = 0.0
    population_total = 0
    for _, group in selected.groupby("pilot_stratum"):
        population = int(group["stratum_population"].iloc[0])
        population_total += population
        weighted_available += population * float(group["pilot_fully_available"].mean())
    weighted_available_rate = 100 * weighted_available / population_total

    previous_no_imagery: set[str] = set()
    if previous_pilot_status is not None and not previous_pilot_status.empty:
        previous_no_imagery = set(
            previous_pilot_status.loc[
                previous_pilot_status["processing_status"] == "NO_USABLE_IMAGERY",
                "lake_id",
            ].astype(str)
        )
    previous_in_selection = selected["lake_id"].astype(str).isin(previous_no_imagery)
    recovered = int(
        (
            previous_in_selection
            & (selected["satellite_area_freshness"] != "UNAVAILABLE")
        ).sum()
    )
    still_unavailable = int(
        (
            previous_in_selection
            & (selected["satellite_area_freshness"] == "UNAVAILABLE")
        ).sum()
    )

    attempted = int(usage["processed_lake_attempts"])
    elapsed = float(usage["elapsed_seconds"])
    eligible = int(source_summary["processed_unique_records"])
    estimated_hours = elapsed * eligible / attempted / 3600 if attempted else None
    api_keys = (
        "sentinel_scene_lookup_calls",
        "sentinel_scene_measurement_calls",
        "gsmap_earth_engine_summary_calls",
        "gfs_earth_engine_summary_calls",
        "nasa_power_http_requests",
    )
    estimated_calls = {
        f"estimated_full_{key}": (
            round(int(usage.get(key, 0)) * eligible / attempted)
            if attempted
            else None
        )
        for key in api_keys
    }

    error_columns = [
        "event_id",
        "run_id",
        "lake_id",
        "source",
        "attempt_number",
        "occurred_at",
        "error_type",
        "error_message",
    ]
    if errors.empty:
        errors = pd.DataFrame(columns=error_columns)
    selected_ids = set(selection["lake_id"].astype(str))
    pilot_errors = errors[errors["lake_id"].astype(str).isin(selected_ids)].copy()
    error_text = " ".join(
        pilot_errors["error_message"].fillna("").astype(str)
    ).lower()
    quota_problem = any(
        keyword in error_text
        for keyword in ("quota", "rate limit", "too many requests", "billing", "429")
    )
    charge_statement = (
        "A quota/rate-limit/billing-related provider error was recorded; inspect the "
        "error log and Google Cloud console before expansion."
        if quota_problem
        else "No quota, rate-limit, or billing error was returned. Actual account "
        "charges are not visible locally and must be checked in Google Cloud Billing."
    )
    summary: dict[str, object] = {
        "report_state": "COMPLETE" if pending == 0 else "INTERIM",
        "sample_size": int(len(selection)),
        "populated_strata_covered": int(selection["pilot_stratum"].nunique()),
        "processing_status": counts("processing_status"),
        "satellite_area_freshness": satellite_counts,
        "observed_weather_freshness": observed_counts,
        "observed_weather_data_quality": observed_quality_counts,
        "forecast_freshness": forecast_counts,
        "forecast_data_quality": forecast_quality_counts,
        "historical_baseline_availability": baseline_counts,
        "sentinel_diagnostic_reasons": diagnostic_counts,
        "sentinel_cause_tests": cause_tests,
        "previous_no_imagery_lakes_in_selection": int(previous_in_selection.sum()),
        "previous_no_imagery_recovered": recovered,
        "previous_no_imagery_still_unavailable": still_unavailable,
        "provider_failures": provider_failures,
        "mock_count": mock_count,
        "pending": pending,
        "runtime_seconds": round(elapsed, 3),
        "runtime_hours": round(elapsed / 3600, 3),
        "processed_lake_attempts": attempted,
        **{key: int(usage.get(key, 0)) for key in api_keys},
        "estimated_full_run_all_critical_sources_available_percent": round(
            weighted_available_rate, 2
        ),
        "estimated_full_run_duration_hours": (
            round(estimated_hours, 2) if estimated_hours is not None else None
        ),
        **estimated_calls,
        "quota_problem_observed": quota_problem,
        "charge_or_quota_statement": charge_statement,
        "exact_command_used": exact_command,
    }
    stem = "corrected_representative_100_pilot"
    _atomic_csv(output_dir / f"{stem}_selection.csv", selection)
    _atomic_csv(output_dir / f"{stem}_status.csv", selected)
    _atomic_csv(output_dir / f"{stem}_error_log.csv", pilot_errors)
    _atomic_json(output_dir / f"{stem}_summary.json", summary)

    diagnostic_lines = "\n".join(
        f"- {reason}: {count:,}"
        for reason, count in diagnostic_counts.items()
    )
    report = f"""# Corrected Representative 100-Lake REAL-Data Pilot Report

Report state: **{summary['report_state']}**  
Exact command: `{exact_command}`

## Source-specific availability and freshness

| Source assessment | Counts |
|---|---|
| Satellite-area freshness | {satellite_counts} |
| GSMaP observed-weather freshness | {observed_counts} |
| GSMaP observed-weather data quality | {observed_quality_counts} |
| GFS forecast freshness | {forecast_counts} |
| GFS forecast data quality | {forecast_quality_counts} |
| NASA POWER historical-baseline availability | {baseline_counts} |
| Overall availability (not freshness) | {summary['processing_status']} |

NASA POWER publication delay is not used in any live freshness calculation. The 20-day satellite-area, 36-hour observed-weather, and 12-hour forecast thresholds are unchanged and evaluated independently.

## Sentinel investigation

The corrected search uses the latest reliable observation found within the configured 180-day window, retains its actual age, pre-filters metadata clouds that could never pass the configured threshold, verifies Cloud Score joins by shared `system:index`, and records scene/candidate counts for each lake.

{diagnostic_lines}

Explicit cause tests among unavailable Sentinel records:

- Invalid source geometry: {cause_tests['invalid_geometry']:,}
- No Sentinel scene coverage: {cause_tests['no_scene_coverage']:,}
- All scenes removed by metadata cloud filtering: {cause_tests['all_scenes_failed_metadata_cloud_filter']:,}
- Cloud Score join unavailable: {cause_tests['cloud_score_join_unavailable']:,}
- Pixel cloud/shadow filtering prevented a reliable area: {cause_tests['pixel_cloud_or_shadow_filtering']:,}
- Valid areas recovered only by the extended date window: {cause_tests['recovered_only_by_extended_date_window']:,}

- Previously unavailable pilot lakes represented here: {int(previous_in_selection.sum()):,}
- Recovered with a valid satellite area: {recovered:,}
- Still unavailable: {still_unavailable:,}

## Runtime and API usage

- Runtime: {summary['runtime_seconds']:.3f} seconds ({summary['runtime_hours']:.3f} hours)
- Sentinel lookups: {summary['sentinel_scene_lookup_calls']:,}
- Sentinel measurements: {summary['sentinel_scene_measurement_calls']:,}
- GSMaP Earth Engine summaries: {summary['gsmap_earth_engine_summary_calls']:,}
- GFS Earth Engine summaries: {summary['gfs_earth_engine_summary_calls']:,}
- NASA POWER baseline requests: {summary['nasa_power_http_requests']:,}
- Provider failures: {provider_failures:,}
- Mock count: {mock_count:,}

## Full-run operational estimate

- Stratified estimate with all three critical live sources available: **{summary['estimated_full_run_all_critical_sources_available_percent']:.2f}%**
- Estimated duration for {eligible:,} unique lakes: **{summary['estimated_full_run_duration_hours']} hours**
- Estimated Sentinel lookups: {summary['estimated_full_sentinel_scene_lookup_calls']}
- Estimated Sentinel measurements: {summary['estimated_full_sentinel_scene_measurement_calls']}
- Estimated GSMaP summaries: {summary['estimated_full_gsmap_earth_engine_summary_calls']}
- Estimated GFS summaries: {summary['estimated_full_gfs_earth_engine_summary_calls']}
- Estimated NASA POWER requests: {summary['estimated_full_nasa_power_http_requests']}

These are sample-based operational estimates, not guarantees.

## Charges and quotas

{charge_statement}

## Safety

`environmental_conditions_score` is a transparent conditions index, not a validated GLOF probability. No mock data is substituted after REAL-source failures.

> {SAFETY_NOTICE}
"""
    path = output_dir / "CORRECTED_REPRESENTATIVE_100_PILOT_REPORT.md"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(report, encoding="utf-8")
    temporary.replace(path)
    return summary
