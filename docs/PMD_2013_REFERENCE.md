# PMD-2013 historical reference integration

**Pakistan Meteorological Department (PMD) — Glacial Lakes Inventory 2013**

Badge: **PMD Official Inventory · 2013**

**Historical reference inventory — not a live warning feed.**

## Isolation contract

This is an independent local reference package, not an active inventory replacement.
It never calls the older `reconcile-inventory` command, populates `official_lakes`,
changes `public.lakes`, or refreshes Sentinel-2/GSMaP/GFS/NASA POWER data.
The uploaded attribution is retained as provenance; publisher authenticity has not
been independently verified. Geometry and spatial crosswalk decisions require review.

The pre-existing deletion of the tracked `glofguard/` Python package is intentionally
untouched. This integration lives in `pmd_reference/` and can run independently.

## Reproduce locally

Dependencies already available in this workspace: GeoPandas 1.1.4, Shapely 2.1.2,
PyProj 3.7.2, pandas and Python's SQLite. No new packages or credentials are needed.

```powershell
.\.python\python.exe -m pmd_reference
```

Default inputs and outputs:

| Path | Purpose |
|---|---|
| `data/Glacial lakes_2013/` | Original supplied shapefile and sidecars; never edited |
| `data/raw/pmd_glacial_lakes_2013/source/` | Byte-identical preservation copy of all eight supplied files |
| `data/raw/pmd_glacial_lakes_2013/manifest.json` | Source component sizes and SHA-256 hashes |
| `data/map/glof_map.sqlite3` | Read-only current 8,806-lake inventory input |
| `data/reference/pmd_2013/` | Separate generated reference and review artifacts |

The builder refuses an existing output directory. For a repeat validation, use a
new review-version directory rather than overwriting previously reviewed evidence:

```powershell
.\.python\python.exe -m pmd_reference --output data/reference/pmd_2013_review_2
```

Raw-copy conflicts fail closed instead of replacing source data. No environment
file is loaded and no remote import command exists in this module.

## Review artifacts

Within `data/reference/pmd_2013/`:

- `normalized_lakes.geojson`: all 3,044 unique basin/PMD identities. Quarantined
  geometry is `null`, not a guessed centroid or dissolved cross-basin footprint.
- `source_rows.jsonl`: all 3,080 rows, all original attributes, original projected
  geometry, geometry validation and one-based source-row references.
- `row_memberships.jsonl`: all 3,252 raw-row/basin-ID associations, including the
  explicit resolution and geometry-selection decision for every association.
- `crosswalk.jsonl`: every PMD identity, its proposed current-lake match or
  unmatched/ambiguous state, candidate counts and distance rules.
- `current_coverage.jsonl`: every current lake, including all unmatched lakes.
- `match_candidates.jsonl`: every in-radius spatial pair considered, including
  rejected/ambiguous competitors.
- `pmd_2013_reference.sqlite3`: the same evidence in a **standalone** local database.
  SQLite files are ignored by the repository's existing Git rules; the portable
  GeoJSON/JSONL and reports remain available separately.
- `validation_report.json`, `VALIDATION_REPORT.md`: exact counts, decisions,
  caveats, source hashes and unchanged-live-data verification.
- `output_manifest.json`: integrity hashes for every other delivered artifact
  (the manifest cannot hash itself).

Local tables and proposed future remote destinations:

| Standalone SQLite table | Proposed remote table, NOT created |
|---|---|
| `pmd_2013_lakes` | `reference.pmd_2013_lakes` |
| `pmd_2013_source_rows` | `reference.pmd_2013_source_rows` |
| `pmd_2013_row_memberships` | `reference.pmd_2013_row_memberships` |
| `pmd_2013_crosswalk` | `reference.pmd_2013_crosswalk` |
| `pmd_2013_current_coverage` | `reference.pmd_2013_current_coverage` |
| `pmd_2013_match_candidates` | `reference.pmd_2013_match_candidates` |
| `pmd_2013_metadata` | `reference.pmd_2013_metadata` |

No Supabase schema, migration, grants, policies or data have been changed. Review
the normalized output and report first. Remote design must then be approved in a
proper migration with restricted access; the `reference` schema should not become
publicly writable. A separate explicit import approval is required.

## Geometry and matching rules

1. Read all ten basin-specific identifier fields. One source row may refer to more
   than one basin/lake. Group by `(basin, original PMD lake ID)`, never by PKGL ID.
2. Retain unassigned source rows. Do not invent missing PMD identifiers to force a count.
3. Retain every duplicate association. Identical geometries for the same ID can share
   one representation; conflicting variants, multi-basin assignments and geometry
   shared with another ID require review. Never union cross-assigned shapes.
4. Retain invalid or very small geometry unchanged in the source ledger; do not
   silently repair it. Under 10 m² is a conservative review threshold, not a PMD rule.
5. Compare eligible PMD polygons to current centroids in EPSG:32643, with a 750 m
   candidate radius. A proposal requires mutual nearest candidates separated from
   the runner-up by more than 100 m on both sides. Invalid current geometry cannot
   become an automatic proposal. All ties and competitors remain inspectable.
6. Proposals are not approved equivalences. Different survey years, centroid offsets
   and geometry defects limit interpretation. An unmatched lake is not evidence of
   a new lake or a disappearance.

GeoJSON uses WGS84/EPSG:4326; original geometry remains EPSG:32643. Attribute DMS
coordinates are not substituted for geometry: the supplied Shingo latitude field
contains duplicated longitude text. The verified shapefile CRS governs transforms.

## Tests

```powershell
.\.python\python.exe -m unittest discover -s tests -p test_pmd_reference.py -v
cd web
npm run test:reference
npx tsc --noEmit
npm run lint
```

The historical source panel is separate from current monitoring rows. It does not
attribute all PKGL lakes to PMD and explicitly marks the local reference as pending
review and not imported to Supabase.
