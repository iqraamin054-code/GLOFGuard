# Representative 100-Lake REAL-Data Pilot Report

Report state: **COMPLETE**  
Exact command: `"C:\Users\Essa Ahmed\Desktop\Ayesha\GLOF\.python\python.exe" -m glofguard.cli full-inventory --output-dir output\full_inventory --representative-pilot-size 100 --pilot-seed 20260901 --batch-size 5 --rate-limit-seconds 1.0 --max-retries 3 --backoff-base-seconds 2 --backoff-cap-seconds 30`

## Selection design

The deterministic sample contains 100 unique lakes and covers 81 populated strata formed from three latitude bands, three longitude bands, three elevation bands, and three log-area bands. Every populated stratum receives at least one lake; remaining positions are allocated in proportion to stratum population. The stratified full-run estimates use the recorded expansion weights.

Selected latitude range: 34.70407 to 36.87354  
Selected longitude range: 71.19429 to 77.20819  
Selected elevation range: 2867.0 to 5185.0 m  
Selected reference-area range: 0.00012881 to 0.24782931 km2

## Outcomes

| Outcome | Lakes |
|---|---:|
| Fresh REAL observations | 0 |
| Stale REAL observations | 34 |
| No usable imagery | 66 |
| Provider failures | 0 |
| MOCK observations | 0 |
| Pending | 0 |

## Runtime and measured API usage

- Runtime across resumed pilot sessions: 962.953 seconds (0.267 hours)
- Lakes attempted during pilot sessions: 100
- Sentinel scene lookups: 100
- Sentinel scene measurements: 632
- NASA POWER HTTP requests: 100

## Stratified full-run estimates

- Estimated REAL-usable rate (fresh plus stale): **34.63%**
- Estimated fresh-REAL rate: **0.00%**
- Estimated duration for 8,806 unique lakes: **23.55 hours**
- Estimated Sentinel lookups: 8806
- Estimated Sentinel scene measurements: 55654
- Estimated NASA POWER requests: 8806

These are sample-based operational estimates, not guarantees. Weather and image availability can vary geographically and over time.

## Charges and quotas

No quota, rate-limit, or billing error was returned by either provider. Actual account charges are not visible to this local process and must be confirmed in the Google Cloud Billing console.

## Safety and interpretation

`environmental_conditions_score` is not a validated GLOF probability and does not predict that a flood will occur. No mock data is used as a fallback for failed REAL requests.

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.
