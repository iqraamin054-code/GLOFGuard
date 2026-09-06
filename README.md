# GLOF Early Warning & Risk Predictor - ML Audit

This repository contains an audited, reproducible **student prototype** for comparing GLOF proxy-label models in Northern Pakistan. It is not an official flood-warning system and must not be used for safety decisions.

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.

## Live-data extension

The project now has two separate outputs:

- **Baseline lake susceptibility** is a label-free, relative monitoring-priority index from static lake area, elevation, and settlement proximity. The legacy CSV has no observation dates and is used only for this baseline context.
- **Dynamic environmental risk indicator** uses dated Sentinel-2 lake areas, JAXA GSMaP observed precipitation, NOAA GFS short forecasts, and NASA POWER historical weather/anomalies. It recalculates only when its input signature changes.

The canonical one-lake-per-date schema is in `data/time_series_schema.csv`. Provider adapters, append-safe storage, rolling features, confidence/staleness warnings, drift monitoring, scheduling, and tests are under `glofguard/`, `scripts/`, and `tests/`.

Start with [docs/LIVE_DATA.md](docs/LIVE_DATA.md). A quick interface test that cannot be confused with real data is:

```powershell
.\.python\python.exe -m unittest discover -s tests -v
.\.python\python.exe -m glofguard.cli import-baseline `
  --input tests/fixtures/baseline_lakes_sample.csv `
  --database data/mock/glofguard_mock.sqlite3
.\.python\python.exe -m glofguard.cli refresh --mock
```

Mock rows are isolated under `data/mock`, carry `source_mode=MOCK`, use low confidence, and include a data-quality warning. No event target is present. Targets such as `glof_within_next_7_days` may be added only to a separately versioned training table after authoritative historical event dates are verified.

The checkpointed 8,808-polygon reconciliation and REAL Sentinel-2/GSMaP/GFS/NASA POWER batch runner are documented in [docs/FULL_INVENTORY.md](docs/FULL_INVENTORY.md). It defaults to a small batch, creates a database backup, resumes after interruption, logs every failed/excluded lake, and blocks an unrestricted run until `--confirm-full-run` is explicitly supplied after quota review.

The corrected representative 100-lake REAL pilot is complete and recorded in `output/full_inventory_corrected_v2_pilot/CORRECTED_REPRESENTATIVE_100_PILOT_REPORT.md`. All 100 lakes had fresh complete GSMaP and GFS data and an available POWER baseline; Sentinel area was fresh for 34, stale-but-valid for 19, and unavailable for 47. There were no provider failures or mock records. The remaining inventory is intentionally not started automatically; review the estimated 54.38% all-critical-source availability, 66.03-hour duration, and account-specific Cloud Billing/quota state before granting separate approval.

## Official PMD master coverage

The system now treats a validated, row-level PMD inventory as the only acceptable master coverage list. The importer fails closed unless the file contains exactly 3,044 rows, unique official lake identifiers, and valid coordinates. It never samples or trims the 8,806 modeling rows to manufacture that count.

The PMD/PARC publications available in this workspace confirm the 3,044 total, but no public machine-readable file with all identifiers and coordinates was found. Obtain the official CSV, shapefile, GeoPackage, or GeoJSON from PMD/PARC and run:

```powershell
.\.python\python.exe -m glofguard.cli reconcile-inventory `
  --official path\to\official_pmd_3044_lakes.csv `
  --existing glofguard_model_ready.csv `
  --output-dir output\inventory
```

On success, the command atomically activates the PMD rows as the database master, classifies every existing row as matched, unmatched, duplicate, or newly detected candidate, writes the detailed reports, and creates `output/inventory/pakistan_pmd_lakes_map.html`. Every official lake remains on the map; a gray `Data unavailable` state is used until critical real satellite, observed-weather, and forecast features are present. See [docs/PMD_INVENTORY.md](docs/PMD_INVENTORY.md) for field rules and matching semantics.

## Current status

The modelling pipeline runs successfully, but label provenance is not strong enough to save a final risk classifier.

The available `label` column was created as follows:

```text
label = 1 if the lake centroid is within 5 km geodesic distance
        of any coordinate in pakistan_hazardous_lakes_unique.csv
        else 0
