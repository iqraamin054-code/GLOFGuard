"""Rolling feature engineering from dated observations."""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta
from typing import Sequence

from .types import DailyWeather, ForecastPoint, HourlyPrecipitation, SatelliteObservation


def area_change_percent(
    current: SatelliteObservation | None,
    history: Sequence[SatelliteObservation],
    days: int,
    tolerance_days: int | None = None,
) -> float | None:
    """Percent area change from the nearest reliable observation around a target date."""
    if current is None or current.area_km2 is None:
        return None
    tolerance = tolerance_days if tolerance_days is not None else max(7, int(days * 0.25))
    target = current.observed_at - timedelta(days=days)
    candidates = [
        row
        for row in history
        if row.area_km2 is not None
        and row.observed_at < current.observed_at
        and abs((row.observed_at - target).total_seconds())
        <= tolerance * 86_400
    ]
    if not candidates:
        return None
    previous = min(candidates, key=lambda row: abs(row.observed_at - target))
    if previous.area_km2 is None or previous.area_km2 <= 0:
        return None
    return 100.0 * (current.area_km2 - previous.area_km2) / previous.area_km2


def rainfall_accumulation(
    observations: Sequence[HourlyPrecipitation],
    as_of: datetime,
    hours: int,
    *,
    minimum_coverage: float = 0.80,
) -> float | None:
    start = as_of - timedelta(hours=hours)
    values_by_hour = {
        row.observed_at.replace(minute=0, second=0, microsecond=0): row.rainfall_mm
        for row in observations
        if start < row.observed_at <= as_of
    }
    if len(values_by_hour) < math.ceil(hours * minimum_coverage):
        return None
    # Hourly GSMaP rain rate in mm/hr integrated over each one-hour record.
    return float(sum(values_by_hour.values()))


def weather_features(
    observations: Sequence[DailyWeather], as_of: date
) -> tuple[float | None, float | None, float | None, float | None]:
    usable = [
        row for row in observations if row.observation_date <= as_of and row.temperature_c is not None
    ]
    if not usable:
        return None, None, None, None
    latest = max(usable, key=lambda row: row.observation_date)
    last_seven = [
        row.temperature_c
        for row in usable
        if as_of - timedelta(days=6) <= row.observation_date <= as_of
        and row.temperature_c is not None
    ]
    average_7d = statistics.fmean(last_seven) if last_seven else None
    # Long-term baseline uses the same calendar month on earlier years. It is
    # only calculated when at least three historical years are represented.
    baseline_rows = [
        row.temperature_c
        for row in usable
        if row.observation_date.year < as_of.year
        and row.observation_date.month == as_of.month
        and row.temperature_c is not None
    ]
    baseline_years = {
        row.observation_date.year
        for row in usable
        if row.observation_date.year < as_of.year
        and row.observation_date.month == as_of.month
        and row.temperature_c is not None
    }
    anomaly = None
    if len(baseline_years) >= 3 and baseline_rows:
        anomaly = float(latest.temperature_c) - statistics.fmean(baseline_rows)
    rainfall_anomaly = None
    rainfall_rows = [
        row
        for row in observations
        if row.rainfall_mm is not None
        and row.observation_date.month == as_of.month
        and row.observation_date.day <= as_of.day
    ]
    current_rainfall = sum(
        float(row.rainfall_mm)
        for row in rainfall_rows
        if row.observation_date.year == as_of.year
    )
    historical_totals: dict[int, float] = {}
    for row in rainfall_rows:
        if row.observation_date.year < as_of.year:
            historical_totals[row.observation_date.year] = (
                historical_totals.get(row.observation_date.year, 0.0)
                + float(row.rainfall_mm)
            )
    if len(historical_totals) >= 3:
        rainfall_anomaly = current_rainfall - statistics.fmean(
            historical_totals.values()
        )
    return float(latest.temperature_c), average_7d, anomaly, rainfall_anomaly


def forecast_features(
    observations: Sequence[ForecastPoint],
) -> tuple[float | None, float | None, datetime | None, int | None]:
    if not observations:
        return None, None, None, None
    newest_creation = max(row.forecast_created_at for row in observations)
    newest = [row for row in observations if row.forecast_created_at == newest_creation]
    rainfall_by_horizon = {
        row.horizon_hours: row.precipitation_mm
        for row in newest
        if row.horizon_hours <= 72 and row.precipitation_mm is not None
    }
    temperature_values = [
        row.temperature_c
        for row in newest
        if row.horizon_hours <= 168 and row.temperature_c is not None
    ]
    # In Earth Engine's GFS collection precipitation is cumulative for the
    # previous 1-6 hours. Six-hour multiples are the non-overlapping sequence;
    # summing intermediate assets would double-count rainfall.
    required_precipitation_hours = set(range(6, 73, 6))
    rainfall_72h = (
        float(
            sum(
                float(rainfall_by_horizon[hour])
                for hour in sorted(required_precipitation_hours)
            )
        )
        if required_precipitation_hours.issubset(rainfall_by_horizon)
        else None
    )
    temperature_7d = (
        statistics.fmean(temperature_values) if len(temperature_values) >= 45 else None
    )
    horizon = max((row.horizon_hours for row in newest), default=None)
    return rainfall_72h, temperature_7d, newest_creation, horizon
