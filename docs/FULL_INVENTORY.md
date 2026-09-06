# Full 8,808-polygon inventory processing

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.

The source archive contains 8,808 polygon rows and the processed inventory contains 8,806 records. Reconciliation proves that the difference is two exact duplicate geometries:

- source `OBJECTID_1=6960` duplicates `OBJECTID_1=6121` (`PKGL-06121`);
- source `OBJECTID_1=6961` duplicates `OBJECTID_1=6122` (`PKGL-06122`).

The duplicate source rows remain in every status export as `EXCLUDED_DUPLICATE`. The 103 geometries reported invalid by Shapely are not silently discarded; they remain eligible and are listed in `source_geometry_warnings.csv`.

## Preflight with no provider calls

```powershell
.\.python\python.exe -m glofguard.cli full-inventory `
  --dry-run `
  --output-dir output\full_inventory
```

This validates row counts, source order, centroid/area agreement, geometry hashes, and writes a worst-case API estimate. It does not initialize Earth Engine, contact NASA POWER, or modify the database.

## Safe real batch

```powershell
.\.python\python.exe -m glofguard.cli full-inventory `
  --output-dir output\full_inventory `
  --max-lakes 3 `
  --batch-size 1 `
  --rate-limit-seconds 1.0 `
  --max-retries 3 `
  --backoff-base-seconds 2 `
  --backoff-cap-seconds 30
```

The first real invocation makes a consistent SQLite backup under `data/backups` before adding full-inventory checkpoints. It requires `GLOF_DATA_MODE=real`; there is no mock option and no mock fallback. A successful row must have `source_mode=REAL`.

Each lake is checkpointed immediately. Re-running the same command and as-of date skips completed/stale/unavailable-imagery records and continues with the next pending batch. `FAILED` records are retried by default. Use `--no-retry-failed` only when the failed records should remain untouched during that invocation.

Provider exceptions are retried with exponential backoff and written per lake and per attempt. A rejected cloudy Sentinel measurement never replaces the most recent reliable area. Missing NASA values are not treated as current weather; the most recent complete daily row is used and is clearly marked stale when it exceeds the configured freshness threshold.

## Representative 100-lake pilot

Before any unrestricted launch, run the deterministic 100-lake pilot:

```powershell
.\.python\python.exe -m glofguard.cli full-inventory `
  --output-dir output\full_inventory `
  --representative-pilot-size 100 `
  --pilot-seed 20260901 `
  --batch-size 5 `
  --rate-limit-seconds 1.0 `
  --max-retries 3 `
  --backoff-base-seconds 2 `
  --backoff-cap-seconds 30
```

The selector uses three latitude, longitude, elevation, and log-area bands. It covers every populated combined stratum first, allocates the remaining positions proportionally, and chooses lakes deterministically within strata. The selection, pilot-only statuses, errors, cumulative usage, summary, and report are stored as `representative_100_pilot_*` files and `REPRESENTATIVE_100_PILOT_REPORT.md`.

The corrected pilot additionally reports four independent states: satellite-area freshness, GSMaP observed-weather freshness, GFS forecast freshness, and NASA POWER baseline availability. It does not collapse them into one stale flag. Sentinel searches up to 180 days for the latest reliable area while retaining the actual observation age and unchanged 20-day freshness threshold. GSMaP and GFS are read from their public Earth Engine collections; NASA POWER delay cannot make the live record stale.

The original September 1, 2026 pilot exposed the combined-freshness design problem. The corrected pilot is recorded in `output/full_inventory_corrected_v2_pilot/CORRECTED_REPRESENTATIVE_100_PILOT_REPORT.md`: all 100 lakes had fresh complete GSMaP and GFS data and an available NASA POWER baseline; Sentinel area was fresh for 34, stale-but-valid for 19, and unavailable for 47. The extended search recovered 19 of the original 66 no-imagery cases. The remaining 47 had scene coverage, valid geometry, metadata-clear candidates, and successful Cloud Score joins, but pixel cloud/shadow filtering still prevented a reliable area. There were 0 provider failures and 0 mock observations. Runtime was 2,699.234 seconds, with a stratified 54.38% all-critical-source availability estimate and a 66.03-hour full-run duration estimate. No quota, rate-limit, or billing error was returned; actual account charges remain visible only in the Google Cloud Billing console.

## Full launch safety gate

The command refuses an unrestricted launch unless the API/quota estimate has been reviewed and the confirmation flag is supplied:

```powershell
.\.python\python.exe -m glofguard.cli full-inventory `
  --output-dir output\full_inventory `
  --all-lakes `
  --confirm-full-run `
  --batch-size 25 `
  --rate-limit-seconds 1.0 `
  --max-retries 3
```

Do not run this command until the operator has checked the active Earth Engine project and applicable quotas or billing. The September 1, 2026 three-lake sample used 3 Sentinel lookups, 11 Sentinel scene measurements, and 3 NASA POWER requests in 29.844 seconds. Linear extrapolation is approximately 8,806 lookups, 32,288 measurements, 8,806 POWER requests, and 24.3 hours. The conservative retry/candidate ceiling is much larger and is recorded in `api_usage_estimate.json`.

## Outputs

All writes are atomic and idempotent:

- `full_inventory_status.csv`: all 8,808 source rows, including pending and duplicate rows;
- `excluded_or_failed_lakes.csv`: every duplicate, failure, no-usable-imagery, and stale record with its reason;
- `per_lake_error_log.csv`: every provider/runner exception and attempt;
- `source_inventory_reconciliation.csv`: the complete source-to-processed audit;
- `source_geometry_warnings.csv`: invalid-geometry warnings retained for review;
- `full_inventory_checkpoint.json`: latest resumable progress;
- `api_usage_estimate.json`: measured and upper-bound request estimates;
- `FULL_DATA_QUALITY_REPORT.md`: coverage, freshness, source-mode, date, and failure summary.
- `REPRESENTATIVE_100_PILOT_REPORT.md`: representative-pilot outcomes, measured calls, weighted success estimate, duration projection, and quota/charge evidence.

`environmental_conditions_score` is a transparent conditions index. It is not a validated GLOF probability, an event forecast, or an official warning.
