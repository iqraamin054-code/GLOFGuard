"""Environment-only configuration for authenticated and public data sources."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> None:
    """Load a simple .env file without overwriting existing environment values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in (None, "") else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw in (None, "") else float(raw)


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Credentials are names, never embedded file contents."""

    data_mode: str
    database_path: Path
    output_csv_path: Path
    model_version: str
    baseline_model_path: Path | None
    baseline_preprocessing_path: Path | None
    dynamic_model_path: Path | None
    dynamic_preprocessing_path: Path | None
    model_validated: bool

    gee_project: str | None
    sentinel_lookback_days: int
    sentinel_area_lookback_days: int
    sentinel_search_radius_m: int
    sentinel_cloud_score_threshold: float
    sentinel_max_cloud_percent: float
    sentinel_min_clear_fraction: float
    sentinel_water_index: str
    sentinel_water_threshold: float
    sentinel_max_candidate_scenes: int
    sentinel_area_max_candidate_scenes: int

    gsmap_username: str | None
    gsmap_password: str | None
    gsmap_base_url: str
    gsmap_algorithm_version: str
    gsmap_timeout_seconds: int
    gsmap_cache_dir: Path

    noaa_gfs_filter_url: str
    noaa_timeout_seconds: int
    noaa_cache_dir: Path
    gfs_left_longitude: float
    gfs_right_longitude: float
    gfs_top_latitude: float
    gfs_bottom_latitude: float
    nasa_power_daily_url: str
    nasa_timeout_seconds: int
    nasa_power_baseline_years: int
    observed_weather_min_coverage: float

    satellite_stale_days: int
    weather_stale_hours: int
    forecast_stale_hours: int
    allow_mock_data: bool

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "Settings":
        load_env_file(env_path or PROJECT_ROOT / ".env")
        mode = os.getenv("GLOF_DATA_MODE", "real").strip().lower()
        if mode not in {"real", "mock"}:
            raise ValueError("GLOF_DATA_MODE must be 'real' or 'mock'")

        def optional_path(name: str) -> Path | None:
            value = os.getenv(name, "").strip()
            return Path(value).expanduser().resolve() if value else None

        database = Path(
            os.getenv("GLOF_DATABASE_PATH", str(PROJECT_ROOT / "data" / "glofguard.sqlite3"))
        ).expanduser().resolve()
        output_csv = Path(
            os.getenv(
                "GLOF_TIMESERIES_CSV_PATH",
                str(PROJECT_ROOT / "data" / "lake_observations.csv"),
            )
        ).expanduser().resolve()
        return cls(
            data_mode=mode,
            database_path=database,
            output_csv_path=output_csv,
            model_version=os.getenv(
                "GLOF_MODEL_VERSION", "transparent-conditions-index-v0.1.0"
            ),
            baseline_model_path=optional_path("GLOF_BASELINE_MODEL_PATH"),
            baseline_preprocessing_path=optional_path(
                "GLOF_BASELINE_PREPROCESSING_PATH"
            ),
            dynamic_model_path=optional_path("GLOF_DYNAMIC_MODEL_PATH"),
            dynamic_preprocessing_path=optional_path(
                "GLOF_DYNAMIC_PREPROCESSING_PATH"
            ),
            model_validated=_env_bool("GLOF_MODEL_VALIDATED", False),
            gee_project=os.getenv("GLOF_GEE_PROJECT") or None,
            sentinel_lookback_days=_env_int("GLOF_SENTINEL_LOOKBACK_DAYS", 30),
            sentinel_area_lookback_days=_env_int(
                "GLOF_SENTINEL_AREA_LOOKBACK_DAYS", 180
            ),
            sentinel_search_radius_m=_env_int("GLOF_SENTINEL_SEARCH_RADIUS_M", 1000),
            sentinel_cloud_score_threshold=_env_float(
                "GLOF_SENTINEL_CLOUD_SCORE_THRESHOLD", 0.60
            ),
            sentinel_max_cloud_percent=_env_float(
                "GLOF_SENTINEL_MAX_CLOUD_PERCENT", 35.0
            ),
            sentinel_min_clear_fraction=_env_float(
                "GLOF_SENTINEL_MIN_CLEAR_FRACTION", 0.65
            ),
            sentinel_water_index=os.getenv("GLOF_SENTINEL_WATER_INDEX", "MNDWI").upper(),
            sentinel_water_threshold=_env_float("GLOF_SENTINEL_WATER_THRESHOLD", 0.10),
            sentinel_max_candidate_scenes=_env_int(
                "GLOF_SENTINEL_MAX_CANDIDATE_SCENES", 12
            ),
            sentinel_area_max_candidate_scenes=_env_int(
                "GLOF_SENTINEL_AREA_MAX_CANDIDATE_SCENES", 60
            ),
            gsmap_username=os.getenv("GLOF_GSMAP_USERNAME") or None,
            gsmap_password=os.getenv("GLOF_GSMAP_PASSWORD") or None,
            gsmap_base_url=os.getenv("GLOF_GSMAP_BASE_URL", "").rstrip("/"),
            gsmap_algorithm_version=os.getenv("GLOF_GSMAP_VERSION", "08"),
            gsmap_timeout_seconds=_env_int("GLOF_GSMAP_TIMEOUT_SECONDS", 60),
            gsmap_cache_dir=Path(
                os.getenv(
                    "GLOF_GSMAP_CACHE_DIR", str(PROJECT_ROOT / "data" / "cache" / "gsmap")
                )
            ).expanduser().resolve(),
            noaa_gfs_filter_url=os.getenv(
                "GLOF_NOAA_GFS_FILTER_URL",
                "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl",
            ),
            noaa_timeout_seconds=_env_int("GLOF_NOAA_TIMEOUT_SECONDS", 90),
            noaa_cache_dir=Path(
                os.getenv(
                    "GLOF_NOAA_CACHE_DIR", str(PROJECT_ROOT / "data" / "cache" / "noaa_gfs")
                )
            ).expanduser().resolve(),
            gfs_left_longitude=_env_float("GLOF_GFS_LEFT_LONGITUDE", 60.0),
            gfs_right_longitude=_env_float("GLOF_GFS_RIGHT_LONGITUDE", 80.0),
            gfs_top_latitude=_env_float("GLOF_GFS_TOP_LATITUDE", 38.0),
            gfs_bottom_latitude=_env_float("GLOF_GFS_BOTTOM_LATITUDE", 23.0),
            nasa_power_daily_url=os.getenv(
                "GLOF_NASA_POWER_DAILY_URL",
                "https://power.larc.nasa.gov/api/temporal/daily/point",
            ),
            nasa_timeout_seconds=_env_int("GLOF_NASA_TIMEOUT_SECONDS", 60),
            nasa_power_baseline_years=_env_int(
                "GLOF_NASA_POWER_BASELINE_YEARS", 10
            ),
            observed_weather_min_coverage=_env_float(
                "GLOF_OBSERVED_WEATHER_MIN_COVERAGE", 0.80
            ),
            satellite_stale_days=_env_int("GLOF_SATELLITE_STALE_DAYS", 20),
            weather_stale_hours=_env_int("GLOF_WEATHER_STALE_HOURS", 36),
            forecast_stale_hours=_env_int("GLOF_FORECAST_STALE_HOURS", 12),
            allow_mock_data=_env_bool("GLOF_ALLOW_MOCK_DATA", False),
        )

    def validate(self) -> None:
        if self.data_mode == "mock" and not self.allow_mock_data:
            raise ValueError(
                "Mock mode is disabled. Set GLOF_ALLOW_MOCK_DATA=true explicitly; "
                "mock results are for interface tests only."
            )
        if self.sentinel_water_index not in {"NDWI", "MNDWI"}:
            raise ValueError("GLOF_SENTINEL_WATER_INDEX must be NDWI or MNDWI")
        if not 0 <= self.sentinel_cloud_score_threshold <= 1:
            raise ValueError("Cloud Score+ threshold must be between 0 and 1")
        if not 0 <= self.sentinel_min_clear_fraction <= 1:
            raise ValueError("Minimum clear fraction must be between 0 and 1")
        if self.sentinel_area_lookback_days < self.sentinel_lookback_days:
            raise ValueError(
                "Sentinel area lookback must be at least the recent diagnostic window"
            )
        if self.sentinel_area_max_candidate_scenes < self.sentinel_max_candidate_scenes:
            raise ValueError(
                "Extended Sentinel area candidate cap must be at least the recent cap"
            )
        if self.nasa_power_baseline_years < 3:
            raise ValueError("NASA POWER baseline must include at least three years")
        if not 0 < self.observed_weather_min_coverage <= 1:
            raise ValueError("Observed-weather minimum coverage must be in (0, 1]")
        if self.model_validated and not (
            self.baseline_model_path or self.dynamic_model_path
        ):
            raise ValueError(
                "GLOF_MODEL_VALIDATED=true requires at least one saved classifier path"
            )
        if self.gfs_left_longitude >= self.gfs_right_longitude:
            raise ValueError("GFS left longitude must be less than right longitude")
        if self.gfs_bottom_latitude >= self.gfs_top_latitude:
            raise ValueError("GFS bottom latitude must be less than top latitude")
