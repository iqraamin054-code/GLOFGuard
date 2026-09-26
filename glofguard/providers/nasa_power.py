"""NASA POWER historical daily weather adapter."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

import requests

from ..config import Settings
from ..types import DailyWeather, Lake
from .base import ProviderError


class NasaPowerProvider:
    PARAMETERS = "T2M,PRECTOTCORR"

    def __init__(
        self,
        settings: Settings,
        session: Any | None = None,
        before_remote_call: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.before_remote_call = before_remote_call or (lambda: None)
        self.request_count = 0

    def daily(self, lake: Lake, start: date, end: date) -> list[DailyWeather]:
        if start > end:
            raise ValueError("NASA POWER start date must not be after end date")
        params = {
            "parameters": self.PARAMETERS,
            "community": "AG",
            "longitude": lake.longitude,
            "latitude": lake.latitude,
            "start": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
            "format": "JSON",
            "time-standard": "UTC",
        }
        try:
            self.before_remote_call()
            self.request_count += 1
            response = self.session.get(
                self.settings.nasa_power_daily_url,
                params=params,
                timeout=self.settings.nasa_timeout_seconds,
            )
            response.raise_for_status()
            parameter = response.json()["properties"]["parameter"]
            temperatures = parameter["T2M"]
            rainfall = parameter["PRECTOTCORR"]
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"NASA POWER daily request failed for {lake.lake_id}") from exc

        rows: list[DailyWeather] = []
        for day_key in sorted(set(temperatures) | set(rainfall)):
            try:
                observation_date = datetime.strptime(day_key, "%Y%m%d").date()
            except ValueError:
                continue
            temperature = temperatures.get(day_key)
            precipitation = rainfall.get(day_key)
            temperature_value = None if temperature in (None, -999) else float(temperature)
            rain_value = None if precipitation in (None, -999) else float(precipitation)
            rows.append(
                DailyWeather(
                    lake_id=lake.lake_id,
                    observation_date=observation_date,
                    temperature_c=temperature_value,
                    rainfall_mm=rain_value,
                )
            )
        return rows
