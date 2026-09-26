# GLOF Data Audit Report

## Executive finding

The dataset is technically usable for **experimental method comparisons**, but its `label` column is not sufficiently trustworthy for a final GLOF risk model.

The available evidence shows that labels were produced by assigning `label = 1` to every inventory lake centroid within 5 km geodesic distance of any row in `pakistan_hazardous_lakes_unique.csv`. This rule reproduces all 8,806 labels exactly. It is not direct one-to-one matching to documented historical ICIMOD events. The reference file contains 53 coordinates, has no event IDs or source-record links, and 30 of its 53 lake names are `Unknown` or `Unnamed`.

Accordingly:

- All labels are treated as **experimental proxy labels**.
- Experimental models were trained only to evaluate the pipeline and the behavior of these proxy labels.
- No final risk classifier was saved.
- The saved preprocessing pipeline was fitted on training rows only and is safe to reuse after labels are rebuilt.

This is a student prototype, not an official flood-warning system.

## 1. Sources inspected

| Source | Role | Status |
|---|---|---|
| `glofguard_training_data.csv` | Primary modelling dataset | Preserved unchanged |
| `GLOF_Project_Proposal.pdf` | Project intent and proposed sources | Preserved unchanged; all 4 pages reviewed visually and by text extraction |
| `glof.py` | Available label-generation implementation | Contains a 5 km nearest-coordinate labelling rule |
| `pakistan_hazardous_lakes_unique.csv` | Coordinate list used by the label rule | Present, but upstream provenance is unverified |
| `pakistan_full_training_dataset.csv` | Earlier 8,808-row dataset | Contains two duplicate coordinate pairs removed from the 8,806-row dataset |

Source-file SHA-256 values at audit completion:

- `glofguard_training_data.csv`: `85403F0AF2C6669891464F154951CCA4CF1991BF8857959FC3BD19482B981F1B`
- `GLOF_Project_Proposal.pdf`: `E674F0A7A8FC210F448725C2E2A8852B6199F8FB10FD2ACDD31900338067EE9C`

## 2. Confirmed dataset facts

### Shape, types, missingness, and duplicates

- Rows: **8,806**
- Columns: **13**
- Fully duplicated rows: **0**
- Duplicate `sample_id` values: **0**
- Duplicate coordinate rows: **0**
- Earlier full dataset: **8,808 rows**, including **2 duplicate coordinate pairs** (4 involved rows)

| Column | Parsed type | Missing | Missing % |
|---|---:|---:|---:|
| `sample_id` | integer | 0 | 0.000% |
| `area` | float | 0 | 0.000% |
| `longitude` | float | 0 | 0.000% |
| `latitude` | float | 0 | 0.000% |
| `is_top200` | boolean | 0 | 0.000% |
| `temperature` | float | 0 | 0.000% |
| `rainfall` | float | 0 | 0.000% |
| `label` | integer | 0 | 0.000% |
| `elevation` | integer | 0 | 0.000% |
| `distance_to_nearest_settlement_km` | float | 0 | 0.000% |
| `nearest_settlement_name` | string | 0 | 0.000% |
| `annual_area_change_km2_per_year` | float | **5,611** | **63.718%** |
| `growth_data_available` | integer | 0 | 0.000% |

The growth indicator is internally consistent: all 3,195 rows with `growth_data_available = 1` have an annual-growth value, and all 5,611 rows with `growth_data_available = 0` have a missing value.

### Label balance

| Label | Count | Percentage |
|---:|---:|---:|
| 0 | 8,455 | 96.014% |
| 1 | 351 | 3.986% |

The imbalance ratio is approximately **24.1 label-0 rows per label-1 row**.

### Feature distributions

| Feature | Mean | Median | Minimum | Maximum |
|---|---:|---:|---:|---:|
| Area (km2) | 0.015485 | 0.001855 | 0.000009 | 3.930649 |
| Longitude | 74.735997 | 75.149693 | 71.149000 | 77.208187 |
| Latitude | 35.744941 | 35.832334 | 34.482656 | 36.926687 |
| Temperature | -3.2608 | -3.0300 | -13.3700 | 11.8200 |
| Rainfall | 0.9060 | 0.7700 | 0.3100 | 2.0600 |
| Elevation (m) | 4,187.09 | 4,195 | 2,023 | 5,541 |
| Settlement distance (km) | 17.5861 | 15.1465 | 0.1240 | 55.6372 |
| Annual area change (km2/year, observed rows only) | -0.0000188 | 0.0000000 | -0.00936 | 0.00978 |

Important distribution observations:

- Area is strongly right-skewed: the median is 0.00186 km2, while the maximum is 3.93 km2.
- Annual growth is concentrated around zero and exists for only 36.28% of rows.
- Positive proxy rows have lower mean elevation (3,598 m versus 4,212 m) and shorter mean settlement distance (9.35 km versus 17.93 km) than negative rows.
- Positive proxy rows have a more northerly mean latitude (36.212 versus 35.726), consistent with strong geographic clustering.
- The complete distribution figure is in `artifacts/feature_distributions.png`.

