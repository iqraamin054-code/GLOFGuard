# Corrected Representative 100-Lake REAL-Data Pilot Report

Report state: **COMPLETE**  
Exact command: `"C:\Users\Essa Ahmed\Desktop\Ayesha\GLOF\.python\python.exe" -m glofguard.cli full-inventory --output-dir output\full_inventory_corrected_v2_pilot --representative-pilot-size 100 --pilot-seed 20260901 --batch-size 5 --rate-limit-seconds 1.0 --max-retries 3 --backoff-base-seconds 2 --backoff-cap-seconds 30`

## Source-specific availability and freshness

| Source assessment | Counts |
|---|---|
| Satellite-area freshness | {'UNAVAILABLE': 47, 'FRESH': 34, 'STALE': 19} |
| GSMaP observed-weather freshness | {'FRESH': 100} |
| GSMaP observed-weather data quality | {'COMPLETE': 100} |
| GFS forecast freshness | {'FRESH': 100} |
| GFS forecast data quality | {'COMPLETE': 100} |
| NASA POWER historical-baseline availability | {'AVAILABLE': 100} |
| Overall availability (not freshness) | {'AVAILABLE': 53, 'PARTIAL': 47} |

NASA POWER publication delay is not used in any live freshness calculation. The 20-day satellite-area, 36-hour observed-weather, and 12-hour forecast thresholds are unchanged and evaluated independently.

## Sentinel investigation

The corrected search uses the latest reliable observation found within the configured 180-day window, retains its actual age, pre-filters metadata clouds that could never pass the configured threshold, verifies Cloud Score joins by shared `system:index`, and records scene/candidate counts for each lake.

- PIXEL_CLOUD_OR_SHADOW_FILTERING: 47
- USABLE_IN_RECENT_WINDOW: 34
- USABLE_RECOVERED_FROM_EXTENDED_WINDOW: 19

Explicit cause tests among unavailable Sentinel records:

- Invalid source geometry: 0
- No Sentinel scene coverage: 0
- All scenes removed by metadata cloud filtering: 0
- Cloud Score join unavailable: 0
- Pixel cloud/shadow filtering prevented a reliable area: 47
- Valid areas recovered only by the extended date window: 19

- Previously unavailable pilot lakes represented here: 66
- Recovered with a valid satellite area: 19
- Still unavailable: 47

## Runtime and API usage

- Runtime: 2699.234 seconds (0.750 hours)
- Sentinel lookups: 100
- Sentinel measurements: 1,685
- GSMaP Earth Engine summaries: 100
- GFS Earth Engine summaries: 100
- NASA POWER baseline requests: 56
- Provider failures: 0
- Mock count: 0

## Full-run operational estimate

- Stratified estimate with all three critical live sources available: **54.38%**
- Estimated duration for 8,806 unique lakes: **66.03 hours**
- Estimated Sentinel lookups: 8806
- Estimated Sentinel measurements: 148381
- Estimated GSMaP summaries: 8806
- Estimated GFS summaries: 8806
- Estimated NASA POWER requests: 4931

These are sample-based operational estimates, not guarantees.

## Charges and quotas

No quota, rate-limit, or billing error was returned. Actual account charges are not visible locally and must be checked in Google Cloud Billing.

## Safety

`environmental_conditions_score` is a transparent conditions index, not a validated GLOF probability. No mock data is substituted after REAL-source failures.

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.
