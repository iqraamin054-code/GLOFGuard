"""Provider-neutral domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class QualityStatus(StrEnum):
    RELIABLE = "RELIABLE"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    REJECTED = "REJECTED"
    MISSING = "MISSING"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MOCK = "MOCK"


@dataclass(frozen=True)
class Lake:
    lake_id: str
    latitude: float
    longitude: float
    elevation_m: float | None = None
    distance_to_nearest_settlement_km: float | None = None
    reference_area_km2: float | None = None
    name: str | None = None


@dataclass(frozen=True)
class SatelliteObservation:
    lake_id: str
    observed_at: datetime
    area_km2: float | None
    cloud_percentage: float
    clear_fraction: float
    quality_status: QualityStatus
    source_image_id: str
    water_index: str
    source: str = "Sentinel-2 SR Harmonized / Google Earth Engine"
    is_mock: bool = False
    rejection_reason: str | None = None


@dataclass(frozen=True)
class HourlyPrecipitation:
    lake_id: str
    observed_at: datetime
    rainfall_mm: float
    source: str = "JAXA GSMaP Gauge NRT"
    is_mock: bool = False


@dataclass(frozen=True)
class DailyWeather:
    lake_id: str
    observation_date: date
    temperature_c: float | None
    rainfall_mm: float | None
    source: str = "NASA POWER"
    is_mock: bool = False


@dataclass(frozen=True)
class ForecastPoint:
    lake_id: str
    forecast_created_at: datetime
    valid_at: datetime
    precipitation_mm: float | None
    temperature_c: float | None
    horizon_hours: int
    source: str = "NOAA GFS 0.25 degree"
    is_mock: bool = False


@dataclass(frozen=True)
class ObservedRainfallSummary:
    lake_id: str
    observation_time: datetime
    rainfall_last_24h_mm: float | None
    rainfall_last_7d_mm: float | None
    rainfall_last_30d_mm: float | None
    available_hours_24h: int
    available_hours_7d: int
    available_hours_30d: int
    product_status: str | None
    source: str = "JAXA GSMaP V8 operational / Google Earth Engine"
    is_mock: bool = False


@dataclass(frozen=True)
class ForecastSummary:
    lake_id: str
    forecast_created_at: datetime
    forecast_rainfall_next_72h_mm: float | None
    forecast_temperature_next_24h_c: float | None
    forecast_temperature_next_7d_c: float | None
    precipitation_steps: int
    temperature_steps_24h: int
    temperature_steps_7d: int
    forecast_horizon_hours: int
    source: str = "NOAA GFS 0.25 degree / Google Earth Engine"
    is_mock: bool = False


@dataclass(frozen=True)
class IndicatorResult:
    baseline_susceptibility_score: float | None
    baseline_susceptibility_level: str | None
    risk_score: float | None
    risk_level: str | None
    confidence_level: str
    uncertainty_score: float
    influential_factors: list[dict[str, Any]] = field(default_factory=list)
    model_version: str = ""
    data_quality_warning: str | None = None
    model_available: bool = False
