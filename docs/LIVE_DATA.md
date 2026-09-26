# Live environmental data pipeline

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.

## What the two outputs mean

`data/baseline_susceptibility.csv` is a label-free, relative monitoring-priority index based on lake area, elevation, and settlement proximity. It is not a probability and it does not use the legacy proxy `label` column.

`data/lake_observations.csv` has one row per lake and observation date. Its dynamic score describes the environmental conditions available at refresh time. The score is not a claim that a GLOF will occur. The legacy CSV has no observation-date field and remains baseline-only.

`area_change_30d`, `area_change_90d`, and `area_change_365d` are percentage changes. A change stays missing until a reliable satellite observation exists near the requested earlier date. No value is fabricated.

The default `transparent-conditions-index-v0.1.0` is an uncalibrated research index. Its thresholds and weights are visible in `glofguard/scoring.py`. It supports interface development; it is not an event probability. A trained model is loaded only when `GLOF_MODEL_VALIDATED=true` and both its saved preprocessing pipeline and classifier are present. The audited repository's existing preprocessor is not sufficient because no trustworthy classifier was saved.

## Official sources and authentication

### Sentinel-2 Surface Reflectance Harmonized

The adapter uses Earth Engine collection `COPERNICUS/S2_SR_HARMONIZED`, links `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED`, applies a Cloud Score+ threshold, and excludes Sentinel-2 SCL cloud-shadow, cloud, cirrus, and snow/ice classes. It computes MNDWI (B3/B11) by default; NDWI (B3/B8) is configurable. It searches recent scenes newest-first for the newest usable measurement. If every candidate is contaminated, the newest rejected scene is recorded with no area and never replaces the latest reliable observation.

One-time local authentication:

1. Create or select a Google Cloud project, enable/register it for Earth Engine, and place its ID in `GLOF_GEE_PROJECT`.
2. Run `earthengine authenticate` and complete the browser flow.
3. Verify with `python -c "import ee, os; ee.Initialize(project=os.environ['GLOF_GEE_PROJECT']); print('Earth Engine ready')"`.

For unattended infrastructure, use Google Application Default Credentials or a registered service account. Do not commit a JSON key; reference it through the deployment environment or secret manager.

Official documentation:

- https://developers.google.com/earth-engine/guides/auth
- https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED
- https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_CLOUD_SCORE_PLUS_V1_S2_HARMONIZED

### JAXA GSMaP

Both daily refresh and full-inventory workflows use the public Earth Engine collection `JAXA/GPM_L3/GSMaP/v8/operational`. They read the gauge-corrected `hourlyPrecipRateGC` band. The daily path stores the dated hourly point observations; the inventory path efficiently returns 24-hour, 7-day, and 30-day totals. Rolling windows end at the latest published REAL image, while observed-weather freshness uses that image's actual age. Totals remain incomplete unless at least 80% of expected hours exist.

No separate JAXA password is used by the active pipeline; the configured Earth Engine project and authenticated Earth Engine session provide access. Provider failures are logged and never replaced with mock rainfall.

Official collection documentation: https://developers.google.com/earth-engine/datasets/catalog/JAXA_GPM_L3_GSMaP_v8_operational

### NOAA GFS

Both daily refresh and full-inventory workflows read `NOAA/GFS0P25` through Earth Engine. They select the newest cycle that contains forecast hour 168, sum only precipitation assets at six-hour multiples through hour 72 to avoid double-counting, and use 2-metre temperature through 24 hours and seven days. A complete current seven-day cycle has 136 temperature steps because GFS is hourly through hour 120 and three-hourly afterward. Incomplete horizons remain explicitly incomplete. GFS is model guidance; creation time and horizon are stored with every forecast.

Official product/filter documentation:

- https://developers.google.com/earth-engine/datasets/catalog/NOAA_GFS0P25
- https://www.nco.ncep.noaa.gov/pmb/products/gfs/

### NASA POWER

