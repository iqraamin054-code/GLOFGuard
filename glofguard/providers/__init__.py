"""Official-source ingestion adapters and clearly marked test doubles."""

from .base import ClimateProvider, ForecastProvider, PrecipitationProvider, SatelliteProvider

__all__ = ["SatelliteProvider", "PrecipitationProvider", "ForecastProvider", "ClimateProvider"]
