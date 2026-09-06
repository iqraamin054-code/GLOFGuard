# PMD 3,044-lake master inventory

## Source requirement

Use the row-level PMD/PARC inventory developed from 2013 remote sensing. A summary, chart, basin-total table, presentation, map image, or a different inventory is not sufficient because reconciliation requires each official lake's identifier and coordinates.

The loader accepts CSV, TSV, GeoJSON, GeoPackage, shapefile, or a ZIP containing a GIS dataset. It requires:

- exactly 3,044 records;
- a nonblank, unique official lake identifier;
- valid latitude/longitude values within the Pakistan study bounds, or geospatial geometry with a declared CRS.

The loader refuses to derive the master by selecting 3,044 records from `glofguard_model_ready.csv`.

Official count references:

- NDMA, *Glaciers and Glacial Lakes Inventory of Pakistan*: https://ndma.gov.pk/public/storage/outlooks/May2025/fZeLw3QdTyjo4Smc53Xo.pdf
- Pakistan Meteorological Department glacier monitoring: https://www.pmd.gov.pk/rnd/rndweb/rnd_new/glacier-monitoring.php
- Government of Pakistan National Assembly response citing the PARC 2015 final technical report: https://na.gov.pk/uploads/documents/questions/629090c05615a_261.pdf

The available 2020 source is a separate PlanetScope-derived dataset whose archive contains 8,808 polygons. The cleaned model table has 8,806 rows after two exact duplicate extras are removed. Neither count is a count of unique official PMD lakes.

## Command

```powershell
.\.python\python.exe -m glofguard.cli reconcile-inventory `
  --official path\to\official_pmd_3044_lakes.csv `
  --existing glofguard_model_ready.csv `
  --output-dir output\inventory `
  --max-distance-m 750 `
  --ambiguity-margin-m 100 `
  --duplicate-distance-m 50
```

If automatic field detection is not appropriate, add `--official-id-column`, `--source-id-column`, or `--source-official-id-column`. The PMD identifier column is mandatory; generated row numbers are not accepted as official identifiers.

## Matching rules

1. A normalized official identifier match takes priority, provided its coordinates are within the 5 km sanity limit.
2. Otherwise, the nearest official coordinate within 750 m is considered.
3. A coordinate match is marked `unmatched` for manual review when the two nearest official lakes are within 100 m of each other in match distance.
4. The best source row for an official lake is `matched`. Additional source rows assigned to that same official lake are `duplicate` records, not additional official lakes.
5. A valid 2020 row outside the official match radius is `newly_detected_candidate`. This means candidate only; expert or agency verification is required before adding it to any official inventory.
6. Missing coordinates, identifier-coordinate conflicts, and ambiguous coordinate matches are `unmatched`.

These thresholds are recorded in the report and can be changed explicitly. Changing them does not change the PMD master count.

## Outputs

- `inventory_matching_report.csv`: one status for every existing source row;
- `pmd_master_coverage.csv`: exactly one row per official PMD lake;
- `duplicate_source_rows.csv`;
- `unmatched_source_rows.csv`;
- `newly_detected_candidates.csv`;
- `unmatched_official_lakes.csv`;
- `inventory_matching_summary.json`: counts, hashes, parameters, and provenance;
- `pakistan_pmd_lakes_map.html`: all 3,044 official lakes.

The map only displays a risk indicator when all critical real-data fields are present: current lake area, current temperature, seven-day observed rainfall, next-72-hour forecast rainfall, a risk score, and a prediction timestamp. Otherwise the lake remains visible as `Data unavailable`.

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.
