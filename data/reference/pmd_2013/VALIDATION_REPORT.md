# PMD Glacial Lakes Inventory 2013 — validation and reconciliation

Pakistan Meteorological Department (PMD) — Glacial Lakes Inventory 2013

Historical reference inventory — not a live warning feed.

Status: **LOCAL REVIEW CANDIDATE — NO SUPABASE IMPORT**.

## Counts

| Measurement | Result |
|---|---:|
| raw_feature_rows | 3,080 |
| unique_pmd_lakes | 3,044 |
| row_id_memberships | 3,252 |
| extra_id_memberships_collapsed | 208 |
| repeated_pmd_identifiers | 17 |
| current_inventory_lakes | 8,806 |
| spatial_matches_proposed | 1,945 |
| unmatched_pmd_lakes | 1,099 |
| unmatched_current_lakes | 6,861 |
| candidate_pairs | 10,033 |

## Basin validation

| Basin | Expected | Normalized |
|---|---:|---:|
| Swat | 214 | 214 |
| Chitral | 116 | 116 |
| Gilgit | 660 | 660 |
| Hunza | 216 | 216 |
| Shigar | 110 | 110 |
| Shyok | 270 | 270 |
| Indus | 815 | 815 |
| Shingo | 247 | 247 |
| Astore | 196 | 196 |
| Jhelum | 200 | 200 |

## Row and geometry accounting

3,080 raw rows are NOT 3,080 independent lakes, and 36 is NOT a defensible duplicate count. Every nonblank basin-specific identifier becomes a row membership; those memberships are grouped by basin plus original PMD ID. No records are trimmed to reach 3,044. Rows without any basin ID are retained in source_rows, not assigned invented IDs.

Raw row statuses: `{"MULTI_BASIN": 31, "NO_BASIN_ID": 3, "SINGLE_BASIN": 3046}`.

Raw geometry statuses: `{"DEGENERATE_AREA": 5, "INVALID": 5, "VALID": 3070}`.

Normalized geometry statuses: `{"AMBIGUOUS": 30, "INVALID": 5, "VALID": 3009}`.

All raw attributes and original EPSG:32643 geometry are retained in source_rows.jsonl and the standalone SQLite source-row table. row_memberships.jsonl records each ID association, its resolution, and whether it supplies the normalized geometry. Source-row references are one-based shapefile feature positions.

Different geometry variants, multi-basin rows, or geometry shared by different IDs are quarantined: the lake identity remains present but normalized geometry is null. No union or arbitrary largest/first polygon is used to resolve conflicting geometry. Invalid, missing, or area-under-10-m² geometry is also excluded from matching; the 10-m² threshold is a conservative review heuristic, not a PMD scientific cutoff. Identical geometry within the same ID can be collapsed while retaining all row references. No raw geometry is repaired.

### Identities requiring geometry review

| PMD ID | Source rows | Status | Issues |
|---|---|---|---|
| PMD2013:Astore:Ast_gl_164 | 2579 | INVALID | INVALID |
| PMD2013:Astore:Ast_gl_174 | 2611, 3054, 3055, 3056, 3057, 3058, 3059, 3060, 3061, 3062, 3063, 3064, 3065, 3066, 3067, 3068, 3069, 3070, 3071, 3072, 3073, 3074, 3075, 3076, 3077 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Astore:Ast_gl_87 | 3053 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Chitral:Chi_gl_116 | 2450, 3054, 3056, 3058, 3060, 3062, 3064, 3066, 3068, 3070, 3072, 3074, 3076 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Chitral:Chi_gl_81 | 2483 | INVALID | INVALID |
| PMD2013:Chitral:Chi_gl_83 | 2461, 3055, 3057, 3059, 3061, 3063, 3065, 3067, 3069, 3071, 3073, 3075, 3077 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Gilgit:Gil_gl_19 | 1861, 3054, 3055, 3062, 3063, 3070, 3071 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS, DEGENERATE_AREA |
| PMD2013:Gilgit:Gil_gl_463 | 2246, 3060, 3061, 3068, 3069, 3076, 3077 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Gilgit:Gil_gl_49 | 2160, 3056, 3057, 3064, 3065, 3072, 3073 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Gilgit:Gil_gl_633 | 2194, 3058, 3059, 3066, 3067, 3074, 3075 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Hunza:Hunz_gl_16 | 1624 | INVALID | INVALID |
| PMD2013:Hunza:Hunz_gl_177 | 1611, 3054, 3055, 3056, 3057, 3058, 3059, 3060, 3061, 3062, 3063, 3064, 3065, 3066, 3067, 3068, 3069, 3070, 3071, 3072, 3073, 3074, 3075, 3076, 3077 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Indus:Ind_gl_142 | 3033 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Indus:Ind_gl_203 | 3035 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Indus:Ind_gl_301 | 3034 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Indus:Ind_gl_361 | 1145 | INVALID | INVALID |
| PMD2013:Indus:Ind_gl_627 | 3052 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_189 | 595, 3054, 3055, 3056, 3057, 3058, 3059, 3060, 3061 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_19 | 3049 | AMBIGUOUS | GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_200 | 3070, 3071, 3072, 3073, 3074, 3075, 3076, 3077 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_24 | 3047 | AMBIGUOUS | GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_27 | 3045 | AMBIGUOUS | GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_42 | 3051 | AMBIGUOUS | GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_47 | 3043 | AMBIGUOUS | GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_50 | 3041 | AMBIGUOUS | GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Jhelum:Jhe_gl_55 | 655, 3040, 3042, 3044, 3046, 3048, 3050, 3052, 3053, 3062, 3063, 3064, 3065, 3066, 3067, 3068, 3069 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS, DEGENERATE_AREA |
| PMD2013:Shigar:Shig_gl_2 | 485, 3054, 3055, 3056, 3057, 3058, 3059, 3060, 3061, 3062, 3063, 3064, 3065, 3066, 3067, 3068, 3069, 3070, 3071, 3072, 3073, 3074, 3075, 3076, 3077 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Shigar:Shig_gl_70 | 3032 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Shigar:Shig_gl_81 | 568, 3037 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Shigar:Shig_gl_82 | 569, 3039 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Shigar:Shig_gl_83 | 463, 3036, 3038 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Shigar:Shig_gl_91 | 3031 | AMBIGUOUS | MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Shingo:Shin_gl_127 | 220, 3054, 3055, 3056, 3057, 3058, 3059, 3060, 3061, 3062, 3063, 3064, 3065, 3066, 3067, 3068, 3069, 3070, 3071, 3072, 3073, 3074, 3075, 3076, 3077 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS |
| PMD2013:Shingo:Shin_gl_151 | 362 | INVALID | INVALID |
| PMD2013:Swat:Swat_gl_181 | 28, 3031, 3032, 3033, 3034, 3035, 3054, 3055, 3056, 3057, 3058, 3059, 3060, 3061, 3062, 3063, 3064, 3065, 3066, 3067, 3068, 3069, 3070, 3071, 3072, 3073, 3074, 3075, 3076, 3077 | AMBIGUOUS | DISTINCT_GEOMETRY_VARIANTS, MULTI_BASIN_ASSIGNMENT, GEOMETRY_SHARED_BY_DIFFERENT_IDS, DEGENERATE_AREA |

