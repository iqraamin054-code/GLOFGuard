from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date

from glofguard.config import PROJECT_ROOT, Settings
from glofguard.providers.nasa_power import NasaPowerProvider
from glofguard.types import Lake


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_params = None

    def get(self, url, params, timeout):
        self.last_params = params
        return FakeResponse(self.payload)


class ProviderTests(unittest.TestCase):
    def test_nasa_power_daily_parser_preserves_dates_and_missing_values(self) -> None:
        payload = {
            "properties": {
                "parameter": {
                    "T2M": {"20250101": -3.2, "20250102": -999},
                    "PRECTOTCORR": {"20250101": 2.5, "20250102": 0.0},
                }
            }
        }
        session = FakeSession(payload)
        settings = Settings.from_env(PROJECT_ROOT / "does-not-exist.env")
        provider = NasaPowerProvider(settings, session=session)
        rows = provider.daily(
            Lake("L1", 36.0, 74.0), date(2025, 1, 1), date(2025, 1, 2)
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].temperature_c, -3.2)
        self.assertIsNone(rows[1].temperature_c)
        self.assertEqual(rows[1].rainfall_mm, 0.0)
        self.assertEqual(session.last_params["time-standard"], "UTC")


if __name__ == "__main__":
    unittest.main()
