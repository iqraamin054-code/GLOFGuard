"""Deterministic mock providers for interface and pipeline tests only."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta

from ..types import (
    DailyWeather,
    ForecastPoint,
    HourlyPrecipitation,
    Lake,
    QualityStatus,
    SatelliteObservation,
)


MOCK_SOURCE_WARNING = "MOCK DATA - NOT A REAL ENVIRONMENTAL OBSERVATION"


class MockSentinel2Provider:
    def latest(self, lake: Lake, start: date, end: date) -> SatelliteObservation:
        reference = lake.reference_area_km2 or 0.15
        return SatelliteObservation(
            lake_id=lake.lake_id,
            observed_at=datetime.combine(end - timedelta(days=1), time(10), tzinfo=UTC),
            area_km2=round(reference * 1.04, 6),
            cloud_percentage=8.0,
            clear_fraction=0.92,
            quality_status=QualityStatus.MOCK,
            source_image_id=f"MOCK_S2_{lake.lake_id}_{end.isoformat()}",
            water_index="MNDWI",
            source=MOCK_SOURCE_WARNING,
            is_mock=True,
        )


class MockGsmapProvider:
    def hourly(self, lake: Lake, start: datetime, end: datetime) -> list[HourlyPrecipitation]:
        rows: list[HourlyPrecipitation] = []
        cursor = start.replace(minute=0, second=0, microsecond=0)
        while cursor <= end:
            # Deterministic periodic test data, not a physical simulation.
            value = max(0.0, 1.2 * math.sin(cursor.timestamp() / 43_200.0))
            rows.append(
                HourlyPrecipitation(
                    lake_id=lake.lake_id,
                    observed_at=cursor,
                    rainfall_mm=round(value, 4),
                    source=MOCK_SOURCE_WARNING,
                    is_mock=True,
                )
            )
            cursor += timedelta(hours=1)
        return rows


class MockGfsProvider:
    def forecast(self, lake: Lake, created_after: datetime) -> list[ForecastPoint]:
        created = created_after.replace(minute=0, second=0, microsecond=0)
        return [
            ForecastPoint(
                lake_id=lake.lake_id,
                forecast_created_at=created,
                valid_at=created + timedelta(hours=hour),
                precipitation_mm=0.4 if hour <= 72 else 0.1,
                temperature_c=5.0 + math.sin(hour / 24.0),
                horizon_hours=hour,
                source=MOCK_SOURCE_WARNING,
                is_mock=True,
            )
            for hour in range(3, 169, 3)
        ]


class MockPowerProvider:
    def daily(self, lake: Lake, start: date, end: date) -> list[DailyWeather]:
        rows: list[DailyWeather] = []
        cursor = start
        while cursor <= end:
            rows.append(
                DailyWeather(
                    lake_id=lake.lake_id,
                    observation_date=cursor,
                    temperature_c=4.0 + math.sin(cursor.toordinal() / 20.0),
                    rainfall_mm=1.0,
                    source=MOCK_SOURCE_WARNING,
                    is_mock=True,
                )
            )
            cursor += timedelta(days=1)
        return rows

