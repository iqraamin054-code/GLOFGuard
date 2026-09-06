"""NOAA NOMADS GFS short-horizon forecast ingestion."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from ..config import Settings
from ..types import ForecastPoint, Lake
from .base import ProviderError


DecodedGfs = tuple[float | None, float | None]


def decode_grib_point(payload: bytes, latitude: float, longitude: float) -> DecodedGfs:
    """Decode filtered GRIB2 bytes with xarray/cfgrib.

    Returns interval precipitation in millimetres and 2-metre temperature in
    Celsius.  The optional cfgrib/eccodes stack is needed only for live NOAA
    ingestion; tests inject a decoder and do not pretend it is a live result.
    """
    try:
        import xarray as xr
    except ImportError as exc:
        raise ProviderError(
            "Live NOAA GFS decoding needs xarray, cfgrib, and eccodes"
        ) from exc
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as handle:
            handle.write(payload)
            path = Path(handle.name)
        datasets = xr.open_datasets(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
        precipitation: float | None = None
        temperature: float | None = None
        target_lon = longitude % 360.0
        for dataset in datasets:
            for name, data_array in dataset.data_vars.items():
                short_name = str(data_array.attrs.get("GRIB_shortName", name)).lower()
                selector: dict[str, float] = {}
                if "latitude" in data_array.coords:
                    selector["latitude"] = latitude
                if "longitude" in data_array.coords:
                    selector["longitude"] = target_lon
                value = data_array.sel(selector, method="nearest").squeeze().item()
                if short_name in {"tp", "apcp"}:
                    precipitation = float(value)
                elif short_name in {"2t", "t2m"}:
                    value_float = float(value)
                    temperature = value_float - 273.15 if value_float > 150 else value_float
            dataset.close()
        return precipitation, temperature
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("Unable to decode the filtered NOAA GFS GRIB2 response") from exc
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


class NoaaGfsProvider:
    """Download point-sized GFS 0.25-degree APCP and 2-metre temperature."""

    CYCLES = (0, 6, 12, 18)

    def __init__(
        self,
        settings: Settings,
        session: Any | None = None,
        decoder: Callable[[bytes, float, float], DecodedGfs] = decode_grib_point,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.decoder = decoder
        self.now = now or (lambda: datetime.now(UTC))
        self.cache_dir = settings.noaa_cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._decoded_cache: dict[tuple[str, int, float, float], DecodedGfs] = {}

    @classmethod
    def latest_completed_cycle(cls, now: datetime) -> datetime:
        # A conservative five-hour availability lag avoids requesting a cycle
        # before the selected forecast files have reached NOMADS.
        available = now.astimezone(UTC) - timedelta(hours=5)
        cycle_hour = max(hour for hour in cls.CYCLES if hour <= available.hour)
        return available.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)

    def _request_params(self, lake: Lake, cycle: datetime, hour: int) -> dict[str, Any]:
        return {
            "file": f"gfs.t{cycle:%H}z.pgrb2.0p25.f{hour:03d}",
            "lev_surface": "on",
            "lev_2_m_above_ground": "on",
            "var_APCP": "on",
            "var_TMP": "on",
            "subregion": "",
            "leftlon": self.settings.gfs_left_longitude,
            "rightlon": self.settings.gfs_right_longitude,
            "toplat": self.settings.gfs_top_latitude,
            "bottomlat": self.settings.gfs_bottom_latitude,
            "dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos",
        }

    def _payload(self, lake: Lake, cycle: datetime, hour: int) -> bytes:
        bounds = (
            f"{self.settings.gfs_left_longitude:g}_{self.settings.gfs_right_longitude:g}_"
            f"{self.settings.gfs_bottom_latitude:g}_{self.settings.gfs_top_latitude:g}"
        ).replace("-", "m").replace(".", "p")
        cache_path = (
            self.cache_dir
            / f"{cycle:%Y%m%d%H}"
            / f"gfs_f{hour:03d}_{bounds}.grib2"
        )
        if cache_path.exists():
            return cache_path.read_bytes()
        response = self.session.get(
            self.settings.noaa_gfs_filter_url,
            params=self._request_params(lake, cycle, hour),
            timeout=self.settings.noaa_timeout_seconds,
        )
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".grib2.part")
        temporary.write_bytes(response.content)
        temporary.replace(cache_path)
        return response.content

    def forecast(self, lake: Lake, created_after: datetime) -> list[ForecastPoint]:
        cycle = self.latest_completed_cycle(self.now())
        if cycle < created_after.astimezone(UTC):
            return []
        rows: list[ForecastPoint] = []
        failures: list[str] = []
        # Three-hour increments cover the requested 72-hour and seven-day
        # horizons without treating long-range output as high-resolution truth.
        for hour in range(3, 169, 3):
            try:
                nearest_latitude = round(lake.latitude * 4.0) / 4.0
                nearest_longitude = round(lake.longitude * 4.0) / 4.0
                cache_key = (
                    cycle.isoformat(),
                    hour,
                    nearest_latitude,
                    nearest_longitude,
                )
                decoded = self._decoded_cache.get(cache_key)
                if decoded is None:
                    decoded = self.decoder(
                        self._payload(lake, cycle, hour),
                        lake.latitude,
                        lake.longitude,
                    )
                    self._decoded_cache[cache_key] = decoded
                precipitation, temperature = decoded
                rows.append(
                    ForecastPoint(
                        lake_id=lake.lake_id,
                        forecast_created_at=cycle,
                        valid_at=cycle + timedelta(hours=hour),
                        precipitation_mm=precipitation,
                        temperature_c=temperature,
                        horizon_hours=hour,
                    )
                )
            except (requests.RequestException, ProviderError) as exc:
                failures.append(f"f{hour:03d}: {exc}")
        if not rows:
            detail = failures[0] if failures else "no new cycle"
            raise ProviderError(f"No usable NOAA GFS forecast for {lake.lake_id}: {detail}")
        return rows
