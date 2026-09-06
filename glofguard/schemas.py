"""Canonical one-lake-per-observation-date time-series schema."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


SAFETY_NOTICE = (
    "This research prototype provides environmental risk indicators and is not "
    "an official emergency-warning system. High-risk results require expert verification."
)

REQUIRED_TIME_SERIES_COLUMNS = [
    "lake_id",
    "observation_date",
    "latitude",
    "longitude",
    "area_current_km2",
    "area_change_30d",
    "area_change_90d",
    "area_change_365d",
    "temperature_current",
    "temperature_7d_average",
    "temperature_anomaly",
    "rainfall_last_24h",
    "rainfall_last_7d",
    "rainfall_last_30d",
    "forecast_rainfall_next_72h",
    "forecast_temperature_next_7d",
    "elevation",
    "distance_to_nearest_settlement_km",
    "satellite_observation_date",
    "weather_observation_date",
    "data_quality_status",
    "risk_score",
    "risk_level",
    "prediction_timestamp",
    "model_version",
]

ADDITIONAL_AUDIT_COLUMNS = [
    "rainfall_anomaly",
    "baseline_susceptibility_score",
    "baseline_susceptibility_level",
    "confidence_level",
    "uncertainty_score",
    "most_influential_factors",
    "data_quality_warning",
    "satellite_cloud_percentage",
    "forecast_creation_time",
    "forecast_horizon_hours",
    "satellite_freshness_status",
    "observed_weather_freshness_status",
    "forecast_freshness_status",
    "historical_baseline_status",
    "historical_baseline_observation_date",
    "input_signature",
    "source_mode",
    "safety_notice",
]

TIME_SERIES_COLUMNS = REQUIRED_TIME_SERIES_COLUMNS + ADDITIONAL_AUDIT_COLUMNS

# Verified outcome targets are intentionally absent. Add them only after a
# separately sourced event table provides authoritative historical event dates.


@dataclass
class TimeSeriesRecord:
    lake_id: str
    observation_date: date
    latitude: float
    longitude: float
    area_current_km2: float | None = None
    area_change_30d: float | None = None
    area_change_90d: float | None = None
    area_change_365d: float | None = None
    temperature_current: float | None = None
    temperature_7d_average: float | None = None
    temperature_anomaly: float | None = None
    rainfall_last_24h: float | None = None
    rainfall_last_7d: float | None = None
    rainfall_last_30d: float | None = None
    forecast_rainfall_next_72h: float | None = None
    forecast_temperature_next_7d: float | None = None
    elevation: float | None = None
    distance_to_nearest_settlement_km: float | None = None
    satellite_observation_date: datetime | None = None
    weather_observation_date: datetime | None = None
    data_quality_status: str = "MISSING"
    risk_score: float | None = None
    risk_level: str | None = None
    prediction_timestamp: datetime | None = None
    model_version: str = ""
    rainfall_anomaly: float | None = None
    baseline_susceptibility_score: float | None = None
    baseline_susceptibility_level: str | None = None
    confidence_level: str = "Low"
    uncertainty_score: float = 1.0
    most_influential_factors: list[dict[str, Any]] = field(default_factory=list)
    data_quality_warning: str | None = None
    satellite_cloud_percentage: float | None = None
    forecast_creation_time: datetime | None = None
    forecast_horizon_hours: int | None = None
    satellite_freshness_status: str = "MISSING"
    observed_weather_freshness_status: str = "MISSING"
    forecast_freshness_status: str = "MISSING"
    historical_baseline_status: str = "UNAVAILABLE"
    historical_baseline_observation_date: date | None = None
    input_signature: str = ""
    source_mode: str = "REAL"
    safety_notice: str = SAFETY_NOTICE

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        for key, value in tuple(values.items()):
            if isinstance(value, (date, datetime)):
                values[key] = value.isoformat()
        values["most_influential_factors"] = json.dumps(
            values["most_influential_factors"], separators=(",", ":")
        )
        return {column: values[column] for column in TIME_SERIES_COLUMNS}