## Spatial crosswalk limits

Matches are reviewable spatial proposals, NOT PMD-certified equivalences. PMD polygons are compared with the current inventory's centroids in EPSG:32643. Candidate radius: 750 metres; require mutual nearest candidates and more than 100 metres of separation from the second candidate on both sides. All ties/competing matches remain unresolved. Current invalid-source-geometry records remain visible but cannot be accepted automatically. All in-radius candidate pairs are saved. Geometry-quarantined PMD identities have no automatic spatial candidates.

These conservative thresholds are configurable in the matching function and have not been calibrated against labeled matches. UTM distances are projected approximations, and the current data exposes centroids rather than lake footprints. A failure to match does not prove a lake disappeared or is newly formed. Different inventory years, geometry defects and centroid offsets may explain mismatches. PMD IDs are never compared to PKGL IDs as equivalent keys.

PMD outcomes: `{"AMBIGUOUS_SPATIAL_CANDIDATES": 658, "NO_SPATIAL_CANDIDATE": 406, "PMD_GEOMETRY_REVIEW_REQUIRED": 35, "SPATIAL_MATCH_PROPOSED": 1945}`.

Current outcomes: `{"CURRENT_GEOMETRY_REVIEW_REQUIRED": 103, "NO_SPATIAL_CANDIDATE": 3848, "SPATIAL_MATCH_PROPOSED": 1945, "UNRESOLVED_CANDIDATES": 2910}`.

crosswalk.jsonl includes every PMD lake, including unmatched and ambiguous entries. current_coverage.jsonl includes every one of the 8,806 current lakes, including unmatched entries. Review candidate distances in match_candidates.jsonl before accepting any match.

## Preservation and provenance

The supplied directory is untouched. All eight supplied shapefile components are copied byte-for-byte into data/raw/pmd_glacial_lakes_2013/source; the manifest records SHA-256 hashes. No original ZIP archive was supplied in that directory. Attribution to PMD and year 2013 follows the user's supplied source description, not an independent publisher-signature verification.

The current map SQLite database, live-pipeline SQLite database and observation CSV were read-only inputs (where present). Their before/after SHA-256 hashes match. No public.lakes, official_lakes, current environmental observations or source measurements were modified. This package imports no live pipeline or Supabase client and never loads .env.

## Exact review artifacts and future tables

Local files: normalized_lakes.geojson, source_rows.jsonl, row_memberships.jsonl, crosswalk.jsonl, current_coverage.jsonl, match_candidates.jsonl, pmd_2013_reference.sqlite3, validation_report.json, VALIDATION_REPORT.md and output_manifest.json.

Standalone SQLite tables (not live database tables):

- `pmd_2013_lakes`
- `pmd_2013_source_rows`
- `pmd_2013_row_memberships`
- `pmd_2013_crosswalk`
- `pmd_2013_current_coverage`
- `pmd_2013_match_candidates`
- `pmd_2013_metadata`

Proposed remote destinations only: the same seven names in a separate `reference` schema, subject to review of geometry decisions, match thresholds, schema exposure and restricted grants. No remote schema or migration has been created. No remote import command is included. A reviewed migration and a separate explicit import approval are required; do not apply old inventory-replacement commands to this dataset.

The existing glofguard/ package was already deleted from the working tree when this task started. Those deletions were preserved; the new pmd_reference module runs independently. The historical source badge belongs to a separate UI reference panel, not to current measurements or every PKGL lake.
