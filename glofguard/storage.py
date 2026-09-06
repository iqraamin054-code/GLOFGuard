"""Append-safe SQLite persistence for source data, failures, and indicators."""

from __future__ import annotations

import csv
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable

from .schemas import TIME_SERIES_COLUMNS, TimeSeriesRecord
from .types import DailyWeather, ForecastPoint, HourlyPrecipitation, Lake, QualityStatus, SatelliteObservation


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS lakes (
    lake_id TEXT PRIMARY KEY,
    name TEXT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    elevation_m REAL,
    distance_to_nearest_settlement_km REAL,
    reference_area_km2 REAL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS official_lakes (
    lake_id TEXT PRIMARY KEY,
    official_source_id TEXT NOT NULL,
    name TEXT,
    basin TEXT,
    lake_type TEXT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    elevation_m REAL,
    distance_to_nearest_settlement_km REAL,
    reference_area_km2 REAL,
    matched_source_lake_id TEXT,
    match_distance_m REAL,
    match_method TEXT,
    matched_source_count INTEGER NOT NULL DEFAULT 0,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory_reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    official_source_path TEXT NOT NULL,
    official_source_sha256 TEXT NOT NULL,
    existing_source_path TEXT NOT NULL,
    existing_source_sha256 TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inventory_matches (
    reconciliation_id TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    source_lake_id TEXT NOT NULL,
    source_system_lake_id TEXT,
    status TEXT NOT NULL,
    official_lake_id TEXT,
    match_method TEXT,
    match_distance_m REAL,
    duplicate_of_source_lake_id TEXT,
    details_json TEXT NOT NULL,
    PRIMARY KEY (reconciliation_id, source_row_number),
    FOREIGN KEY (reconciliation_id) REFERENCES inventory_reconciliations(reconciliation_id)
);
CREATE TABLE IF NOT EXISTS satellite_observations (
    lake_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    area_km2 REAL,
    cloud_percentage REAL NOT NULL,
    clear_fraction REAL NOT NULL,
    quality_status TEXT NOT NULL,
    source_image_id TEXT NOT NULL,
    water_index TEXT NOT NULL,
    source TEXT NOT NULL,
    is_mock INTEGER NOT NULL,
    rejection_reason TEXT,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, observed_at, source_image_id)
);
CREATE TABLE IF NOT EXISTS hourly_precipitation (
    lake_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    rainfall_mm REAL NOT NULL,
    source TEXT NOT NULL,
    is_mock INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, observed_at, source)
);
CREATE TABLE IF NOT EXISTS daily_weather (
    lake_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    temperature_c REAL,
    rainfall_mm REAL,
    source TEXT NOT NULL,
    is_mock INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, observation_date, source)
);
CREATE TABLE IF NOT EXISTS forecasts (
    lake_id TEXT NOT NULL,
    forecast_created_at TEXT NOT NULL,
    valid_at TEXT NOT NULL,
    precipitation_mm REAL,
    temperature_c REAL,
    horizon_hours INTEGER NOT NULL,
    source TEXT NOT NULL,
    is_mock INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, forecast_created_at, valid_at, source)
);
CREATE TABLE IF NOT EXISTS time_series_records (
    lake_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    input_signature TEXT NOT NULL,
    record_json TEXT NOT NULL,
    prediction_timestamp TEXT,
    model_version TEXT NOT NULL,
    risk_score REAL,
    risk_level TEXT,
    data_quality_status TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    PRIMARY KEY (lake_id, observation_date, input_signature)
);
CREATE TABLE IF NOT EXISTS ingestion_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lake_id TEXT,
    source TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS monitoring_snapshots (
    created_at TEXT PRIMARY KEY,
    metrics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS full_inventory_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS full_inventory_lake_status (
    run_id TEXT NOT NULL,
    lake_id TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    status_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, lake_id)
);
CREATE TABLE IF NOT EXISTS full_inventory_error_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    lake_id TEXT NOT NULL,
    source TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS environmental_source_snapshots (
    lake_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    source_mode TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (lake_id, source_type, source_timestamp, source)
);
"""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def connection(self):
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA_SQL)

    def upsert_lakes(self, lakes: Iterable[Lake]) -> None:
        now = _utc_now()
        rows = [
            (
                lake.lake_id,
                lake.name,
                lake.latitude,
                lake.longitude,
                lake.elevation_m,
                lake.distance_to_nearest_settlement_km,
                lake.reference_area_km2,
                now,
            )
            for lake in lakes
        ]
        with self.connection() as connection:
            connection.executemany(
                """INSERT INTO lakes VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lake_id) DO UPDATE SET
                    name=excluded.name, latitude=excluded.latitude,
                    longitude=excluded.longitude, elevation_m=excluded.elevation_m,
                    distance_to_nearest_settlement_km=excluded.distance_to_nearest_settlement_km,
                    reference_area_km2=excluded.reference_area_km2,
                    updated_at=excluded.updated_at""",
                rows,
            )

    def list_lakes(self, lake_ids: list[str] | None = None) -> list[Lake]:
        with self.connection() as connection:
            official_count = int(
                connection.execute("SELECT COUNT(*) FROM official_lakes").fetchone()[0]
            )
            table = "official_lakes" if official_count else "lakes"
            if lake_ids:
                placeholders = ",".join("?" for _ in lake_ids)
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE lake_id IN ({placeholders}) ORDER BY lake_id",
                    lake_ids,
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT * FROM {table} ORDER BY lake_id"
                ).fetchall()
        return [
            Lake(
                lake_id=row["lake_id"],
                name=row["name"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                elevation_m=row["elevation_m"],
                distance_to_nearest_settlement_km=row[
                    "distance_to_nearest_settlement_km"
                ],
                reference_area_km2=row["reference_area_km2"],
            )
            for row in rows
        ]

    def list_source_lakes(self, lake_ids: list[str] | None = None) -> list[Lake]:
        """Return the 2020 processed source inventory even if a PMD master is active."""
        with self.connection() as connection:
            if lake_ids:
                placeholders = ",".join("?" for _ in lake_ids)
                rows = connection.execute(
                    f"SELECT * FROM lakes WHERE lake_id IN ({placeholders}) ORDER BY lake_id",
                    lake_ids,
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM lakes ORDER BY lake_id").fetchall()
        return [
            Lake(
                lake_id=row["lake_id"],
                name=row["name"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                elevation_m=row["elevation_m"],
                distance_to_nearest_settlement_km=row[
                    "distance_to_nearest_settlement_km"
                ],
                reference_area_km2=row["reference_area_km2"],
            )
            for row in rows
        ]

    def backup_to(self, destination: Path) -> None:
        """Create a consistent SQLite backup without modifying the live database."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def official_lake_count(self) -> int:
        with self.connection() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM official_lakes").fetchone()[0]
            )

    def replace_official_inventory(
        self,
        master_rows: Iterable[dict[str, object]],
        match_rows: Iterable[dict[str, object]],
        reconciliation: dict[str, object],
    ) -> None:
        """Atomically replace the validated official master and its match audit."""
        masters = list(master_rows)
        matches = list(match_rows)
        reconciliation_id = str(reconciliation["reconciliation_id"])
        imported_at = str(reconciliation["created_at"])
        with self.connection() as connection:
            connection.execute("DELETE FROM official_lakes")
            connection.executemany(
                """INSERT INTO official_lakes VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        row["official_lake_id"],
                        row["official_source_id"],
                        row.get("lake_name"),
                        row.get("basin"),
                        row.get("lake_type"),
                        row["latitude"],
                        row["longitude"],
                        row.get("elevation_m"),
                        row.get("distance_to_nearest_settlement_km"),
                        row.get("reference_area_km2"),
                        row.get("matched_source_lake_id"),
                        row.get("match_distance_m"),
                        row.get("match_method"),
                        int(row.get("matched_source_count") or 0),
                        reconciliation["official_source_path"],
                        reconciliation["official_source_sha256"],
                        imported_at,
                    )
                    for row in masters
                ],
            )
            connection.execute(
                """INSERT OR REPLACE INTO inventory_reconciliations VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    reconciliation_id,
                    imported_at,
                    reconciliation["official_source_path"],
                    reconciliation["official_source_sha256"],
                    reconciliation["existing_source_path"],
                    reconciliation["existing_source_sha256"],
                    json.dumps(reconciliation["parameters"], separators=(",", ":")),
                    json.dumps(reconciliation["summary"], separators=(",", ":")),
                ),
            )
            connection.executemany(
                """INSERT OR REPLACE INTO inventory_matches VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        reconciliation_id,
                        int(row["source_row_number"]),
                        row["source_lake_id"],
                        row.get("source_system_lake_id"),
                        row["status"],
                        row.get("official_lake_id"),
                        row.get("match_method"),
                        row.get("match_distance_m"),
                        row.get("duplicate_of_source_lake_id"),
                        json.dumps(row, separators=(",", ":"), allow_nan=False),
                    )
                    for row in matches
                ],
            )

    def store_satellite(self, observation: SatelliteObservation) -> None:
        values = asdict(observation)
        with self.connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO satellite_observations VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation.lake_id,
                    observation.observed_at.isoformat(),
                    observation.area_km2,
                    observation.cloud_percentage,
                    observation.clear_fraction,
                    observation.quality_status.value,
                    observation.source_image_id,
                    observation.water_index,
                    observation.source,
                    int(observation.is_mock),
                    observation.rejection_reason,
                    _utc_now(),
                ),
            )

    def satellite_history(self, lake_id: str) -> list[SatelliteObservation]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT * FROM satellite_observations
                WHERE lake_id=? AND area_km2 IS NOT NULL
                  AND quality_status IN ('RELIABLE', 'MOCK')
                ORDER BY observed_at""",
                (lake_id,),
            ).fetchall()
        return [
            SatelliteObservation(
                lake_id=row["lake_id"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                area_km2=row["area_km2"],
                cloud_percentage=row["cloud_percentage"],
                clear_fraction=row["clear_fraction"],
                quality_status=QualityStatus(row["quality_status"]),
                source_image_id=row["source_image_id"],
                water_index=row["water_index"],
                source=row["source"],
                is_mock=bool(row["is_mock"]),
                rejection_reason=row["rejection_reason"],
            )
            for row in rows
        ]

    def latest_satellite_attempt(
        self, lake_id: str, *, real_only: bool = False
    ) -> SatelliteObservation | None:
        query = """SELECT * FROM satellite_observations WHERE lake_id=?"""
        parameters: list[object] = [lake_id]
        if real_only:
            query += " AND is_mock=0"
        query += " ORDER BY observed_at DESC, ingested_at DESC LIMIT 1"
        with self.connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        if row is None:
            return None
        return SatelliteObservation(
            lake_id=row["lake_id"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            area_km2=row["area_km2"],
            cloud_percentage=row["cloud_percentage"],
            clear_fraction=row["clear_fraction"],
            quality_status=QualityStatus(row["quality_status"]),
            source_image_id=row["source_image_id"],
            water_index=row["water_index"],
            source=row["source"],
            is_mock=bool(row["is_mock"]),
            rejection_reason=row["rejection_reason"],
        )

    def latest_source_times(self, lake_id: str) -> dict[str, datetime | date | None]:
        with self.connection() as connection:
            satellite = connection.execute(
                """SELECT MAX(observed_at) AS value FROM satellite_observations
                WHERE lake_id=? AND area_km2 IS NOT NULL
                  AND quality_status IN ('RELIABLE', 'MOCK')""",
                (lake_id,),
            ).fetchone()["value"]
            precipitation = connection.execute(
                "SELECT MAX(observed_at) AS value FROM hourly_precipitation WHERE lake_id=?",
                (lake_id,),
            ).fetchone()["value"]
            weather = connection.execute(
                "SELECT MAX(observation_date) AS value FROM daily_weather WHERE lake_id=?",
                (lake_id,),
            ).fetchone()["value"]
            forecast = connection.execute(
                "SELECT MAX(forecast_created_at) AS value FROM forecasts WHERE lake_id=?",
                (lake_id,),
            ).fetchone()["value"]
        return {
            "satellite": datetime.fromisoformat(satellite) if satellite else None,
            "precipitation": datetime.fromisoformat(precipitation) if precipitation else None,
            "weather": date.fromisoformat(weather) if weather else None,
            "forecast": datetime.fromisoformat(forecast) if forecast else None,
        }

    def store_hourly_precipitation(self, rows: Iterable[HourlyPrecipitation]) -> None:
        values = [
            (
                row.lake_id,
                row.observed_at.isoformat(),
                row.rainfall_mm,
                row.source,
                int(row.is_mock),
                _utc_now(),
            )
            for row in rows
        ]
        with self.connection() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO hourly_precipitation VALUES (?, ?, ?, ?, ?, ?)",
                values,
            )

    def precipitation_since(self, lake_id: str, since: datetime) -> list[HourlyPrecipitation]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT * FROM hourly_precipitation
                WHERE lake_id=? AND observed_at>=? ORDER BY observed_at""",
                (lake_id, since.isoformat()),
            ).fetchall()
        return [
            HourlyPrecipitation(
                lake_id=row["lake_id"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                rainfall_mm=row["rainfall_mm"],
                source=row["source"],
                is_mock=bool(row["is_mock"]),
            )
            for row in rows
        ]

    def store_daily_weather(self, rows: Iterable[DailyWeather]) -> None:
        values = [
            (
                row.lake_id,
                row.observation_date.isoformat(),
                row.temperature_c,
                row.rainfall_mm,
                row.source,
                int(row.is_mock),
                _utc_now(),
            )
            for row in rows
        ]
        with self.connection() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO daily_weather VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def daily_weather(self, lake_id: str) -> list[DailyWeather]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM daily_weather WHERE lake_id=? ORDER BY observation_date",
                (lake_id,),
            ).fetchall()
        return [
            DailyWeather(
                lake_id=row["lake_id"],
                observation_date=date.fromisoformat(row["observation_date"]),
                temperature_c=row["temperature_c"],
                rainfall_mm=row["rainfall_mm"],
                source=row["source"],
                is_mock=bool(row["is_mock"]),
            )
            for row in rows
        ]

    def store_forecasts(self, rows: Iterable[ForecastPoint]) -> None:
        values = [
            (
                row.lake_id,
                row.forecast_created_at.isoformat(),
                row.valid_at.isoformat(),
                row.precipitation_mm,
                row.temperature_c,
                row.horizon_hours,
                row.source,
                int(row.is_mock),
                _utc_now(),
            )
            for row in rows
        ]
        with self.connection() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def latest_forecast(self, lake_id: str) -> list[ForecastPoint]:
        with self.connection() as connection:
            latest = connection.execute(
                "SELECT MAX(forecast_created_at) AS created FROM forecasts WHERE lake_id=?",
                (lake_id,),
            ).fetchone()["created"]
            if latest is None:
                return []
            rows = connection.execute(
                """SELECT * FROM forecasts WHERE lake_id=? AND forecast_created_at=?
                ORDER BY valid_at""",
                (lake_id, latest),
            ).fetchall()
        return [
            ForecastPoint(
                lake_id=row["lake_id"],
                forecast_created_at=datetime.fromisoformat(row["forecast_created_at"]),
                valid_at=datetime.fromisoformat(row["valid_at"]),
                precipitation_mm=row["precipitation_mm"],
                temperature_c=row["temperature_c"],
                horizon_hours=row["horizon_hours"],
                source=row["source"],
                is_mock=bool(row["is_mock"]),
            )
            for row in rows
        ]

    def save_record_if_changed(self, record: TimeSeriesRecord) -> bool:
        payload = record.to_dict()
        with self.connection() as connection:
            latest = connection.execute(
                """SELECT input_signature FROM time_series_records
                WHERE lake_id=? AND observation_date=? ORDER BY rowid DESC LIMIT 1""",
                (record.lake_id, record.observation_date.isoformat()),
            ).fetchone()
            if latest and latest["input_signature"] == record.input_signature:
                return False
            connection.execute(
                "INSERT INTO time_series_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.lake_id,
                    record.observation_date.isoformat(),
                    record.input_signature,
                    json.dumps(payload, separators=(",", ":"), allow_nan=False),
                    record.prediction_timestamp.isoformat()
                    if record.prediction_timestamp
                    else None,
                    record.model_version,
                    record.risk_score,
                    record.risk_level,
                    record.data_quality_status,
                    record.source_mode,
                ),
            )
        return True

    def records(self) -> list[dict[str, object]]:
        """Return one canonical latest revision per lake and observation date.

        Prior same-day revisions remain in SQLite for auditability, but are not
        duplicated in the canonical one-row-per-lake-per-date dataset.
        """
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT record_json FROM time_series_records
                WHERE rowid IN (
                    SELECT MAX(rowid) FROM time_series_records
                    GROUP BY lake_id, observation_date
                )
                ORDER BY observation_date, lake_id"""
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def export_records_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.records()
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=TIME_SERIES_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def record_failure(self, lake_id: str | None, source: str, error: Exception) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO ingestion_failures
                (lake_id, source, attempted_at, error_type, error_message)
                VALUES (?, ?, ?, ?, ?)""",
                (lake_id, source, _utc_now(), type(error).__name__, str(error)[:2000]),
            )

    def failure_summary(self) -> dict[str, int]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT source, COUNT(*) AS failure_count
                FROM ingestion_failures WHERE resolved=0 GROUP BY source ORDER BY source"""
            ).fetchall()
        return {str(row["source"]): int(row["failure_count"]) for row in rows}

    def store_monitoring_snapshot(self, metrics: dict[str, object]) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO monitoring_snapshots VALUES (?, ?)",
                (_utc_now(), json.dumps(metrics, separators=(",", ":"), allow_nan=False)),
            )

    def start_full_inventory_run(
        self, run_id: str, config: dict[str, object], *, status: str = "RUNNING"
    ) -> None:
        now = _utc_now()
        payload = json.dumps(config, separators=(",", ":"), allow_nan=False)
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT config_json FROM full_inventory_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is not None and existing["config_json"] != payload:
                raise ValueError(
                    f"Checkpoint run {run_id} exists with different configuration"
                )
            connection.execute(
                """INSERT INTO full_inventory_runs VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET updated_at=excluded.updated_at,
                status=excluded.status""",
                (run_id, now, now, payload, status),
            )

    def set_full_inventory_run_status(self, run_id: str, status: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE full_inventory_runs SET updated_at=?, status=? WHERE run_id=?",
                (_utc_now(), status, run_id),
            )

    def upsert_full_inventory_statuses(
        self, run_id: str, rows: Iterable[dict[str, object]]
    ) -> None:
        now = _utc_now()
        values = []
        for row in rows:
            values.append(
                (
                    run_id,
                    str(row["lake_id"]),
                    str(row["processing_status"]),
                    json.dumps(row, separators=(",", ":"), allow_nan=False),
                    now,
                )
            )
        with self.connection() as connection:
            connection.executemany(
                """INSERT INTO full_inventory_lake_status VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, lake_id) DO UPDATE SET
                    processing_status=excluded.processing_status,
                    status_json=excluded.status_json,
                    updated_at=excluded.updated_at""",
                values,
            )

    def full_inventory_statuses(self, run_id: str) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT status_json FROM full_inventory_lake_status
                WHERE run_id=? ORDER BY lake_id""",
                (run_id,),
            ).fetchall()
        return [json.loads(row["status_json"]) for row in rows]

    def store_full_inventory_error(self, event: dict[str, object]) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO full_inventory_error_events VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_id"],
                    event["run_id"],
                    event["lake_id"],
                    event["source"],
                    event["attempt_number"],
                    event["occurred_at"],
                    event["error_type"],
                    event["error_message"],
                ),
            )

    def full_inventory_errors(self, run_id: str) -> list[dict[str, object]]:
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT * FROM full_inventory_error_events
                WHERE run_id=? ORDER BY occurred_at, lake_id, source, attempt_number""",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def store_environmental_source_snapshot(
        self,
        *,
        lake_id: str,
        source_type: str,
        source_timestamp: str,
        source: str,
        source_mode: str,
        payload: dict[str, object],
    ) -> None:
        """Idempotently retain a source-specific REAL summary and its retrieval time."""
        if source_mode != "REAL":
            raise ValueError("Environmental source snapshots must be REAL")
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO environmental_source_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lake_id, source_type, source_timestamp, source) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    retrieved_at=excluded.retrieved_at""",
                (
                    lake_id,
                    source_type,
                    source_timestamp,
                    source,
                    source_mode,
                    json.dumps(payload, separators=(",", ":"), allow_nan=False),
                    _utc_now(),
                ),
            )

    def latest_environmental_source_snapshot(
        self, lake_id: str, source_type: str
    ) -> dict[str, object] | None:
        with self.connection() as connection:
            row = connection.execute(
                """SELECT * FROM environmental_source_snapshots
                WHERE lake_id=? AND source_type=? AND source_mode='REAL'
                ORDER BY source_timestamp DESC LIMIT 1""",
                (lake_id, source_type),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(str(result.pop("payload_json")))
        return result