NASA POWER's daily point API provides `T2M` and `PRECTOTCORR` in UTC. The first refresh builds a ten-year history, then fetches only new dates. Temperature and month-to-date rainfall anomalies compare current conditions with the same calendar period in earlier years and remain missing until at least three earlier years exist.

NASA POWER is historical/climate-baseline data. Its publication date is reported as baseline availability but never controls satellite-area, observed-weather, forecast, or overall live freshness.

Official API documentation: https://power.larc.nasa.gov/docs/services/api/temporal/daily/

## First run

```powershell
Copy-Item .env.example .env
# Fill only the secrets/project settings in .env.
.\.python\python.exe -m pip install -r requirements.txt
.\.python\python.exe -m pip install -r requirements-live.txt
.\.python\python.exe -m glofguard.cli init-db
.\.python\python.exe -m glofguard.cli import-baseline --input glofguard_model_ready.csv
.\.python\python.exe -m glofguard.cli baseline
.\.python\python.exe -m glofguard.cli refresh `
  --lake-id PKGL-00001
.\.python\python.exe -m glofguard.cli monitor
```

The first refresh command is the required all-source real-data smoke test: it processes one lake using authenticated Earth Engine Sentinel-2, public Earth Engine GSMaP/GFS, and NASA POWER. It does not use mock data. Inspect `data/lake_observations.csv` and require `source_mode=REAL`, source-specific freshness fields, and no `MOCK DATA` warning before expanding the scope. The import reads static fields and intentionally ignores `label`, label-source metadata, and all target-like columns.

Only after both checks pass, refresh all imported lakes:

```powershell
.\.python\python.exe -m glofguard.cli refresh
```

For the audited 8,808-source-polygon run, do not use the unrestricted `refresh` command. Use the rate-limited, checkpointed workflow in [FULL_INVENTORY.md](FULL_INVENTORY.md), beginning with `full-inventory --dry-run` and a small REAL batch. The full launch is intentionally approval-gated.

## Mock interface test

Authenticated sources are not required for the automated tests. Mock mode must be explicitly requested and uses `data/mock/glofguard_mock.sqlite3` and `data/mock/lake_observations_mock.csv` so it cannot silently contaminate the real store.

```powershell
.\.python\python.exe -m glofguard.cli import-baseline `
  --input tests/fixtures/baseline_lakes_sample.csv `
  --database data/mock/glofguard_mock.sqlite3
.\.python\python.exe -m glofguard.cli refresh --mock
```

Every mock source string, row, warning, and output path is marked `MOCK`. Never present these values as environmental observations.

## Daily scheduling

Run `scripts/run_daily_refresh.ps1` once per day with Windows Task Scheduler under an account that can access its Earth Engine credentials. `scripts/register_daily_task.ps1` registers a 03:00 daily task when you intentionally run it; task registration is not performed automatically. The task should start in this repository and may run whether or not the user is logged in. Keep credentials in the task account's environment or a managed secret store.

The refresh is append-safe: failures are inserted into `ingestion_failures`; valid source rows are not deleted; rejected cloudy areas are stored as rejected candidates with no area; the indicator is recalculated only when its input signature changes; and no retraining occurs during refresh.

## Targets, validation, and retraining

There are no event targets in the canonical schema. Add `glof_within_next_7_days` or `glof_within_next_30_days` only in a separately versioned training table after authoritative lake IDs and historical event dates have been verified. Never derive event labels from area, weather, coordinates, risk scores, or other model inputs.

Use `glofguard.validation_split.time_geographic_split` so all training dates precede test dates and 0.25-degree geographic groups do not cross partitions. Monitoring reports missingness, feature PSI, prediction PSI, high-risk counts, freshness, and mock-row counts. Calibration and false-positive/false-negative metrics stay unavailable until a separate label table with `label_provenance_status=verified` is supplied.

Review retraining every three to six months, when new verified GLOF events are added, or when meaningful drift appears. Do not retrain during daily ingestion.
