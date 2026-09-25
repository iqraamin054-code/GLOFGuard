"""Export non-secret REAL training/reference inputs from local SQLite.

The source database is opened immutable/read-only. Operational error logs,
outbox payloads, and credentials are intentionally not exported.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "glofguard.sqlite3"
DESTINATION = ROOT / "data" / "training"
TABLES = {
    "lakes": "canonical_lakes.csv",
    "daily_weather": "real_daily_weather.csv",
    "hourly_precipitation": "real_hourly_precipitation.csv",
    "forecasts": "real_forecasts.csv",
    "satellite_observations": "real_satellite_observations.csv",
    "time_series_records": "real_time_series_records.csv",
}


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit("Source SQLite database is absent; no export created.")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    uri = "file:" + SOURCE.resolve().as_posix() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        for table, filename in TABLES.items():
            columns = [row[1] for row in connection.execute("PRAGMA table_info(" + quote(table) + ")")]
            if not columns:
                raise SystemExit("Required source table is absent: " + table)
            query = "SELECT " + ", ".join(quote(column) for column in columns) + " FROM " + quote(table)
            if table in {"daily_weather", "hourly_precipitation", "forecasts", "satellite_observations"}:
                query += " WHERE is_mock = 0"
            query += " ORDER BY rowid"
            with (DESTINATION / filename).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerows(connection.execute(query))


if __name__ == "__main__":
    main()
