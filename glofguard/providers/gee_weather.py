"""Recent observed and forecast weather summaries from public Earth Engine data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from ..config import Settings
from ..types import (
    ForecastPoint,
    ForecastSummary,
    HourlyPrecipitation,
    Lake,
    ObservedRainfallSummary,
)
from .base import ProviderError


GSMAP_COLLECTION = "JAXA/GPM_L3/GSMaP/v8/operational"
GSMAP_BAND = "hourlyPrecipRateGC"
GFS_COLLECTION = "NOAA/GFS0P25"
GFS_PRECIPITATION_BAND = "total_precipitation_surface"
GFS_TEMPERATURE_BAND = "temperature_2m_above_ground"


class _EarthEngineWeatherProvider:
    def __init__(
        self,
        settings: Settings,
        ee_module: Any | None = None,
        before_remote_call: Callable[[], None] | None = None,
    ) -> None:
        if not settings.gee_project:
            raise ProviderError("GLOF_GEE_PROJECT is required for Earth Engine")
        initialize_earth_engine = ee_module is None
        if ee_module is None:
            try:
                import ee as ee_module  # type: ignore[no-redef]
            except ImportError as exc:
                raise ProviderError(
                    "Install earthengine-api before using Earth Engine weather"
                ) from exc
        self.ee = ee_module
        self.settings = settings
        self.before_remote_call = before_remote_call or (lambda: None)
        self.summary_calls = 0
        if initialize_earth_engine:
            try:
                self.ee.Initialize(project=settings.gee_project)
            except Exception as exc:
                raise ProviderError(
                    "Earth Engine initialization failed for public weather collections"
                ) from exc

    def _get_info(self, value: Any, source: str, lake_id: str) -> dict[str, Any]:
        try:
            self.before_remote_call()
            self.summary_calls += 1
            result = value.getInfo()
            if not isinstance(result, dict):
                raise TypeError("Earth Engine returned a non-dictionary summary")
            return result
        except Exception as exc:
            raise ProviderError(f"{source} summary failed for {lake_id}") from exc


class EarthEngineGsmapProvider(_EarthEngineWeatherProvider):
    """Aggregate hourly gauge-adjusted GSMaP rates into recent rainfall totals."""

    def hourly(
        self, lake: Lake, start: datetime, end: datetime
    ) -> list[HourlyPrecipitation]:
        """Return dated hourly point observations for the append-safe refresh path."""
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        if start > end:
            return []
        ee = self.ee
        point = ee.Geometry.Point([lake.longitude, lake.latitude])
        collection = (
            ee.ImageCollection(GSMAP_COLLECTION)
            .filterDate(start.isoformat(), end.isoformat())
            .filterBounds(point)
        )

        def attach_point_value(image: Any) -> Any:
            rainfall = image.reduceRegion(
                reducer=ee.Reducer.first(),
                geometry=point,
                scale=11_132,
                bestEffort=True,
                maxPixels=100_000,
            ).get(GSMAP_BAND)
            return image.set("glof_point_rainfall", rainfall)

        sampled = collection.map(attach_point_value).filter(
            ee.Filter.notNull(["glof_point_rainfall"])
        )
        info = self._get_info(
            ee.Dictionary(
                {
                    "times": sampled.aggregate_array("system:time_start"),
                    "rainfall": sampled.aggregate_array("glof_point_rainfall"),
                }
            ),
            "JAXA GSMaP",
            lake.lake_id,
        )
        times = info.get("times") or []
        values = info.get("rainfall") or []
        return [
            HourlyPrecipitation(
                lake_id=lake.lake_id,
                observed_at=datetime.fromtimestamp(float(timestamp) / 1000.0, tz=UTC),
                # hourlyPrecipRateGC is mm/hour and each image represents one hour.
                rainfall_mm=float(rainfall),
                source="JAXA GSMaP V8 operational / Google Earth Engine",
            )
            for timestamp, rainfall in zip(times, values, strict=True)
        ]

    def summary(
        self, lake: Lake, end: datetime
    ) -> ObservedRainfallSummary | None:
        end = end.astimezone(UTC)
        ee = self.ee
        point = ee.Geometry.Point([lake.longitude, lake.latitude])
        start_30d = end - timedelta(days=31)
        collection = (
            ee.ImageCollection(GSMAP_COLLECTION)
            .filterDate(start_30d.isoformat(), end.isoformat())
            .filterBounds(point)
        )
        collection_count = collection.size()
        latest_time = collection.aggregate_max("system:time_start")
        # Rolling totals end at the latest published observation. Dataset age is
        # reported separately, so normal source latency does not manufacture
        # artificial missing hours inside the accumulation windows.
        accumulation_end = ee.Date(latest_time).advance(1, "hour")

        def window(days: int) -> Any:
            return collection.filterDate(
                accumulation_end.advance(-days, "day"), accumulation_end
            )

        def total(image_collection: Any) -> Any:
            count = image_collection.size()
            value = image_collection.select(GSMAP_BAND).sum().reduceRegion(
                reducer=ee.Reducer.first(),
                geometry=point,
                scale=11_132,
                bestEffort=True,
                maxPixels=100_000,
            ).get(GSMAP_BAND)
            return ee.Algorithms.If(count.gt(0), value, None)

        hours_24 = window(1)
        hours_7d = window(7)
        hours_30d = window(30)
        count_30d = hours_30d.size()
        latest = ee.Image(hours_30d.sort("system:time_start", False).first())
        payload = ee.Dictionary(
            {
                "count_24h": hours_24.size(),
                "count_7d": hours_7d.size(),
                "count_30d": count_30d,
                "rainfall_24h": total(hours_24),
                "rainfall_7d": total(hours_7d),
                "rainfall_30d": total(hours_30d),
                "latest_time": ee.Algorithms.If(
                    collection_count.gt(0), latest_time, None
                ),
                "product_status": ee.Algorithms.If(
                    count_30d.gt(0), latest.get("status"), None
                ),
            }
        )
        info = self._get_info(payload, "JAXA GSMaP", lake.lake_id)
        if int(info.get("count_30d") or 0) == 0 or info.get("latest_time") is None:
            return None
        return ObservedRainfallSummary(
            lake_id=lake.lake_id,
            observation_time=datetime.fromtimestamp(
                float(info["latest_time"]) / 1000.0, tz=UTC
            ),
            rainfall_last_24h_mm=(
                float(info["rainfall_24h"])
                if info.get("rainfall_24h") is not None
                else None
            ),
            rainfall_last_7d_mm=(
                float(info["rainfall_7d"])
                if info.get("rainfall_7d") is not None
                else None
            ),
            rainfall_last_30d_mm=(
                float(info["rainfall_30d"])
                if info.get("rainfall_30d") is not None
                else None
            ),
            available_hours_24h=int(info.get("count_24h") or 0),
            available_hours_7d=int(info.get("count_7d") or 0),
            available_hours_30d=int(info.get("count_30d") or 0),
            product_status=(
                str(info["product_status"])
                if info.get("product_status") is not None
                else None
            ),
        )


class EarthEngineGfsProvider(_EarthEngineWeatherProvider):
    """Read the latest complete GFS cycle and aggregate official forecast bands."""

    def _latest_cycle(self, now: datetime) -> tuple[Any, Any, Any]:
        ee = self.ee
        recent_creation_ms = int((now - timedelta(hours=48)).timestamp() * 1000)
        now_ms = int(now.timestamp() * 1000)
        collection = (
            ee.ImageCollection(GFS_COLLECTION)
            .filter(ee.Filter.gte("creation_time", recent_creation_ms))
            .filter(ee.Filter.lte("creation_time", now_ms))
        )
        complete_cycle_markers = collection.filter(
            ee.Filter.eq("forecast_hours", 168)
        )
        latest_creation = complete_cycle_markers.aggregate_max("creation_time")
        return complete_cycle_markers, latest_creation, collection.filter(
            ee.Filter.eq("creation_time", latest_creation)
        )

    def forecast(
        self, lake: Lake, created_after: datetime
    ) -> list[ForecastPoint]:
        """Return the latest complete cycle as dated forecast points."""
        created_after = created_after.astimezone(UTC)
        now = datetime.now(UTC)
        ee = self.ee
        point = ee.Geometry.Point([lake.longitude, lake.latitude])
        markers, latest_creation, cycle = self._latest_cycle(now)
        cycle = cycle.filter(
            ee.Filter.And(
                ee.Filter.gte("forecast_hours", 1),
                ee.Filter.lte("forecast_hours", 168),
            )
        )

        def attach_point_values(image: Any) -> Any:
            values = image.reduceRegion(
                reducer=ee.Reducer.first(),
                geometry=point,
                scale=27_830,
                bestEffort=True,
                maxPixels=100_000,
            )
            return image.set(
                {
                    "glof_temperature": values.get(GFS_TEMPERATURE_BAND),
                    "glof_precipitation": values.get(GFS_PRECIPITATION_BAND),
                }
            )

        sampled = cycle.map(attach_point_values).filter(
            ee.Filter.notNull(["glof_temperature"])
        )
        precipitation = sampled.filter(
            ee.Filter.inList("forecast_hours", list(range(6, 73, 6)))
        )
        info = self._get_info(
            ee.Dictionary(
                {
                    "complete_cycle_count": markers.size(),
                    "creation_time": latest_creation,
                    "forecast_hours": sampled.aggregate_array("forecast_hours"),
                    "forecast_times": sampled.aggregate_array("forecast_time"),
                    "temperatures": sampled.aggregate_array("glof_temperature"),
                    "precipitation_hours": precipitation.aggregate_array(
                        "forecast_hours"
                    ),
                    "precipitation": precipitation.aggregate_array(
                        "glof_precipitation"
                    ),
                }
            ),
            "NOAA GFS",
            lake.lake_id,
        )
        if int(info.get("complete_cycle_count") or 0) == 0:
            return []
        created_at = datetime.fromtimestamp(
            float(info["creation_time"]) / 1000.0, tz=UTC
        )
        if created_at <= created_after:
            return []
        precipitation_by_hour = {
            int(hour): float(value)
            for hour, value in zip(
                info.get("precipitation_hours") or [],
                info.get("precipitation") or [],
                strict=True,
            )
        }
        return [
            ForecastPoint(
                lake_id=lake.lake_id,
                forecast_created_at=created_at,
                valid_at=datetime.fromtimestamp(float(valid_time) / 1000.0, tz=UTC),
                precipitation_mm=precipitation_by_hour.get(int(horizon)),
                temperature_c=float(temperature),
                horizon_hours=int(horizon),
                source="NOAA GFS 0.25 degree / Google Earth Engine",
            )
            for horizon, valid_time, temperature in zip(
                info.get("forecast_hours") or [],
                info.get("forecast_times") or [],
                info.get("temperatures") or [],
                strict=True,
            )
        ]

    def summary(self, lake: Lake, now: datetime) -> ForecastSummary | None:
        now = now.astimezone(UTC)
        ee = self.ee
        point = ee.Geometry.Point([lake.longitude, lake.latitude])
        complete_cycle_markers, latest_creation, cycle = self._latest_cycle(now)
        complete_count = complete_cycle_markers.size()
        precipitation = cycle.filter(
            ee.Filter.inList("forecast_hours", list(range(6, 73, 6)))
        )
        temperature_24h = cycle.filter(
            ee.Filter.And(
                ee.Filter.gte("forecast_hours", 1),
                ee.Filter.lte("forecast_hours", 24),
            )
        )
        temperature_7d = cycle.filter(
            ee.Filter.And(
                ee.Filter.gte("forecast_hours", 1),
                ee.Filter.lte("forecast_hours", 168),
            )
        )

        def point_value(image: Any, band: str) -> Any:
            return image.reduceRegion(
                reducer=ee.Reducer.first(),
                geometry=point,
                scale=27_830,
                bestEffort=True,
                maxPixels=100_000,
            ).get(band)

        payload = ee.Dictionary(
            {
                "complete_cycle_count": complete_count,
                "creation_time": ee.Algorithms.If(
                    complete_count.gt(0), latest_creation, None
                ),
                "precipitation_steps": precipitation.size(),
                "temperature_steps_24h": temperature_24h.size(),
                "temperature_steps_7d": temperature_7d.size(),
                # The catalog specifies that six-hour multiples are non-overlapping.
                "rainfall_72h": ee.Algorithms.If(
                    complete_count.gt(0),
                    point_value(
                        precipitation.select(GFS_PRECIPITATION_BAND).sum(),
                        GFS_PRECIPITATION_BAND,
                    ),
                    None,
                ),
                "temperature_24h": ee.Algorithms.If(
                    complete_count.gt(0),
                    point_value(
                        temperature_24h.select(GFS_TEMPERATURE_BAND).mean(),
                        GFS_TEMPERATURE_BAND,
                    ),
                    None,
                ),
                "temperature_7d": ee.Algorithms.If(
                    complete_count.gt(0),
                    point_value(
                        temperature_7d.select(GFS_TEMPERATURE_BAND).mean(),
                        GFS_TEMPERATURE_BAND,
                    ),
                    None,
                ),
            }
        )
        info = self._get_info(payload, "NOAA GFS", lake.lake_id)
        if (
            int(info.get("complete_cycle_count") or 0) == 0
            or info.get("creation_time") is None
        ):
            return None
        return ForecastSummary(
            lake_id=lake.lake_id,
            forecast_created_at=datetime.fromtimestamp(
                float(info["creation_time"]) / 1000.0, tz=UTC
            ),
            forecast_rainfall_next_72h_mm=(
                float(info["rainfall_72h"])
                if info.get("rainfall_72h") is not None
                else None
            ),
            forecast_temperature_next_24h_c=(
                float(info["temperature_24h"])
                if info.get("temperature_24h") is not None
                else None
            ),
            forecast_temperature_next_7d_c=(
                float(info["temperature_7d"])
                if info.get("temperature_7d") is not None
                else None
            ),
            precipitation_steps=int(info.get("precipitation_steps") or 0),
            temperature_steps_24h=int(info.get("temperature_steps_24h") or 0),
            temperature_steps_7d=int(info.get("temperature_steps_7d") or 0),
            forecast_horizon_hours=168,
        )
