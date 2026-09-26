# Full Inventory Data Quality Report

Report state: **INTERIM**  
Run ID: `8e2cf3ce8c9a4788ce87`  
As-of date: `2026-09-01`
Exact command used: `"C:\Users\Essa Ahmed\Desktop\Ayesha\GLOF\.python\python.exe" -m glofguard.cli full-inventory --output-dir output\full_inventory_corrected_v2_pilot --representative-pilot-size 100 --pilot-seed 20260901 --batch-size 5 --rate-limit-seconds 1.0 --max-retries 3 --backoff-base-seconds 2 --backoff-cap-seconds 30`

## Coverage summary

| Metric | Count |
|---|---:|
| Total source lake polygons | 8,808 |
| Eligible unique lake records | 8,806 |
| Successfully processed lakes | 53 |
| Failed lakes | 0 |
| Lakes without usable imagery | 48 |
| Stale observations | 0 |
| Exact duplicate source polygons excluded | 2 |
| Pending lakes | 8,705 |
| REAL observations | 101 |
| MOCK observations | 0 |

## Source-specific availability and freshness

- Satellite-area freshness: {'UNAVAILABLE': 8755, 'FRESH': 34, 'STALE': 19}
- Observed-weather freshness: {'UNAVAILABLE': 8707, 'FRESH': 101}
- Observed-weather data quality: {'MISSING': 8707, 'COMPLETE': 101}
- Forecast freshness: {'UNAVAILABLE': 8707, 'FRESH': 101}
- Forecast data quality: {'MISSING': 8707, 'COMPLETE': 101}
- Historical-baseline availability: {'UNAVAILABLE': 8707, 'AVAILABLE': 101}

Eligible success coverage: **0.602%**  
Overall source success coverage: **0.602%**

## Observation dates

- Earliest usable satellite observation: 2026-04-26T05:49:07.071000+00:00
- Latest usable satellite observation: 2026-08-30T06:09:15.501000+00:00
- Earliest weather observation: 2026-08-31T00:00:00+00:00
- Latest weather observation: 2026-08-31T00:00:00+00:00

## Reconciliation of 8,808 versus 8,806

The original archive contains 8,808 polygon rows but only 8,806 unique geometries. Source polygon `OBJECTID_1=6960` is an exact geometry duplicate of `6121`, and `OBJECTID_1=6961` is an exact geometry duplicate of `6122`. The canonical earlier rows map to `PKGL-06121` and `PKGL-06122`. The later copies remain in the audit outputs as `EXCLUDED_DUPLICATE`; they are not silently deleted and do not trigger duplicate API requests.

The source also contains 103 geometries reported invalid by Shapely. They remain eligible because the processed centroid and area fields are available. Every warning is listed in `source_geometry_warnings.csv`.

## Provider failure reasons

- No provider exceptions recorded.

Every provider exception is retained in `per_lake_error_log.csv`. Every excluded, failed, unavailable-imagery, or stale record is listed in `excluded_or_failed_lakes.csv`.

## Score interpretation

`environmental_conditions_score` is a transparent 0-100 index based on available recent temperature, observed rainfall, and lake-area change. It is **not a validated GLOF probability**, does not assert that a flood will occur, and must not be used as an official warning.

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.
