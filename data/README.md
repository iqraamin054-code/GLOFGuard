# Collaborator data guide

This repository keeps lightweight, reproducible source data alongside the code.
Supabase is the shared operational datastore; local SQLite files are temporary
migration sources/backups and deliberately remain ignored.

## Included in Git

- `raw/`, `processed/`, and `final/`: the model-training source and derived CSVs.
- `map/lakes_map.geojson`: display geometry.
- `Glacial lakes_2013/`: the unchanged PMD 2013 shapefile archive, added as an
  authoritative historical source.
- `reference/pmd_2013/`: normalized PMD-2013 rows, membership, crosswalk, and
  candidate-match outputs for spatial/reference analysis. These remain a
  historical reference and must not be treated as live environmental data or
  automatically used as model labels.
- `baseline_susceptibility.csv`: the 8,806-lake baseline input used by the
  reviewed Supabase migration.

## Intentionally excluded

- `.env` and all credentials.
- `*.sqlite3`, WAL/SHM files, and historical SQLite backups. These are neither
  needed to run the shared Supabase-backed deployment nor suitable for normal
  GitHub storage.
- Supabase data itself: collaborators use the reviewed migration and their own
  securely configured backend database access; browser clients receive no
  private-schema access.

## Reproducibility

The reviewed migration, source-row accounting, and checksums are in
`output/supabase_migration_review_v4.json`. Run its tests before any database
transfer. Do not upload credentials or force-add ignored SQLite archives.
