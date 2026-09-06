"""Plausibility, unit, coordinate, and timestamp validation."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Iterable

from .types import DailyWeather, ForecastPoint, HourlyPrecipitation, Lake, SatelliteObservation


class DataValidationError(ValueError):
    pass


def _finite(name: str, value: float | None, *, allow_none: bool = True) -> None:
    if value is None and allow_none:
        return
    if value is None or not math.isfinite(float(value)):
        raise DataValidationError(f"{name} must be finite")


def _range(name: str, value: float | None, minimum: float, maximum: float) -> None:
    if value is None:
        return
    _finite(name, value)
    if not minimum <= float(value) <= maximum:
        raise DataValidationError(
            f"{name}={value} is outside the plausible range [{minimum}, {maximum}]"
        )


def validate_lake(lake: Lake) -> None:
    if not lake.lake_id.strip():
        raise DataValidationError("lake_id must not be blank")
    _range("latitude", lake.latitude, -90.0, 90.0)
    _range("longitude", lake.longitude, -180.0, 180.0)
    _range("elevation_m", lake.elevation_m, -500.0, 9000.0)
    _range(
        "distance_to_nearest_settlement_km",
        lake.distance_to_nearest_settlement_km,
        0.0,
        2000.0,
    )
    _range("reference_area_km2", lake.reference_area_km2, 0.0, 10_000.0)


def validate_satellite(observation: SatelliteObservation, *, now: datetime) -> None:
    if observation.observed_at > now + timedelta(hours=24):
        raise DataValidationError("satellite observation time is implausibly in the future")
    _range("satellite area_km2", observation.area_km2, 0.0, 10_000.0)
    _range("satellite cloud_percentage", observation.cloud_percentage, 0.0, 100.0)
    _range("satellite clear_fraction", observation.clear_fraction, 0.0, 1.0)
    if observation.is_mock and "MOCK" not in observation.source.upper():
        raise DataValidationError("mock satellite data must be explicitly marked in its source")


def validate_precipitation(
    observations: Iterable[HourlyPrecipitation], *, now: datetime
) -> None:
    for observation in observations:
        if observation.observed_at > now + timedelta(hours=2):
            raise DataValidationError("hourly rainfall timestamp is in the future")
        _range("hourly rainfall_mm", observation.rainfall_mm, 0.0, 500.0)
        if observation.is_mock and "MOCK" not in observation.source.upper():
            raise DataValidationError("mock rainfall data must be explicitly marked")


def validate_daily_weather(observations: Iterable[DailyWeather], *, today: date) -> None:
    for observation in observations:
        if observation.observation_date > today:
            raise DataValidationError("historical weather date is in the future")
        _range("daily temperature_c", observation.temperature_c, -90.0, 65.0)
        _range("daily rainfall_mm", observation.rainfall_mm, 0.0, 2000.0)
        if observation.is_mock and "MOCK" not in observation.source.upper():
            raise DataValidationError("mock daily weather must be explicitly marked")


def validate_forecast(observations: Iterable[ForecastPoint], *, now: datetime) -> None:
    for observation in observations:
        if observation.forecast_created_at > now + timedelta(hours=2):
            raise DataValidationError("forecast creation time is in the future")
        if observation.valid_at < observation.forecast_created_at:
            raise DataValidationError("forecast valid time precedes its creation time")
        if observation.horizon_hours < 0 or observation.horizon_hours > 384:
            raise DataValidationError("forecast horizon must be between 0 and 384 hours")
        _range("forecast precipitation_mm", observation.precipitation_mm, 0.0, 1000.0)
        _range("forecast temperature_c", observation.temperature_c, -90.0, 65.0)
        if observation.is_mock and "MOCK" not in observation.source.upper():
            raise DataValidationError("mock forecast data must be explicitly marked")
