# Spatial Database Import Report

Import state: **VERIFIED**

- Database backend: SQLite with RTree spatial index
- Supabase/PostGIS configured: No; `database/postgis_schema.sql` is ready for later use
- Unique lakes imported: 8,806
- Original reconciliation rows preserved: 8,808
- Duplicate mappings preserved: 2
- Baseline susceptibility rows: 8,806
- Corrected REAL pilot observations: 100
- Source-freshness rows: 400
- Processing-queue rows: 8,806
- Spatial-index rows: 8,806
- Foreign-key violations: 0
- Mock pilot observations: 0
- Full remaining-inventory processing launched: No

## Map statuses

- LIVE_COMPLETE: 34
- LIVE_PENDING: 8,706
- SATELLITE_STALE: 19
- SATELLITE_UNAVAILABLE: 47

`LIVE_PENDING`, `SATELLITE_UNAVAILABLE`, and other incomplete states are not
interpreted as safe. The environmental conditions score is stored and displayed
only as a research index, never as a GLOF probability.

## Evidence

- Evidence version: `corrected-100-real-v1.0.0`
- Evidence manifest SHA-256: `10e3cb15f6a4bb31fceca698461a4f2159df9f7ff2e595a978eb679bddd6804c`
- Inventory version: `hkh-pk-2020-reconciled-8808-v1.0.0`
- Dynamic data/model version: `environmental-conditions-index-v1.1.1-source-freshness`

> This research prototype provides environmental risk indicators and is not an official emergency-warning system. High-risk results require expert verification.