```

This rule reproduces all 8,806 labels. The 53-row coordinate list has no event IDs or verifiable source-record links, and 30 names are `Unknown` or `Unnamed`. The 351 positives are therefore treated as experimental proxy labels, not 351 confirmed dangerous lakes or historical events.

See [GLOF_Data_Audit_Report.md](GLOF_Data_Audit_Report.md) for the complete evidence and limitations.

## Main deliverables

- `GLOF_Data_Audit_Report.md` - data, provenance, leakage, and model audit
- `glofguard_model_ready.csv` - untouched values plus traceable label metadata
- `GLOF_Model_Training.ipynb` - fully executed notebook with zero cell errors
- `model_metrics.csv` - holdout and geographic-CV metrics
- `glof_experiment.py` - reusable implementation called by the notebook
- `artifacts/confusion_matrices.png`
- `artifacts/precision_recall_curves.png`
- `artifacts/feature_distributions.png`
- `artifacts/preprocessing_pipeline.joblib`
- `artifacts/preprocessing_metadata.json`
- `artifacts/geographic_split_assignments.csv`
- `artifacts/geographic_cv_stability.csv`
- `artifacts/geographic_test_predictions.csv`

No final `.keras`, `.h5`, or other classifier file is included because the labels are not sufficiently verified.

## Features used and removed

All models exclude:

- `sample_id` - retained only for prediction tracing
- `nearest_settlement_name` - name string, not a physical model input
- `is_top200` - exactly duplicates an `area` threshold
- `match_distance_km` - directly defines the proxy target and would be target leakage
- All label-source, matched-event, and geographic-group audit fields

The experimental variants are:

| Variant | Inputs |
|---|---|
| Model A | Area, temperature, rainfall, elevation, settlement distance, longitude, latitude, annual growth, growth-availability indicator |
| Model B | Model A without longitude and latitude |
| Model C | Area, temperature, rainfall, elevation, settlement distance, longitude, latitude; no growth fields |
| Model D | Model A physical/coordinate/growth fields, only for rows with observed growth |

Model D excludes the growth-availability indicator because every row in that subset has observed growth.

## Missing-data strategy

`annual_area_change_km2_per_year` is missing for 5,611 rows (63.718%). Those rows are retained.

- The model-ready CSV keeps the values missing.
- Median imputation is fitted using training rows only.
- `growth_data_available` remains a missing-data indicator in Models A/B.
- Numeric scaling is also fitted on training rows only.
- Nothing is imputed or scaled before splitting.

## Class-imbalance strategy

The labels contain 8,455 zeros and 351 ones.

- Class weights are computed programmatically from each training partition.
- No SMOTE or oversampling is used.
- Accuracy is not used as the main metric.
- Reported metrics include precision, recall, F1, PR-AUC, ROC-AUC, confusion matrix, false negatives, and false positives.
- A `DummyClassifier` baseline is included.

## Geographic validation

Lakes are grouped into approximately 0.25-degree latitude/longitude blocks.

- Fixed train, validation, and test groups are mutually disjoint.
- Thresholds are selected on validation data only.
- The test set is not used for threshold tuning.
- Three-fold `StratifiedGroupKFold` measures regional stability.
- Preprocessing and class weights are refitted independently inside every fold.

The fixed split contains:

| Partition | Rows | Positives | Groups |
|---|---:|---:|---:|
| Train | 5,271 | 212 | 97 |
| Validation | 1,780 | 75 | 31 |
| Test | 1,755 | 64 | 27 |

## Models

Each variant compares:

- Logistic Regression
- Class-weighted Random Forest
- Small TensorFlow/Keras ANN with sigmoid output, binary cross-entropy, dropout, class weights, fixed seeds, and early stopping

Model C also includes the dummy baseline.

The best mean three-fold geographic PR-AUC was Model B logistic regression at 0.235 +/- 0.118. This variability is high, recall is weak on the fixed geographic test set, and these results describe proxy-label reproduction only.

## How to run

Python 3.12 is available locally at `.python\python.exe` in the audited workspace. On another machine, install Python 3.12 first.

Install dependencies:

```powershell
.\.python\python.exe -m pip install -r requirements.txt
```

Execute the notebook from beginning to end:

```powershell
$env:MPLCONFIGDIR = (Join-Path (Get-Location) '.matplotlib')
.\.python\Scripts\jupyter-nbconvert.exe --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 GLOF_Model_Training.ipynb
```

Or run the same reusable workflow directly:

```powershell
.\.python\python.exe glof_experiment.py
```

For a normal system Python installation, replace `.\.python\python.exe` with `python` and the Jupyter executable with `python -m jupyter nbconvert`.

## Label fields in the model-ready CSV

The audit adds:

- `label_source`
- `matched_event_id` (blank because no event IDs were available)
- `matched_event_name`
- `match_distance_km`
- `matched_reference_row`
- `label_method`
- `label_provenance_status`
- `geographic_group_025deg`

These fields are for audit and traceability only and are never model inputs.

## Limitations

- The 53-row coordinate list cannot currently be tied to authoritative event records.
- The proposal mentions about 33 dangerous lakes, which conflicts with the local 53-row list.
- Nearby inventory polygons were labelled positive automatically, so positive does not mean a verified event lake.
- Negative means only "outside the 5 km proxy radius," not scientifically confirmed safe.
- Physical and settlement features encode geographic regions associated with the proxy labels.
- Annual-growth provenance and uncertainty are undocumented.
- The proposal describes Low/Medium/High risk, but the dataset provides only binary proxy labels.
- GLOFs are rare, complex events; this prototype does not predict an outburst date or provide an operational warning.

Before saving or deploying a final model, rebuild labels from authoritative event IDs with one-to-one lake matching and domain-expert review, then repeat the geographic audit.
