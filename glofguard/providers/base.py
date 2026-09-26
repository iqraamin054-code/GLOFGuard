"""Interfaces shared by real providers and mock test doubles."""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Sequence

from ..types import DailyWeather, ForecastPoint, HourlyPrecipitation, Lake, SatelliteObservation


class ProviderError(RuntimeError):
    """A source failed or returned an unusable response."""


class DisabledProvider:
    """Explicitly disabled source for a bounded partial-source smoke test."""

    def latest(self, lake: Lake, start: date, end: date) -> None:
        return None

    def hourly(self, lake: Lake, start: datetime, end: datetime) -> list[HourlyPrecipitation]:
        return []

    def forecast(self, lake: Lake, created_after: datetime) -> list[ForecastPoint]:
        return []

    def daily(self, lake: Lake, start: date, end: date) -> list[DailyWeather]:
        return []


class SatelliteProvider(Protocol):
    def latest(self, lake: Lake, start: date, end: date) -> SatelliteObservation | None: ...


class PrecipitationProvider(Protocol):
    def hourly(
        self, lake: Lake, start: datetime, end: datetime
    ) -> Sequence[HourlyPrecipitation]: ...


class ForecastProvider(Protocol):
    def forecast(self, lake: Lake, created_after: datetime) -> Sequence[ForecastPoint]: ...


class ClimateProvider(Protocol):
    def daily(self, lake: Lake, start: date, end: date) -> Sequence[DailyWeather]: ...
