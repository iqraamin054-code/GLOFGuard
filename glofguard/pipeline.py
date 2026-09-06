"""Daily append-safe refresh orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Callable

from .config import Settings
from .features import area_change_percent, forecast_features, rainfall_accumulation, weather_features
from .providers.base import ClimateProvider, ForecastProvider, PrecipitationProvider, SatelliteProvider
from .schemas import TimeSeriesRecord
from .scoring import (
    BaselineSusceptibilityIndex,
    DynamicEnvironmentalIndex,
    SavedValidatedModel,
    level_for_score,
)
from .storage import Repository
from .types import Lake, QualityStatus
from .validation import (
    validate_daily_weather,
    validate_forecast,
    validate_lake,
    validate_precipitation,
    validate_satellite,
)


@dataclass(frozen=True)
class RefreshSummary:
    lakes_processed: int = 0
    records_created: int = 0
    unchanged_records: int = 0
    failures: int = 0
    rejected_satellite_measurements: int = 0


class RefreshPipeline:
    """Refresh sources, engineer features, score changed inputs, and persist."""

    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        satellite: SatelliteProvider,
        precipitation: PrecipitationProvider,
        forecast: ForecastProvider,
        climate: ClimateProvider,
        *,
        now: Callable[[], datetime] | None = None,
        lake_ids: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.satellite = satellite
        self.precipitation = precipitation
        self.forecast = forecast
        self.climate = climate
        self.now = now or (lambda: datetime.now(UTC))
        self.lake_ids = lake_ids
        self.baseline_model: SavedValidatedModel | None = None
        self.dynamic_model: SavedValidatedModel | None = None
        if settings.model_validated:
            if settings.baseline_model_path or settings.baseline_preprocessing_path:
                if not settings.baseline_model_path or not settings.baseline_preprocessing_path:
                    raise ValueError(
                        "A validated baseline model requires both baseline artifact paths"
                    )
                self.baseline_model = SavedValidatedModel(
                    settings.baseline_preprocessing_path,
                    settings.baseline_model_path,
                    settings.model_version,
                )
            if settings.dynamic_model_path or settings.dynamic_preprocessing_path:
                if not settings.dynamic_model_path or not settings.dynamic_preprocessing_path:
                    raise ValueError(
                        "A validated dynamic model requires both dynamic artifact paths"
                    )
                self.dynamic_model = SavedValidatedModel(
                    settings.dynamic_preprocessing_path,
                    settings.dynamic_model_path,
                    settings.model_version,
                )

    def _attempt(
        self, lake: Lake, source: str, operation: Callable[[], object]
    ) -> tuple[bool, object | None]:
        try:
            return True, operation()
        except Exception as exc:
            # Last valid data remains untouched; only a failure record is added.
            self.repository.record_failure(lake.lake_id, source, exc)
            return False, None

    def _refresh_sources(self, lake: Lake, current: datetime) -> tuple[int, int]:
        failures = 0
        rejected = 0
        source_times = self.repository.latest_source_times(lake.lake_id)

        # Search farther back for the latest reliable area without changing the
        # independent freshness threshold or pretending an older scene is new.
        satellite_start = (
            current - timedelta(days=self.settings.sentinel_area_lookback_days)
        ).date()
        satellite_ok, satellite_result = self._attempt(
            lake,
            "Sentinel-2",
            lambda: self.satellite.latest(lake, satellite_start, current.date() + timedelta(days=1)),
        )
        if not satellite_ok:
            failures += 1
        elif satellite_result is not None:
            validate_satellite(satellite_result, now=current)
            self.repository.store_satellite(satellite_result)
            if satellite_result.quality_status == QualityStatus.REJECTED:
                rejected += 1

        precip_latest = source_times["precipitation"]
        precip_start = (
            max(precip_latest + timedelta(hours=1), current - timedelta(days=30))
            if isinstance(precip_latest, datetime)
            else current - timedelta(days=30)
        )
        precip_end = current - timedelta(hours=4)
        if precip_start <= precip_end:
            precipitation_ok, precipitation_result = self._attempt(
                lake,
                "JAXA GSMaP",
                lambda: self.precipitation.hourly(lake, precip_start, precip_end),
            )
            if not precipitation_ok:
                failures += 1
            elif precipitation_result is not None:
                validate_precipitation(precipitation_result, now=current)
                self.repository.store_hourly_precipitation(precipitation_result)

        weather_latest = source_times["weather"]
        weather_end = current.date() - timedelta(days=1)
        if isinstance(weather_latest, date):
            weather_start = weather_latest + timedelta(days=1)
        else:
            # First refresh builds a ten-year daily climate baseline. It is not
            # mislabeled as a daily forecasting target dataset.
            weather_start = weather_end - timedelta(days=3652)
        if weather_start <= weather_end:
            weather_ok, weather_result = self._attempt(
                lake,
                "NASA POWER",
                lambda: self.climate.daily(lake, weather_start, weather_end),
            )
            if not weather_ok:
                failures += 1
            elif weather_result is not None:
                validate_daily_weather(weather_result, today=current.date())
                self.repository.store_daily_weather(weather_result)

        forecast_after = source_times["forecast"]
        if not isinstance(forecast_after, datetime):
            forecast_after = current - timedelta(days=1)
        forecast_ok, forecast_result = self._attempt(
            lake,
            "NOAA GFS",
            lambda: self.forecast.forecast(lake, forecast_after),
        )
        if not forecast_ok:
            failures += 1
        elif forecast_result:
            validate_forecast(forecast_result, now=current)
            self.repository.store_forecasts(forecast_result)
        return failures, rejected

    def _build_record(
        self,
        lake: Lake,
        current: datetime,
        baseline_index: BaselineSusceptibilityIndex,
    ) -> TimeSeriesRecord:
        satellite_history = self.repository.satellite_history(lake.lake_id)
        satellite_current = satellite_history[-1] if satellite_history else None
        precipitation = self.repository.precipitation_since(
            lake.lake_id, current - timedelta(days=31)
        )
        daily = self.repository.daily_weather(lake.lake_id)
        forecast = self.repository.latest_forecast(lake.lake_id)
        (
            temperature_current,
            temperature_7d,
            temperature_anomaly,
            rainfall_anomaly,
        ) = weather_features(daily, current.date())
        forecast_rain, forecast_temperature, forecast_creation, forecast_horizon = (
            forecast_features(forecast)
        )
        weather_dates = [row.observed_at for row in precipitation]
        weather_update = max(weather_dates) if weather_dates else None
        rainfall_as_of = weather_update or current
        baseline_update = daily[-1].observation_date if daily else None

        warnings: list[str] = []
        freshness_penalty = 0.0
        satellite_freshness = "MISSING"
        observed_weather_freshness = "MISSING"
        forecast_freshness = "MISSING"
        if satellite_current is None:
            warnings.append("No reliable satellite observation")
            freshness_penalty += 0.35
        elif current - satellite_current.observed_at > timedelta(
            days=self.settings.satellite_stale_days
        ):
            satellite_freshness = "STALE"
            warnings.append("Satellite observation is stale")
            freshness_penalty += 0.20
        else:
            satellite_freshness = "FRESH"
        if weather_update is None:
            warnings.append("No observed weather update")
            freshness_penalty += 0.35
        elif current - weather_update > timedelta(hours=self.settings.weather_stale_hours):
            observed_weather_freshness = "STALE"
            warnings.append("Observed weather is stale")
            freshness_penalty += 0.20
        else:
            observed_weather_freshness = "FRESH"
        if forecast_creation is None:
            warnings.append("No forecast update")
            freshness_penalty += 0.20
        elif current - forecast_creation > timedelta(hours=self.settings.forecast_stale_hours):
            forecast_freshness = "STALE"
            warnings.append("Forecast is stale")
            freshness_penalty += 0.15
        else:
            forecast_freshness = "FRESH"
        if baseline_update is None:
            warnings.append("Historical climate baseline unavailable")

        source_mode = "MOCK" if any(
            row.is_mock
            for row in [*satellite_history, *precipitation, *daily, *forecast]
        ) else "REAL"
        status = "MOCK" if source_mode == "MOCK" else (
            "MISSING" if len(warnings) >= 3 else "STALE" if any("stale" in item.lower() for item in warnings) else "PARTIAL" if warnings else "RELIABLE"
        )
        record = TimeSeriesRecord(
            lake_id=lake.lake_id,
            observation_date=current.date(),
            latitude=lake.latitude,
            longitude=lake.longitude,
            area_current_km2=satellite_current.area_km2 if satellite_current else None,
            area_change_30d=area_change_percent(satellite_current, satellite_history, 30),
            area_change_90d=area_change_percent(satellite_current, satellite_history, 90),
            area_change_365d=area_change_percent(satellite_current, satellite_history, 365),
            temperature_current=temperature_current,
            temperature_7d_average=temperature_7d,
            temperature_anomaly=temperature_anomaly,
            rainfall_anomaly=rainfall_anomaly,
            rainfall_last_24h=rainfall_accumulation(precipitation, rainfall_as_of, 24),
            rainfall_last_7d=rainfall_accumulation(precipitation, rainfall_as_of, 24 * 7),
            rainfall_last_30d=rainfall_accumulation(
                precipitation, rainfall_as_of, 24 * 30
            ),
            forecast_rainfall_next_72h=forecast_rain,
            forecast_temperature_next_7d=forecast_temperature,
            elevation=lake.elevation_m,
            distance_to_nearest_settlement_km=lake.distance_to_nearest_settlement_km,
            satellite_observation_date=(
                satellite_current.observed_at if satellite_current else None
            ),
            weather_observation_date=weather_update,
            data_quality_status=status,
            satellite_cloud_percentage=(
                satellite_current.cloud_percentage if satellite_current else None
            ),
            forecast_creation_time=forecast_creation,
            forecast_horizon_hours=forecast_horizon,
            satellite_freshness_status=satellite_freshness,
            observed_weather_freshness_status=observed_weather_freshness,
            forecast_freshness_status=forecast_freshness,
            historical_baseline_status=(
                "AVAILABLE" if baseline_update is not None else "UNAVAILABLE"
            ),
            historical_baseline_observation_date=baseline_update,
            source_mode=source_mode,
        )
        baseline = baseline_index.score(lake)
        if self.baseline_model is not None:
            baseline_probability = self.baseline_model.probability(
                {
                    "area_current_km2": lake.reference_area_km2,
                    "elevation": lake.elevation_m,
                    "distance_to_nearest_settlement_km": lake.distance_to_nearest_settlement_km,
                    "latitude": lake.latitude,
                    "longitude": lake.longitude,
                }
            )
            baseline_score = max(0.0, min(100.0, baseline_probability * 100.0))
            baseline = (baseline_score, level_for_score(baseline_score), baseline[2])
        result = DynamicEnvironmentalIndex(self.settings.model_version).score(
            record,
            baseline=baseline,
            freshness_penalty=freshness_penalty,
            source_mode=source_mode,
        )
        record.baseline_susceptibility_score = result.baseline_susceptibility_score
        record.baseline_susceptibility_level = result.baseline_susceptibility_level
        record.risk_score = result.risk_score
        record.risk_level = result.risk_level
        record.confidence_level = result.confidence_level
        record.uncertainty_score = result.uncertainty_score
        record.most_influential_factors = result.influential_factors
        record.model_version = result.model_version
        record.prediction_timestamp = current if result.risk_score is not None else None
        combined_warnings = warnings + (
            [result.data_quality_warning] if result.data_quality_warning else []
        )
        record.data_quality_warning = "; ".join(combined_warnings) or None
        if self.dynamic_model is not None:
            feature_values = {
                field: getattr(record, field)
                for field in (
                    "area_current_km2",
                    "area_change_30d",
                    "area_change_90d",
                    "area_change_365d",
                    "temperature_current",
                    "temperature_7d_average",
                    "temperature_anomaly",
                    "rainfall_anomaly",
                    "rainfall_last_24h",
                    "rainfall_last_7d",
                    "rainfall_last_30d",
                    "forecast_rainfall_next_72h",
                    "forecast_temperature_next_7d",
                    "elevation",
                    "distance_to_nearest_settlement_km",
                    "latitude",
                    "longitude",
                )
            }
            probability = self.dynamic_model.probability(feature_values)
            record.risk_score = round(max(0.0, min(1.0, probability)) * 100.0, 2)
            record.risk_level = level_for_score(record.risk_score)
            record.prediction_timestamp = current
            record.model_version = f"{self.dynamic_model.version}-calibrated-probability"
            explanations = self.dynamic_model.influential_factors(feature_values)
            if explanations:
                record.most_influential_factors = explanations
            else:
                record.data_quality_warning = (
                    f"{record.data_quality_warning}; saved model does not expose a local "
                    "factor explanation"
                )
        signature_fields = (
            "latitude",
            "longitude",
            "area_current_km2",
            "area_change_30d",
            "area_change_90d",
            "area_change_365d",
            "temperature_current",
            "temperature_7d_average",
            "temperature_anomaly",
            "rainfall_anomaly",
            "rainfall_last_24h",
            "rainfall_last_7d",
            "rainfall_last_30d",
            "forecast_rainfall_next_72h",
            "forecast_temperature_next_7d",
            "elevation",
            "distance_to_nearest_settlement_km",
            "satellite_observation_date",
            "weather_observation_date",
            "satellite_cloud_percentage",
            "forecast_creation_time",
            "forecast_horizon_hours",
            "satellite_freshness_status",
            "observed_weather_freshness_status",
            "forecast_freshness_status",
            "historical_baseline_status",
            "historical_baseline_observation_date",
            "data_quality_status",
            "data_quality_warning",
            "model_version",
            "source_mode",
        )
        serialized = record.to_dict()
        signature_values = {key: serialized[key] for key in signature_fields}
        record.input_signature = hashlib.sha256(
            json.dumps(signature_values, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return record

    def run(self) -> RefreshSummary:
        self.settings.validate()
        self.repository.initialize()
        all_lakes = self.repository.list_lakes()
        lakes = self.repository.list_lakes(self.lake_ids) if self.lake_ids else all_lakes
        if self.lake_ids and len(lakes) != len(set(self.lake_ids)):
            found = {lake.lake_id for lake in lakes}
            missing = sorted(set(self.lake_ids) - found)
            raise ValueError("Unknown lake_id values: " + ", ".join(missing))
        baseline_index = BaselineSusceptibilityIndex(all_lakes)
        current = self.now().astimezone(UTC)
        created = unchanged = failures = rejected = 0
        for lake in lakes:
            try:
                validate_lake(lake)
                lake_failures, lake_rejected = self._refresh_sources(lake, current)
                failures += lake_failures
                rejected += lake_rejected
                record = self._build_record(lake, current, baseline_index)
                if self.repository.save_record_if_changed(record):
                    created += 1
                else:
                    unchanged += 1
            except Exception as exc:
                failures += 1
                self.repository.record_failure(lake.lake_id, "refresh_pipeline", exc)
        self.repository.export_records_csv(self.settings.output_csv_path)
        return RefreshSummary(
            lakes_processed=len(lakes),
            records_created=created,
            unchanged_records=unchanged,
            failures=failures,
            rejected_satellite_measurements=rejected,
        )
