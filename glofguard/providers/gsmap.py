"""JAXA GSMaP v8 hourly gauge-calibrated precipitation ingestion."""

from __future__ import annotations

import gzip
import math
import struct
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from ..config import Settings
from ..types import HourlyPrecipitation, Lake
from .base import ProviderError


class JaxaGsmapProvider:
    """Read the lake's nearest 0.1-degree cell from official GSMaP binaries.

    The JAXA v8 gauge-calibrated files contain 3600 x 1200 little-endian
    float32 values in mm/hr.  Access credentials and the download host supplied
    after JAXA registration are read only from environment-backed settings.
    """

    GRID_LONGITUDES = 3600
    GRID_LATITUDES = 1200
    FIRST_LONGITUDE = 0.05
    FIRST_LATITUDE = 59.95
    RESOLUTION = 0.1

    def __init__(self, settings: Settings, session: Any | None = None) -> None:
        if not settings.gsmap_username or not settings.gsmap_password:
            raise ProviderError(
                "GLOF_GSMAP_USERNAME and GLOF_GSMAP_PASSWORD are required. "
                "Register with JAXA Global Rainfall Watch first."
            )
        if not settings.gsmap_base_url:
            raise ProviderError("GLOF_GSMAP_BASE_URL must be the JAXA download host")
        self.settings = settings
        self.session = session or requests.Session()
        self.cache_dir = settings.gsmap_cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _grid_offset(cls, latitude: float, longitude: float) -> int:
        if not -60.0 < latitude < 60.0:
            raise ValueError("GSMaP NRT binary coverage is 60S to 60N")
        normalized_lon = longitude % 360.0
        lon_index = int(round((normalized_lon - cls.FIRST_LONGITUDE) / cls.RESOLUTION))
        lat_index = int(round((cls.FIRST_LATITUDE - latitude) / cls.RESOLUTION))
        lon_index = max(0, min(cls.GRID_LONGITUDES - 1, lon_index))
        lat_index = max(0, min(cls.GRID_LATITUDES - 1, lat_index))
        return (lat_index * cls.GRID_LONGITUDES + lon_index) * 4

    @classmethod
    def parse_nearest_value(
        cls, compressed: bytes, latitude: float, longitude: float
    ) -> float:
        try:
            payload = gzip.decompress(compressed)
        except OSError as exc:
            raise ProviderError("GSMaP response was not a valid gzip file") from exc
        expected = cls.GRID_LONGITUDES * cls.GRID_LATITUDES * 4
        if len(payload) != expected:
            raise ProviderError(
                f"Unexpected GSMaP binary size: {len(payload)} bytes; expected {expected}"
            )
        offset = cls._grid_offset(latitude, longitude)
        value = float(struct.unpack_from("<f", payload, offset)[0])
        if not math.isfinite(value) or value < 0:
            raise ProviderError("GSMaP nearest cell is missing or invalid")
        return value

    def _url(self, observed_at: datetime) -> str:
        stamp = observed_at.astimezone(UTC)
        version = self.settings.gsmap_algorithm_version
        filename = f"gsmap_gauge.{stamp:%Y%m%d}.{stamp:%H}00.dat.gz"
        return (
            f"{self.settings.gsmap_base_url}/realtime_ver/{version}/hourly_G/"
            f"{stamp:%Y/%m/%d}/{filename}"
        )

    def _cached_payload(self, observed_at: datetime, auth: tuple[str, str]) -> bytes:
        stamp = observed_at.astimezone(UTC)
        filename = f"gsmap_gauge.{stamp:%Y%m%d}.{stamp:%H}00.dat.gz"
        cache_path = self.cache_dir / f"{stamp:%Y}" / f"{stamp:%m}" / f"{stamp:%d}" / filename
        if cache_path.exists():
            return cache_path.read_bytes()
        response = self.session.get(
            self._url(stamp),
            auth=auth,
            timeout=self.settings.gsmap_timeout_seconds,
        )
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(cache_path.suffix + ".part")
        temporary.write_bytes(response.content)
        temporary.replace(cache_path)
        return response.content

    def hourly(
        self, lake: Lake, start: datetime, end: datetime
    ) -> list[HourlyPrecipitation]:
        if start > end:
            raise ValueError("GSMaP start time must not be after end time")
        rows: list[HourlyPrecipitation] = []
        failures: list[str] = []
        cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        final = end.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        auth = (self.settings.gsmap_username, self.settings.gsmap_password)
        while cursor <= final:
            try:
                payload = self._cached_payload(cursor, auth)
                rain_rate = self.parse_nearest_value(
                    payload, lake.latitude, lake.longitude
                )
                rows.append(
                    HourlyPrecipitation(
                        lake_id=lake.lake_id,
                        observed_at=cursor,
                        rainfall_mm=rain_rate,
                    )
                )
            except (requests.RequestException, ProviderError) as exc:
                failures.append(f"{cursor.isoformat()}: {exc}")
            cursor += timedelta(hours=1)
        if not rows:
            detail = failures[0] if failures else "no files were requested"
            raise ProviderError(f"No usable GSMaP hours for {lake.lake_id}: {detail}")
        return rows
