"""Sentinel-2 SR Harmonized lake-area ingestion through Earth Engine."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Callable

from ..config import Settings
from ..types import Lake, QualityStatus, SatelliteObservation
from .base import ProviderError


S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CLOUD_SCORE_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"


class EarthEngineSentinel2Provider:
    """Estimate lake area from the newest usable Sentinel-2 scene.

    Cloud Score+ masks unclear pixels and Sentinel-2's SCL band additionally
    rejects cloud shadows, medium/high probability cloud, cirrus, and snow/ice.
    A rejected measurement carries no area and must not replace stored reliable
    observations.
    """

    def __init__(
        self,
        settings: Settings,
        ee_module: Any | None = None,
        before_remote_call: Callable[[], None] | None = None,
    ) -> None:
        if not settings.gee_project:
            raise ProviderError("GLOF_GEE_PROJECT is required for Earth Engine")
        if ee_module is None:
            try:
                import ee as ee_module  # type: ignore[no-redef]
            except ImportError as exc:
                raise ProviderError("Install earthengine-api before using Sentinel-2") from exc
        self.ee = ee_module
        self.settings = settings
        self.before_remote_call = before_remote_call or (lambda: None)
        self.scene_lookup_calls = 0
        self.scene_measurement_calls = 0
        self.last_diagnostics: dict[str, dict[str, Any]] = {}
        try:
            self.ee.Initialize(project=settings.gee_project)
        except Exception as exc:
            raise ProviderError(
                "Earth Engine initialization failed. Run 'earthengine authenticate' once, "
                "then set GLOF_GEE_PROJECT to a registered Cloud project."
            ) from exc

    def _masked_image(self, image: Any) -> Any:
        score_clear = image.select("cs_cdf").gte(
            self.settings.sentinel_cloud_score_threshold
        )
        scl = image.select("SCL")
        scl_clear = (
            scl.neq(3)
            .And(scl.neq(8))
            .And(scl.neq(9))
            .And(scl.neq(10))
            .And(scl.neq(11))
        )
        clear = score_clear.And(scl_clear).rename("clear")
        return image.addBands(clear).updateMask(clear)

    def _measure_image(
        self, lake: Lake, point: Any, region: Any, image: Any
    ) -> SatelliteObservation:
        ee = self.ee
        masked = self._masked_image(image)
        clear_value = masked.select("clear").unmask(0).reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=20,
            bestEffort=True,
            maxPixels=10_000_000,
        ).get("clear")

        index_name = self.settings.sentinel_water_index
        bands = ["B3", "B11"] if index_name == "MNDWI" else ["B3", "B8"]
        water_index = masked.normalizedDifference(bands).rename("water_index")
        water = water_index.gt(self.settings.sentinel_water_threshold)
        boundaries = water.selfMask().reduceToVectors(
            geometry=region,
            scale=20,
            geometryType="polygon",
            eightConnected=True,
            labelProperty="water",
            maxPixels=100_000_000,
        )
        lake_boundaries = boundaries.filterBounds(point.buffer(30))
        area_value = ee.Algorithms.If(
            lake_boundaries.size().gt(0), lake_boundaries.geometry().area(1), None
        )
        try:
            self.before_remote_call()
            self.scene_measurement_calls += 1
            info = ee.Dictionary(
                {
                    "clear_fraction": clear_value,
                    "area_m2": area_value,
                    "time": image.get("system:time_start"),
                    "image_id": image.get("system:index"),
                    "scene_cloud": image.get("CLOUDY_PIXEL_PERCENTAGE"),
                }
            ).getInfo()
            clear_fraction = float(info.get("clear_fraction") or 0.0)
            area_m2 = info.get("area_m2")
            observed_at = datetime.fromtimestamp(float(info["time"]) / 1000.0, tz=UTC)
            image_id = str(info.get("image_id") or "unknown")
            scene_cloud = float(info.get("scene_cloud") or 100.0)
        except Exception as exc:
            raise ProviderError(f"Sentinel-2 reduction failed for {lake.lake_id}") from exc

        effective_cloud = max(100.0 * (1.0 - clear_fraction), scene_cloud)
        rejection_reasons: list[str] = []
        if clear_fraction < self.settings.sentinel_min_clear_fraction:
            rejection_reasons.append("insufficient clear pixels")
        if effective_cloud > self.settings.sentinel_max_cloud_percent:
            rejection_reasons.append("cloud percentage above configured limit")
        if area_m2 is None:
            rejection_reasons.append("no reliable water pixels")
        if rejection_reasons:
            return SatelliteObservation(
                lake_id=lake.lake_id,
                observed_at=observed_at,
                area_km2=None,
                cloud_percentage=round(effective_cloud, 3),
                clear_fraction=round(clear_fraction, 4),
                quality_status=QualityStatus.REJECTED,
                source_image_id=image_id,
                water_index=index_name,
                rejection_reason="; ".join(rejection_reasons),
            )
        return SatelliteObservation(
            lake_id=lake.lake_id,
            observed_at=observed_at,
            area_km2=float(area_m2) / 1_000_000.0,
            cloud_percentage=round(effective_cloud, 3),
            clear_fraction=round(clear_fraction, 4),
            quality_status=QualityStatus.RELIABLE,
            source_image_id=image_id,
            water_index=index_name,
        )

    def latest(self, lake: Lake, start: date, end: date) -> SatelliteObservation | None:
        if start >= end:
            raise ValueError("Sentinel-2 start date must be earlier than end date")
        ee = self.ee
        point = ee.Geometry.Point([lake.longitude, lake.latitude])
        region = point.buffer(self.settings.sentinel_search_radius_m)
        raw_s2 = (
            ee.ImageCollection(S2_COLLECTION)
            .filterBounds(region)
            .filterDate(start.isoformat(), end.isoformat())
        )
        # A scene above this metadata limit can never pass the later effective-
        # cloud test, so do not spend a reduction call measuring it.
        metadata_clear = raw_s2.filter(
            ee.Filter.lte(
                "CLOUDY_PIXEL_PERCENTAGE",
                self.settings.sentinel_max_cloud_percent,
            )
        )
        cloud_score = (
            ee.ImageCollection(CLOUD_SCORE_COLLECTION)
            .filterBounds(region)
            .filterDate(start.isoformat(), end.isoformat())
        )
        metadata_ids = metadata_clear.aggregate_array("system:index")
        matched_cloud_score = cloud_score.filter(
            ee.Filter.inList("system:index", metadata_ids)
        )
        matched_ids = matched_cloud_score.aggregate_array("system:index")
        matched_s2 = metadata_clear.filter(
            ee.Filter.inList("system:index", matched_ids)
        )
        linked = matched_s2.linkCollection(matched_cloud_score, ["cs_cdf"]).sort(
            "system:time_start", False
        )
        try:
            self.before_remote_call()
            self.scene_lookup_calls += 1
            recent_start = end.fromordinal(
                end.toordinal() - self.settings.sentinel_lookback_days
            )
            counts = ee.Dictionary(
                {
                    "raw_scene_count": raw_s2.size(),
                    "metadata_clear_scene_count": metadata_clear.size(),
                    "cloud_score_match_count": matched_s2.size(),
                    "recent_raw_scene_count": raw_s2.filterDate(
                        recent_start.isoformat(), end.isoformat()
                    ).size(),
                    "recent_metadata_clear_scene_count": metadata_clear.filterDate(
                        recent_start.isoformat(), end.isoformat()
                    ).size(),
                }
            ).getInfo()
            count = int(counts.get("cloud_score_match_count") or 0)
        except Exception as exc:
            raise ProviderError(f"Sentinel-2 scene lookup failed for {lake.lake_id}") from exc
        diagnostics: dict[str, Any] = {
            **{key: int(value or 0) for key, value in counts.items()},
            "search_window_days": (end - start).days,
            "recent_window_days": self.settings.sentinel_lookback_days,
            "candidates_measured": 0,
            "rejection_reasons": [],
            "diagnostic_reason": None,
        }
        if count == 0:
            if diagnostics["raw_scene_count"] == 0:
                reason = "NO_SCENE_COVERAGE_IN_SEARCH_WINDOW"
            elif diagnostics["metadata_clear_scene_count"] == 0:
                reason = "ALL_SCENES_FAILED_METADATA_CLOUD_FILTER"
            else:
                reason = "CLOUD_SCORE_JOIN_UNAVAILABLE"
            diagnostics["diagnostic_reason"] = reason
            self.last_diagnostics[lake.lake_id] = diagnostics
            return None

        candidate_count = min(
            count, self.settings.sentinel_area_max_candidate_scenes
        )
        images = linked.limit(candidate_count).toList(candidate_count)
        newest_rejection: SatelliteObservation | None = None
        rejection_reasons: list[str] = []
        for index in range(candidate_count):
            observation = self._measure_image(
                lake, point, region, ee.Image(images.get(index))
            )
            diagnostics["candidates_measured"] = index + 1
            if observation.quality_status == QualityStatus.RELIABLE:
                age_at_search_end = (
                    datetime.combine(end, datetime.min.time(), tzinfo=UTC)
                    - observation.observed_at
                ).total_seconds() / 86_400
                diagnostics["diagnostic_reason"] = (
                    "USABLE_RECOVERED_FROM_EXTENDED_WINDOW"
                    if age_at_search_end > self.settings.sentinel_lookback_days
                    else "USABLE_IN_RECENT_WINDOW"
                )
                diagnostics["usable_observation_age_days_at_search"] = round(
                    age_at_search_end, 3
                )
                diagnostics["rejection_reasons"] = rejection_reasons
                self.last_diagnostics[lake.lake_id] = diagnostics
                return observation
            rejection_reasons.append(
                str(observation.rejection_reason or "unknown rejection")
            )
            if newest_rejection is None:
                newest_rejection = observation
        diagnostics["rejection_reasons"] = rejection_reasons
        joined_reasons = " ".join(rejection_reasons).lower()
        if "no reliable water pixels" in joined_reasons and not any(
            word in joined_reasons
            for word in ("cloud percentage", "insufficient clear pixels")
        ):
            reason = "NO_WATER_PIXELS_NEAR_LAKE_CENTROID"
        elif any(
            word in joined_reasons
            for word in ("cloud percentage", "insufficient clear pixels")
        ):
            reason = "PIXEL_CLOUD_OR_SHADOW_FILTERING"
        elif count > candidate_count:
            reason = "CANDIDATE_MEASUREMENT_LIMIT_REACHED"
        else:
            reason = "NO_RELIABLE_WATER_MEASUREMENT"
        diagnostics["diagnostic_reason"] = reason
        self.last_diagnostics[lake.lake_id] = diagnostics
        return newest_rejection

    def diagnostics_for(self, lake_id: str) -> dict[str, Any]:
        return dict(self.last_diagnostics.get(lake_id, {}))