### `is_top200` redundancy

`is_top200` is exactly reconstructible from `area`:

- Minimum area where `is_top200 = 1`: **0.119556853 km2**
- Maximum area where `is_top200 = 0`: **0.119347696 km2**
- Reconstruction mismatches: **0**

It was converted to 0/1 in the model-ready file but excluded from model inputs because it duplicates an area threshold.

## 3. Label provenance investigation

### Confirmed facts

1. The proposal says that approximately **33 lakes are known to be dangerous** and names the ICIMOD GLOF Event Database as the intended source for dangerous-lake labels.
2. The proposal does not provide an event table, matching formula, event IDs, inclusion dates, negative-label policy, or manual-review record.
3. The local coordinate reference contains **53 rows**, not 33.
4. All 53 reference coordinates are unique, but **30 lake names are `Unknown` or `Unnamed`**.
5. The reference has only these fields: lake name, glacier name, latitude, longitude, and province. It has no event ID, event date, event type, citation, or source URL.
6. The available script assigns positives by nearest-coordinate distance with a default radius of 5 km.
7. Recomputing WGS84 geodesic distances reproduces **all 8,806 labels exactly**.

The audit added the requested provenance fields to `glofguard_model_ready.csv`:

- `label_source`
- `matched_event_id`
- `matched_event_name`
- `match_distance_km`
- `label_method`

Because actual event identifiers were unavailable, `matched_event_id` is intentionally blank. The file also adds `matched_reference_row`, `label_provenance_status`, and `geographic_group_025deg` for traceability.

### Why 351 positives do not mean 351 known dangerous lakes

- Only **4** inventory centroids are within 100 m of a reference coordinate.
- Positive rows have a mean nearest-reference distance of **2.583 km** and a maximum of **4.982 km**.
- Only **29 of 53** reference rows are the nearest reference for at least one positive.
- The 351 positives are therefore spatial neighbours created by a radius rule, not 351 documented events or 351 verified dangerous lakes.

This also does not support the proposal's approximate count of 33: the local list has 53 coordinates, and no evidence explains the difference.

### Label method classification

| Candidate method | Finding |
|---|---|
| Direct matching to documented ICIMOD events | **Not verified**; no event IDs or source records are present |
| Geographic-distance matching | **Confirmed**; WGS84 geodesic distance <= 5 km reproduces every label |
| Manual review | **No evidence found** |
| Formula using physical model inputs | **No evidence found**; the discovered rule uses coordinates and a reference list |
| Unknown method | Upstream creation of the 53-row reference list remains unknown |

The physical inputs were not used directly in the discovered label formula, so the most obvious circular-labelling problem was not found. However, `match_distance_km` and reference metadata are direct target leakage and were excluded from every model. Coordinates are conceptually risky because the target itself is defined spatially.

## 4. Leakage and geographic clustering

### Confirmed clustering

- Geographic block size: approximately 0.25 degrees latitude/longitude
- Total blocks: **155**
- Blocks containing positives: **20**
- Positive-only blocks: **2**
- Positives in the five most positive-heavy blocks: **217 of 351 (61.82%)**
- Median nearest-neighbour distance among all records: **0.192 km**

This concentration makes a random row split unsafe: nearby lakes from the same labelled radius could appear in both training and test data.

### Univariate leakage warning signals

Direction-free one-feature ROC-AUC values were:

| Feature | Direction-free ROC-AUC |
|---|---:|
| Elevation | 0.803 |
| Settlement distance | 0.794 |
| Latitude | 0.703 |
| Rainfall | 0.650 |
| Area | 0.572 |
| Longitude | 0.551 |
| Annual growth | 0.549 |
| Growth availability | 0.533 |
| Temperature | 0.507 |

These values do not prove leakage by themselves, but elevation, settlement distance, latitude, and rainfall strongly encode the regions where proxy positives were generated.

## 5. Training-data preparation

`glofguard_model_ready.csv` contains 8,806 rows and preserves all missing growth values.

- `sample_id` is retained only for tracing predictions.
- `nearest_settlement_name` is removed.
- `is_top200` is converted to 0/1 but excluded from models due to exact area redundancy.
- Label-audit fields, `match_distance_km`, and geographic-group IDs are not model inputs.
- No global imputation or scaling is stored in the CSV.
- In every fit, median imputation and standardization are learned from the training rows only.
- No SMOTE or oversampling is used.
- Class weights are computed independently from each training partition.

## 6. Geographic validation design

The fixed holdout design uses disjoint 0.25-degree groups:

| Partition | Rows | Positives | Geographic groups |
|---|---:|---:|---:|
| Train | 5,271 | 212 | 97 |
| Validation | 1,780 | 75 | 31 |
| Test | 1,755 | 64 | 27 |

Group overlap between partitions is zero. Classification thresholds are selected by validation F1 only and are never optimized on the test set.

