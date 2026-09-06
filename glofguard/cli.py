"""Command-line interface for baseline and dynamic GLOF indicators."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Sequence

from .config import PROJECT_ROOT, Settings
from .monitoring import build_monitoring_snapshot
from .pipeline import RefreshPipeline
from .providers.base import DisabledProvider
from .providers.gee_weather import EarthEngineGfsProvider, EarthEngineGsmapProvider
from .providers.mock import MockGfsProvider, MockGsmapProvider, MockPowerProvider, MockSentinel2Provider
from .providers.nasa_power import NasaPowerProvider
from .providers.sentinel2 import EarthEngineSentinel2Provider
from .schemas import SAFETY_NOTICE
from .scoring import BaselineSusceptibilityIndex
from .storage import Repository
from .types import Lake
from .validation import validate_lake


def load_baseline_lakes(path: Path) -> list[Lake]:
    """Import static lake fields without importing proxy labels as outcomes."""
    import pandas as pd

    frame = pd.read_csv(path)
    latitude = "latitude"
    longitude = "longitude"
    if latitude not in frame or longitude not in frame:
        raise ValueError("Baseline CSV needs latitude and longitude columns")
    area_column = "area" if "area" in frame else "area_km2" if "area_km2" in frame else None
    id_column = "lake_id" if "lake_id" in frame else "sample_id" if "sample_id" in frame else None
    elevation_column = "elevation" if "elevation" in frame else None
    settlement_column = (
        "distance_to_nearest_settlement_km"
        if "distance_to_nearest_settlement_km" in frame
        else None
    )
    name_column = "lake_name" if "lake_name" in frame else None
    lakes: list[Lake] = []
    for index, row in frame.iterrows():
        raw_id = row[id_column] if id_column else index + 1
        lake_id = str(raw_id) if id_column == "lake_id" else f"PKGL-{int(raw_id):05d}"
        lake = Lake(
            lake_id=lake_id,
            name=str(row[name_column]) if name_column and pd.notna(row[name_column]) else None,
            latitude=float(row[latitude]),
            longitude=float(row[longitude]),
            elevation_m=(
                float(row[elevation_column])
                if elevation_column and pd.notna(row[elevation_column])
                else None
            ),
            distance_to_nearest_settlement_km=(
                float(row[settlement_column])
                if settlement_column and pd.notna(row[settlement_column])
                else None
            ),
            reference_area_km2=(
                float(row[area_column]) if area_column and pd.notna(row[area_column]) else None
            ),
        )
        validate_lake(lake)
        lakes.append(lake)
    return lakes


def write_baseline_output(repository: Repository, path: Path) -> int:
    lakes = repository.list_lakes()
    index = BaselineSusceptibilityIndex(lakes)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "lake_id",
        "latitude",
        "longitude",
        "baseline_susceptibility_score",
        "baseline_susceptibility_level",
        "missing_static_fields",
        "interpretation",
        "safety_notice",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for lake in lakes:
            score, level, missing = index.score(lake)
            writer.writerow(
                {
                    "lake_id": lake.lake_id,
                    "latitude": lake.latitude,
                    "longitude": lake.longitude,
                    "baseline_susceptibility_score": score,
                    "baseline_susceptibility_level": level,
                    "missing_static_fields": "; ".join(missing),
                    "interpretation": (
                        "Relative monitoring-priority index; not a probability or event forecast"
                    ),
                    "safety_notice": SAFETY_NOTICE,
                }
            )
    return len(lakes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=PROJECT_ROOT / ".env")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init-db", help="create the append-safe data store")
    init_parser.add_argument("--database", type=Path)

    import_parser = sub.add_parser(
        "import-baseline", help="import only static fields from the legacy CSV"
    )
    import_parser.add_argument(
        "--input", type=Path, default=PROJECT_ROOT / "glofguard_model_ready.csv"
    )
    import_parser.add_argument("--database", type=Path)

    reconcile_parser = sub.add_parser(
        "reconcile-inventory",
        help="make a validated 3,044-row PMD file the master and audit the 2020 rows",
    )
    reconcile_parser.add_argument(
        "--official",
        type=Path,
        required=True,
        help="official row-level PMD inventory (CSV or supported GIS file)",
    )
    reconcile_parser.add_argument(
        "--existing", type=Path, default=PROJECT_ROOT / "glofguard_model_ready.csv"
    )
    reconcile_parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "output" / "inventory"
    )
    reconcile_parser.add_argument("--database", type=Path)
    reconcile_parser.add_argument(
        "--time-series", type=Path, default=PROJECT_ROOT / "data" / "lake_observations.csv"
    )
    reconcile_parser.add_argument("--expected-count", type=int, default=3044)
    reconcile_parser.add_argument("--max-distance-m", type=float, default=750.0)
    reconcile_parser.add_argument("--ambiguity-margin-m", type=float, default=100.0)
    reconcile_parser.add_argument("--duplicate-distance-m", type=float, default=50.0)
    reconcile_parser.add_argument("--official-id-column")
    reconcile_parser.add_argument("--source-id-column")
    reconcile_parser.add_argument("--source-official-id-column")

    baseline_parser = sub.add_parser(
        "baseline", help="write the separate static monitoring-priority output"
    )
    baseline_parser.add_argument("--database", type=Path)
    baseline_parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "data" / "baseline_susceptibility.csv"
    )

    refresh_parser = sub.add_parser("refresh", help="refresh and recalculate dynamic indicators")
    refresh_parser.add_argument("--database", type=Path)
    refresh_parser.add_argument(
        "--mock",
        action="store_true",
        help="use clearly marked deterministic test data in a separate mock database",
    )
    refresh_parser.add_argument(
        "--lake-id",
        action="append",
        help="refresh only this lake ID; repeat for a few lakes",
    )
    refresh_parser.add_argument(
        "--sources",
        default="sentinel,gsmap,gfs,power",
        help="comma-separated real sources: sentinel, gsmap, gfs, power",
    )

    monitor_parser = sub.add_parser("monitor", help="calculate monitoring metrics")
    monitor_parser.add_argument("--database", type=Path)
    monitor_parser.add_argument("--verified-labels", type=Path)
    monitor_parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "data" / "monitoring_latest.json"
    )

    full_parser = sub.add_parser(
        "full-inventory",
        help=(
            "run checkpointed REAL Sentinel-2, GSMaP, GFS, and NASA POWER "
            "inventory ingestion"
        ),
    )
    full_parser.add_argument(
        "--source-archive", type=Path, default=PROJECT_ROOT / "HKH-PK.zip"
    )
    full_parser.add_argument(
        "--processed", type=Path, default=PROJECT_ROOT / "glofguard_model_ready.csv"
    )
    full_parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "output" / "full_inventory"
    )
    full_parser.add_argument("--database", type=Path)
    full_parser.add_argument("--as-of-date", type=date.fromisoformat, default=date.today())
    full_parser.add_argument("--batch-size", type=int, default=5)
    full_parser.add_argument(
        "--max-lakes",
        type=int,
        default=5,
        help="maximum lakes in this invocation; default is a safe five-lake batch",
    )
    full_parser.add_argument(
        "--all-lakes",
        action="store_true",
        help="remove the safe per-invocation limit (requires --confirm-full-run)",
    )
    full_parser.add_argument(
        "--confirm-full-run",
        action="store_true",
        help="confirm that API usage/quota implications were reviewed",
    )
    full_parser.add_argument(
        "--representative-pilot-size",
        type=int,
        help=(
            "run only a deterministic geography/elevation/area-stratified pilot; "
            "the requested representative pilot uses 100"
        ),
    )
    full_parser.add_argument(
        "--pilot-seed", type=int, default=20260901
    )
    full_parser.add_argument("--rate-limit-seconds", type=float, default=0.5)
    full_parser.add_argument("--max-retries", type=int, default=3)
    full_parser.add_argument("--backoff-base-seconds", type=float, default=2.0)
    full_parser.add_argument("--backoff-cap-seconds", type=float, default=30.0)
    full_parser.add_argument("--power-lookback-days", type=int, default=30)
    full_parser.add_argument(
        "--no-retry-failed",
        action="store_true",
        help="leave FAILED checkpoints untouched in this invocation",
    )
    full_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="reconcile and estimate calls without authentication or provider requests",
    )
    return parser


def _repository(settings: Settings, override: Path | None) -> Repository:
    repository = Repository((override or settings.database_path).resolve())
    repository.initialize()
    return repository


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env(args.env)
        if args.command == "init-db":
            repository = _repository(settings, args.database)
            print(f"Initialized {repository.path}")
        elif args.command == "import-baseline":
            repository = _repository(settings, args.database)
            lakes = load_baseline_lakes(args.input.resolve())
            repository.upsert_lakes(lakes)
            print(f"Imported {len(lakes)} baseline lakes without proxy targets")
        elif args.command == "reconcile-inventory":
            # SciPy/GeoPandas are inventory-only dependencies; avoid loading
            # them during normal daily refresh and monitoring commands.
            from .inventory import run_reconciliation

            repository = _repository(settings, args.database)
            summary = run_reconciliation(
                args.official,
                args.existing,
                args.output_dir.resolve(),
                repository=repository,
                time_series_path=args.time_series.resolve(),
                expected_count=args.expected_count,
                max_distance_m=args.max_distance_m,
                ambiguity_margin_m=args.ambiguity_margin_m,
                duplicate_distance_m=args.duplicate_distance_m,
                official_id_column=args.official_id_column,
                source_id_column=args.source_id_column,
                source_official_id_column=args.source_official_id_column,
            )
            print(json.dumps(summary, indent=2))
        elif args.command == "baseline":
            repository = _repository(settings, args.database)
            count = write_baseline_output(repository, args.output.resolve())
            print(f"Wrote {count} baseline susceptibility rows to {args.output.resolve()}")
        elif args.command == "refresh":
            if args.mock:
                mock_database = (
                    args.database.resolve()
                    if args.database
                    else PROJECT_ROOT / "data" / "mock" / "glofguard_mock.sqlite3"
                )
                settings = replace(
                    settings,
                    data_mode="mock",
                    allow_mock_data=True,
                    database_path=mock_database,
                    output_csv_path=PROJECT_ROOT / "data" / "mock" / "lake_observations_mock.csv",
                )
                providers = (
                    MockSentinel2Provider(),
                    MockGsmapProvider(),
                    MockGfsProvider(),
                    MockPowerProvider(),
                )
            else:
                settings.validate()
                selected_sources = {
                    value.strip().lower()
                    for value in args.sources.split(",")
                    if value.strip()
                }
                unknown_sources = selected_sources - {"sentinel", "gsmap", "gfs", "power"}
                if unknown_sources:
                    raise ValueError(
                        "Unknown sources: " + ", ".join(sorted(unknown_sources))
                    )
                disabled = DisabledProvider()
                satellite = (
                    EarthEngineSentinel2Provider(settings)
                    if "sentinel" in selected_sources
                    else disabled
                )
                shared_ee = getattr(satellite, "ee", None)
                precipitation = (
                    EarthEngineGsmapProvider(settings, ee_module=shared_ee)
                    if "gsmap" in selected_sources
                    else disabled
                )
                shared_ee = getattr(precipitation, "ee", shared_ee)
                forecast = (
                    EarthEngineGfsProvider(settings, ee_module=shared_ee)
                    if "gfs" in selected_sources
                    else disabled
                )
                providers = (
                    satellite,
                    precipitation,
                    forecast,
                    NasaPowerProvider(settings) if "power" in selected_sources else disabled,
                )
            repository = _repository(settings, args.database)
            summary = RefreshPipeline(
                settings,
                repository,
                *providers,
                lake_ids=args.lake_id,
            ).run()
            print(json.dumps(asdict(summary), indent=2))
        elif args.command == "monitor":
            repository = _repository(settings, args.database)
            snapshot = build_monitoring_snapshot(
                repository, verified_labels_path=args.verified_labels
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            print(json.dumps(snapshot, indent=2))
        elif args.command == "full-inventory":
            import pandas as pd

            from .full_inventory import (
                FullInventoryConfig,
                FullInventoryRunner,
                RateLimiter,
                prepare_reconciliation_outputs,
                select_representative_pilot,
                update_representative_pilot_usage,
                write_corrected_representative_pilot_report,
            )

            output_dir = args.output_dir.resolve()
            inventory, source_summary = prepare_reconciliation_outputs(
                args.source_archive.resolve(),
                args.processed.resolve(),
                output_dir,
            )
            static_estimate = {
                "eligible_unique_lakes": source_summary["processed_unique_records"],
                "upper_bound_nasa_power_http_requests": (
                    int(source_summary["processed_unique_records"]) * args.max_retries
                ),
                "upper_bound_sentinel_scene_lookup_getinfo_calls": (
                    int(source_summary["processed_unique_records"]) * args.max_retries
                ),
                "upper_bound_sentinel_measurement_getinfo_calls": (
                    int(source_summary["processed_unique_records"])
                    * settings.sentinel_area_max_candidate_scenes
                    * args.max_retries
                ),
                "upper_bound_gsmap_summary_getinfo_calls": (
                    int(source_summary["processed_unique_records"]) * args.max_retries
                ),
                "upper_bound_gfs_summary_getinfo_calls": (
                    int(source_summary["processed_unique_records"]) * args.max_retries
                ),
                "assumption": (
                    "Worst case: every provider call uses every retry and every "
                    "Sentinel candidate scene is measured. Run a small real batch for "
                    "an observed-rate estimate before approval of all lakes."
                ),
            }
            estimate_path = output_dir / "preflight_api_usage_estimate.json"
            estimate_path.write_text(
                json.dumps(static_estimate, indent=2), encoding="utf-8"
            )
            if args.dry_run:
                print(json.dumps({**source_summary, **static_estimate}, indent=2))
            else:
                if settings.data_mode != "real":
                    raise ValueError(
                        "Full inventory execution requires GLOF_DATA_MODE=real; "
                        "mock fallback is prohibited"
                    )
                settings.validate()
                if args.all_lakes and not args.confirm_full_run:
                    raise ValueError(
                        "The 8,806-requestable-lake launch is blocked. Review the safe-"
                        "batch API estimate and explicitly pass --confirm-full-run."
                    )
                if args.all_lakes and args.representative_pilot_size is not None:
                    raise ValueError(
                        "--all-lakes and --representative-pilot-size are mutually exclusive"
                    )
                if (
                    args.representative_pilot_size is not None
                    and not 1 <= args.representative_pilot_size <= 100
                ):
                    raise ValueError(
                        "Representative pilot size must be between 1 and 100"
                    )
                if (
                    args.representative_pilot_size is None
                    and not args.all_lakes
                    and args.max_lakes > 25
                    and not args.confirm_full_run
                ):
                    raise ValueError(
                        "Batches above 25 lakes require --confirm-full-run after quota review"
                    )
                pilot_selection = (
                    select_representative_pilot(
                        inventory,
                        sample_size=args.representative_pilot_size,
                        seed=args.pilot_seed,
                    )
                    if args.representative_pilot_size is not None
                    else None
                )
                maximum_lakes = (
                    None
                    if args.all_lakes
                    else (
                        args.representative_pilot_size
                        if args.representative_pilot_size is not None
                        else args.max_lakes
                    )
                )
                config = FullInventoryConfig(
                    as_of_date=args.as_of_date,
                    batch_size=args.batch_size,
                    max_lakes=maximum_lakes,
                    rate_limit_seconds=args.rate_limit_seconds,
                    max_retries=args.max_retries,
                    backoff_base_seconds=args.backoff_base_seconds,
                    backoff_cap_seconds=args.backoff_cap_seconds,
                    power_lookback_days=args.power_lookback_days,
                    retry_failed=not args.no_retry_failed,
                )
                config.validate()
                repository_path = (args.database or settings.database_path).resolve()
                repository = Repository(repository_path)
                backup_manifest = output_dir / "database_backup_manifest.json"
                if repository_path.exists() and not backup_manifest.exists():
                    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                    backup_path = (
                        PROJECT_ROOT
                        / "data"
                        / "backups"
                        / f"{repository_path.stem}_before_full_inventory_{timestamp}.sqlite3"
                    )
                    repository.backup_to(backup_path)
                    backup_manifest.write_text(
                        json.dumps(
                            {
                                "source_database": str(repository_path),
                                "backup_database": str(backup_path.resolve()),
                                "created_at": datetime.now(UTC).isoformat(),
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                elif backup_manifest.exists():
                    manifest = json.loads(backup_manifest.read_text(encoding="utf-8"))
                    if Path(manifest["source_database"]).resolve() != repository_path:
                        raise ValueError(
                            "Output directory belongs to a different database backup; "
                            "choose a separate --output-dir"
                        )
                repository.initialize()
                limiter = RateLimiter(args.rate_limit_seconds)
                satellite = EarthEngineSentinel2Provider(
                    settings, before_remote_call=limiter.wait
                )
                observed_weather = EarthEngineGsmapProvider(
                    settings,
                    ee_module=satellite.ee,
                    before_remote_call=limiter.wait,
                )
                forecast = EarthEngineGfsProvider(
                    settings,
                    ee_module=satellite.ee,
                    before_remote_call=limiter.wait,
                )
                power = NasaPowerProvider(settings, before_remote_call=limiter.wait)
                command_arguments = list(argv) if argv is not None else sys.argv[1:]
                exact_command = subprocess.list2cmdline(
                    [str(Path(sys.executable).resolve()), "-m", "glofguard.cli", *command_arguments]
                )
                runner = FullInventoryRunner(
                    settings=settings,
                    repository=repository,
                    inventory=inventory,
                    source_summary=source_summary,
                    satellite_provider=satellite,
                    power_provider=power,
                    observed_weather_provider=observed_weather,
                    forecast_provider=forecast,
                    config=config,
                    output_dir=output_dir,
                    command_used=exact_command,
                    selected_lake_ids=(
                        pilot_selection["lake_id"].astype(str).tolist()
                        if pilot_selection is not None
                        else None
                    ),
                )
                summary = runner.run()
                pilot_summary = None
                if pilot_selection is not None:
                    cumulative_usage = update_representative_pilot_usage(
                        output_dir,
                        pilot_selection,
                        summary,
                        artifact_stem="corrected_representative_100_pilot",
                    )
                    previous_status_path = (
                        PROJECT_ROOT
                        / "output"
                        / "full_inventory"
                        / "representative_100_pilot_status.csv"
                    )
                    previous_status = (
                        pd.read_csv(previous_status_path)
                        if previous_status_path.exists()
                        else None
                    )
                    pilot_summary = write_corrected_representative_pilot_report(
                        output_dir=output_dir,
                        selection=pilot_selection,
                        statuses=pd.DataFrame(
                            repository.full_inventory_statuses(runner.run_id)
                        ),
                        source_summary=source_summary,
                        usage=cumulative_usage,
                        errors=pd.DataFrame(
                            repository.full_inventory_errors(runner.run_id)
                        ),
                        exact_command=exact_command,
                        previous_pilot_status=previous_status,
                    )
                print("Exact command used:")
                print(exact_command)
                print(
                    json.dumps(
                        {
                            "full_inventory_checkpoint": summary,
                            "representative_pilot": pilot_summary,
                        },
                        indent=2,
                    )
                )
        print(SAFETY_NOTICE)
        return 0
    except Exception as exc:
        parser.exit(1, f"Error: {exc}\n{SAFETY_NOTICE}\n")


if __name__ == "__main__":
    raise SystemExit(main())