Three-fold `StratifiedGroupKFold` is also used to measure regional PR-AUC and ROC-AUC stability. Imputation, scaling, class weights, and ANN early stopping are refitted within each fold.

## 7. Experimental model findings

### Geographic holdout

| Variant | Best holdout algorithm by PR-AUC | PR-AUC | Recall | F1 | False negatives | False positives |
|---|---|---:|---:|---:|---:|---:|
| Model A: coordinates + growth | Random Forest | 0.465 | 0.359 | 0.529 | 41 | 0 |
| Model B: no coordinates + growth | Random Forest | 0.461 | 0.359 | 0.517 | 41 | 2 |
| Model C: coordinates, no growth | Random Forest | 0.467 | 0.359 | 0.529 | 41 | 0 |
| Model D: observed-growth rows only | Logistic Regression | 0.104 | 0.037 | 0.045 | 26 | 16 |

The Model C ANN had the best Model C F1 (0.539) but still recalled only 37.5% of positive proxy rows. The dummy baseline had PR-AUC 0.0365 and predicted no positives at threshold 0.5.

### Geographic stability

The highest mean three-fold geographic PR-AUC was **Model B logistic regression: 0.235 +/- 0.118**. This high standard deviation indicates substantial regional instability. Model A logistic regression reached 0.229 +/- 0.156, and Model C logistic regression reached 0.208 +/- 0.150.

Evidence-based interpretation:

- Removing coordinates did not materially hurt the best logistic geographic-CV score, so explicit coordinates were not the only source of predictive signal.
- Regional proxies remain embedded in elevation, rainfall, and settlement distance.
- Model C performed similarly to Models A/B on the fixed holdout, so the incomplete annual-growth feature did not improve generalization.
- Model D performed poorly despite using real growth rows, and only 105 of its 3,195 rows are positive. There is no evidence that the available growth field improves proxy-label prediction.
- High holdout precision for several forest/ANN models comes with many false negatives and low recall. These models are unsuitable for warning use.
- The models reproduce the 5 km proxy target; they do not validate physical GLOF risk.

Complete metrics are in `model_metrics.csv`, confusion matrices are in `artifacts/confusion_matrices.png`, and precision-recall curves are in `artifacts/precision_recall_curves.png`.

## 8. Saved outputs and final-model decision

The following were saved:

- Executed notebook: `GLOF_Model_Training.ipynb`
- Model-ready data: `glofguard_model_ready.csv`
- Complete metrics: `model_metrics.csv`
- Fitted training-only preprocessing object: `artifacts/preprocessing_pipeline.joblib`
- Preprocessing metadata: `artifacts/preprocessing_metadata.json`
- Split assignments and test predictions for traceability
- Confusion-matrix, precision-recall, and feature-distribution images

The preprocessing object corresponds to Model C logistic regression, selected on validation PR-AUC (0.502), then validation recall and F1. **No classifier was saved** because label provenance is insufficient.

## 9. Unresolved problems

1. The authoritative source and extraction procedure for the 53-row hazard coordinate file are unknown.
2. There are no ICIMOD event IDs, event dates, source URLs, or source-record snapshots.
3. The proposal's approximate count of 33 dangerous lakes conflicts with the 53-row local reference.
4. There is no documented distinction between a historically outburst lake, a potentially dangerous lake, and a lake near either type.
5. A 5 km radius can label multiple separate inventory polygons as positive without proving they represent the referenced lake.
6. The negative class means only "not within 5 km of this local list"; it does not mean scientifically confirmed safe.
7. The proposal describes Low/Medium/High risk, while the dataset contains only binary proxy labels.
8. The annual-growth field's upstream matching and uncertainty are not documented in the available workspace.

## 10. Required work before a final risk model

1. Export an authoritative ICIMOD/event table with stable event IDs, event names, dates, coordinates, event type, and source links.
2. Separate historical outburst events from potentially dangerous-lake assessments.
3. Match each authoritative event to a unique inventory lake using polygon/centroid evidence and manual review, not an automatic blanket radius.
4. Store the reviewer, review date, match distance, matching evidence, and reason for every positive.
5. Define negative labels carefully; unlabeled should not automatically mean safe.
6. Reconcile the 33-versus-53 discrepancy.
7. Re-run this notebook with rebuilt labels and repeat geographic validation.
8. Seek domain-expert review before making any operational or safety claim.

## Fact / inference / unresolved summary

- **Confirmed fact:** all current labels are exactly reproduced by a 5 km WGS84 geodesic-distance rule.
- **Confirmed fact:** 351 positive rows are spatially expanded inventory records, not 351 documented events.
- **Confirmed fact:** there is strong geographic concentration and geographic-validation performance is unstable.
- **Evidence-based inference:** models are learning a mixture of regional patterns and physical correlates of the proxy-label locations.
- **Unresolved:** whether the 53 coordinate rows are valid, complete, and correctly derived from ICIMOD or another authoritative source.
- **Unresolved:** whether any experimental metric transfers to real future GLOF risk.

