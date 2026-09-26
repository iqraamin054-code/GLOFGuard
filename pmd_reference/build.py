"""Reproducible PMD-2013 normalization; no network or live-database writes."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely import STRtree, normalize
from shapely.geometry import Point, box, mapping
from shapely.ops import transform
from shapely.validation import explain_validity

ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAME = "Pakistan Meteorological Department (PMD) — Glacial Lakes Inventory 2013"
ORGANIZATION = "Pakistan Meteorological Department (PMD)"
ROLE = "OFFICIAL_HISTORICAL_REFERENCE"
BADGE = "PMD Official Inventory · 2013"
DISCLAIMER = "Historical reference inventory — not a live warning feed."
# These are separate basin-specific fields, not interchangeable global IDs.
BASINS = {
    "Swat": ("LAKE_NO", "Swat", 214),
    "Chitral": ("Lake_No_17", "Chi", 116),
    "Gilgit": ("Lake_No_16", "Gil", 660),
    "Hunza": ("Lake_No_15", "Hunz", 216),
    "Shigar": ("Lake_No_12", "Shig", 110),
    "Shyok": ("Lake_No_19", "Shyk", 270),
    "Indus": ("LAKE_NO_14", "Ind", 815),
    "Shingo": ("Lake_No_1", "Shin", 247),
    "Astore": ("Lake_No_18", "Ast", 196),
    "Jhelum": ("Lake_No_13", "Jhe", 200),
}
TABLES = (
    "pmd_2013_lakes", "pmd_2013_source_rows", "pmd_2013_row_memberships",
    "pmd_2013_crosswalk", "pmd_2013_current_coverage", "pmd_2013_match_candidates",
    "pmd_2013_metadata",
)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False,
                               sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(dumps(row) + "\n")


def preserve_source(source: Path, destination: Path) -> dict:
    """Copy all supplied components byte-for-byte; refuse to replace changed raw data."""
    source, destination = source.resolve(), destination.resolve()
    if not source.is_dir() or destination == source or source in destination.parents:
        raise ValueError("Source and raw preservation directories must be separate")
    files = sorted(p for p in source.rglob("*") if p.is_file())
    if not files or any(p.is_symlink() for p in files):
        raise ValueError("Source must contain regular files, not symlinks")
    entries = []
    for path in files:
        relative = path.relative_to(source)
        target = destination / relative
        sha = digest(path)
        if target.exists() and digest(target) != sha:
            raise ValueError(f"Raw preservation conflict: {relative}; nothing overwritten")
        entries.append({"file": relative.as_posix(), "bytes": path.stat().st_size,
                        "sha256": sha})
    if destination.exists():
        existing = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
        if existing - {row["file"] for row in entries}:
            raise ValueError("Raw destination contains unexpected files")
    for entry in entries:
        target = destination / entry["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source / entry["file"], target)
        if digest(target) != entry["sha256"] or digest(source / entry["file"]) != entry["sha256"]:
            raise RuntimeError("Source changed during preservation")
    return {"source_name": SOURCE_NAME, "provenance_basis": "User-supplied PMD attribution",
            "original_directory": source.name, "files": entries,
            "byte_identical_copy_verified": True}


def clean_scalar(value):
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def normalize_source(frame: gpd.GeoDataFrame, *, strict: bool = True):
    """Keep every source row and ID membership; never union cross-assigned geometry."""
    if frame.crs is None or frame.crs.to_epsg() != 32643:
        raise ValueError("PMD source CRS must be inspected EPSG:32643; refusing to guess")
    if strict and len(frame) != 3080:
        raise ValueError(f"Expected 3080 raw rows, found {len(frame)}")
    required = {spec[0] for spec in BASINS.values()}
    if not required.issubset(frame.columns):
        raise ValueError("Missing basin-specific PMD identifier columns")
    rows, memberships = [], []
    groups = defaultdict(list)
    geometry_owners = defaultdict(set)
    geometries = {}
    for index, (_, source) in enumerate(frame.iterrows()):
        row_id = index + 1  # one-based feature position, independent of dataframe index
        geom = source.geometry
        present = geom is not None and not geom.is_empty
        fingerprint = hashlib.sha256(normalize(geom).wkb).hexdigest() if present else None
        ids = []
        for basin, (column, prefix, _) in BASINS.items():
            original = clean_scalar(source[column])
            if original is None or not str(original).strip():
                continue
            original = str(original).strip()
            if not re.fullmatch(re.escape(prefix) + r"_gl_[1-9][0-9]*", original):
                raise ValueError(f"Unexpected ID in row {row_id}, column {column}: {original}")
            key = f"PMD2013:{basin}:{original}"
            ids.append(key)
            memberships.append({"source_row": row_id, "pmd_id": key, "basin": basin,
                                "original_pmd_lake_id": original, "id_column": column})
            groups[key].append(row_id)
            if fingerprint:
                geometry_owners[fingerprint].add(key)
        status = ("MISSING" if not present else "INVALID" if not geom.is_valid
                  else "DEGENERATE_AREA" if geom.area < 10 else "VALID")
        rows.append({"source_row": row_id, "source_row_reference": f"na_lakes.shp#feature={row_id}",
                     "membership_count": len(ids), "pmd_ids": ids,
                     "row_status": "NO_BASIN_ID" if not ids else "MULTI_BASIN" if len(ids) > 1 else "SINGLE_BASIN",
                     "geometry_validation_status": status,
                     "geometry_validation_detail": explain_validity(geom) if present else "No geometry",
                     "geometry_sha256": fingerprint, "geometry_area_m2": float(geom.area) if present else None,
                     "geometry_type": geom.geom_type if present else None,
                     "geometry_wkt_epsg32643": geom.wkt if present else None,
                     "attributes": {str(c): clean_scalar(source[c]) for c in frame.columns if c != frame.geometry.name}})
        geometries[row_id] = geom
    by_row = {row["source_row"]: row for row in rows}
    lakes, selected_geometries = [], {}
    for key, row_ids in sorted(groups.items()):
        members = [by_row[n] for n in row_ids]
        fingerprints = {r["geometry_sha256"] for r in members if r["geometry_sha256"]}
        reasons = []
        if len(fingerprints) > 1:
            reasons.append("DISTINCT_GEOMETRY_VARIANTS")
        if any(r["membership_count"] > 1 for r in members):
            reasons.append("MULTI_BASIN_ASSIGNMENT")
        if any(len(geometry_owners[h]) > 1 for h in fingerprints):
            reasons.append("GEOMETRY_SHARED_BY_DIFFERENT_IDS")
        ambiguous = bool(reasons)
        raw_states = {r["geometry_validation_status"] for r in members}
        status = ("AMBIGUOUS" if ambiguous else "MISSING" if "MISSING" in raw_states
                  else "INVALID" if "INVALID" in raw_states else "DEGENERATE_AREA"
                  if "DEGENERATE_AREA" in raw_states else "VALID")
        # No repair, centroid substitution, union, or geographically convenient selection.
        selected = min(row_ids) if status == "VALID" else None
        basin, original = key.split(":", 2)[1:]
        decision = ("QUARANTINED_GEOMETRY_RETAIN_ALL_ROWS" if status != "VALID" else
                    "IDENTICAL_GEOMETRY_COLLAPSED_KEEP_ALL_REFERENCES" if len(row_ids) > 1 else "UNIQUE_SOURCE_ROW")
        lake = {"pmd_id": key, "original_pmd_lake_id": original, "basin": basin,
                "source_organization": ORGANIZATION, "source_year": 2013, "source_role": ROLE,
                "source_name": SOURCE_NAME, "source_row_references": row_ids,
                "source_row_count": len(row_ids), "geometry_variant_count": len(fingerprints),
                "geometry_validation_status": status, "geometry_ambiguous": ambiguous,
                "geometry_issues": reasons + sorted(raw_states - {"VALID"}),
                "resolution": decision, "selected_source_row": selected,
                "review_required": status != "VALID", "geometry_wkt_epsg32643": geometries[selected].wkt if selected else None}
        lakes.append(lake)
        selected_geometries[key] = geometries[selected] if selected else None
    by_key = {r["pmd_id"]: r for r in lakes}
    for member in memberships:
        lake = by_key[member["pmd_id"]]
        member["resolution"] = lake["resolution"]
        member["used_for_normalized_geometry"] = member["source_row"] == lake["selected_source_row"]
        member["geometry_review_required"] = lake["review_required"]
    counts = dict(Counter(r["basin"] for r in lakes))
    if strict and counts != {basin: spec[2] for basin, spec in BASINS.items()}:
        raise ValueError(f"PMD basin totals mismatch; output not approved: {counts}")
    return lakes, rows, memberships, selected_geometries


def load_current(path: Path, *, expected_count: int = 8806) -> list[dict]:
    """Only SELECTs against the existing reconciled map inventory, opened read-only."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        columns = {r[1] for r in connection.execute("PRAGMA table_info(lakes)")}
        required = {"lake_id", "latitude", "longitude", "source_geometry_valid", "inventory_version"}
        if not required <= columns:
            raise ValueError("Current inventory does not have the inspected map-lakes schema")
        rows = [dict(r) for r in connection.execute(
            "SELECT lake_id,latitude,longitude,source_geometry_valid,inventory_version FROM lakes ORDER BY lake_id")]
    finally:
        connection.close()
    if len(rows) != expected_count or len({r["lake_id"] for r in rows}) != expected_count:
        raise ValueError("Current inventory count/unique IDs differ from expected; refusing substitution")
    return rows


def crosswalk(lakes: list[dict], geometries: dict, current: list[dict], *, radius_m=750.0, margin_m=100.0):
    """Conservative one-to-one spatial proposals, never official ID equivalences."""
    if not math.isfinite(radius_m) or radius_m <= 0 or not math.isfinite(margin_m) or margin_m < 0:
        raise ValueError("Spatial thresholds must be finite and nonnegative (radius positive)")
    projector = Transformer.from_crs(4326, 32643, always_xy=True)
    points, point_rows = [], []
    for row in current:
        lat, lon = row["latitude"], row["longitude"]
        valid = (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                 and math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)
        if valid:
            points.append(Point(*projector.transform(lon, lat)))
            point_rows.append(row)
    tree = STRtree(points)
    candidates, by_pmd, by_current = [], defaultdict(list), defaultdict(list)
    for lake in lakes:
        geom = geometries[lake["pmd_id"]]
        if geom is None:
            continue
        xmin, ymin, xmax, ymax = geom.bounds
        for index in tree.query(box(xmin-radius_m, ymin-radius_m, xmax+radius_m, ymax+radius_m)):
            row, point = point_rows[int(index)], points[int(index)]
            distance = float(geom.distance(point))
            if distance > radius_m:
                continue
            candidate = {"pmd_id": lake["pmd_id"], "current_lake_id": row["lake_id"],
                         "distance_m": distance, "current_point_within_pmd": bool(geom.covers(point)),
                         "current_geometry_valid": bool(row["source_geometry_valid"]),
                         "method": "PMD_POLYGON_TO_CURRENT_CENTROID_EPSG32643",
                         "status": "REVIEW_CANDIDATE"}
            candidates.append(candidate)
            by_pmd[lake["pmd_id"]].append(candidate)
            by_current[row["lake_id"]].append(candidate)
    for collection in (by_pmd, by_current):
        for values in collection.values():
            values.sort(key=lambda r: (r["distance_m"], r["pmd_id"], r["current_lake_id"]))
    def separated(values):
        return len(values) == 1 or values[1]["distance_m"] - values[0]["distance_m"] > margin_m
    matches, matched_current = [], {}
    for lake in lakes:
        choices = by_pmd[lake["pmd_id"]]
        accepted = None
        status = "PMD_GEOMETRY_REVIEW_REQUIRED" if lake["review_required"] else "NO_SPATIAL_CANDIDATE"
        if choices:
            first = choices[0]
            reverse = by_current[first["current_lake_id"]]
            if (first["current_geometry_valid"] and separated(choices) and separated(reverse)
                    and reverse[0]["pmd_id"] == lake["pmd_id"]):
                accepted = first
                status = "SPATIAL_MATCH_PROPOSED"
                first["status"] = status
                matched_current[first["current_lake_id"]] = lake["pmd_id"]
            else:
                status = "AMBIGUOUS_SPATIAL_CANDIDATES"
        matches.append({"pmd_id": lake["pmd_id"], "original_pmd_lake_id": lake["original_pmd_lake_id"],
                        "basin": lake["basin"], "current_lake_id": accepted["current_lake_id"] if accepted else None,
                        "status": status, "candidate_count": len(choices),
                        "distance_m": accepted["distance_m"] if accepted else None,
                        "nearest_candidate_lake_id": choices[0]["current_lake_id"] if choices else None,
                        "nearest_candidate_distance_m": choices[0]["distance_m"] if choices else None,
                        "review_status": "PENDING_HUMAN_REVIEW", "radius_m": radius_m, "ambiguity_margin_m": margin_m})
    coverage = [{**r, "pmd_id": matched_current.get(r["lake_id"]),
                 "status": "SPATIAL_MATCH_PROPOSED" if r["lake_id"] in matched_current else
                 "CURRENT_GEOMETRY_REVIEW_REQUIRED" if not r["source_geometry_valid"] else
                 "UNRESOLVED_CANDIDATES" if by_current[r["lake_id"]] else "NO_SPATIAL_CANDIDATE",
                 "candidate_count": len(by_current[r["lake_id"]])} for r in current]
    return matches, coverage, sorted(candidates, key=lambda r: (r["pmd_id"], r["distance_m"], r["current_lake_id"]))


def create_reference_db(path: Path, lakes, rows, memberships, matches, coverage, candidates, summary):
    """New standalone database only, never attach or modify a live database."""
    if path.exists():
        raise ValueError("Reference database already exists; refusing overwrite")
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
        CREATE TABLE pmd_2013_source_rows (source_row INTEGER PRIMARY KEY, geometry_validation_status TEXT NOT NULL, row_status TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE pmd_2013_lakes (pmd_id TEXT PRIMARY KEY, original_pmd_lake_id TEXT NOT NULL, basin TEXT NOT NULL,
            source_organization TEXT NOT NULL, source_year INTEGER NOT NULL CHECK(source_year=2013),
            source_role TEXT NOT NULL CHECK(source_role='OFFICIAL_HISTORICAL_REFERENCE'),
            geometry_validation_status TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(basin,original_pmd_lake_id));
        CREATE TABLE pmd_2013_row_memberships (source_row INTEGER NOT NULL REFERENCES pmd_2013_source_rows(source_row),
            pmd_id TEXT NOT NULL REFERENCES pmd_2013_lakes(pmd_id), resolution TEXT NOT NULL,
            payload_json TEXT NOT NULL, PRIMARY KEY(source_row,pmd_id));
        CREATE TABLE pmd_2013_current_coverage (lake_id TEXT PRIMARY KEY, pmd_id TEXT REFERENCES pmd_2013_lakes(pmd_id), status TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE pmd_2013_crosswalk (pmd_id TEXT PRIMARY KEY REFERENCES pmd_2013_lakes(pmd_id),
            current_lake_id TEXT UNIQUE REFERENCES pmd_2013_current_coverage(lake_id), status TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE pmd_2013_match_candidates (pmd_id TEXT NOT NULL REFERENCES pmd_2013_lakes(pmd_id),
            current_lake_id TEXT NOT NULL REFERENCES pmd_2013_current_coverage(lake_id), distance_m REAL NOT NULL CHECK(distance_m>=0),
            payload_json TEXT NOT NULL, PRIMARY KEY(pmd_id,current_lake_id));
        CREATE TABLE pmd_2013_metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
        CREATE INDEX pmd_2013_lakes_basin_idx ON pmd_2013_lakes(basin);
        CREATE INDEX pmd_2013_memberships_id_idx ON pmd_2013_row_memberships(pmd_id);
        CREATE INDEX pmd_2013_candidates_current_idx ON pmd_2013_match_candidates(current_lake_id);
        """)
        connection.executemany("INSERT INTO pmd_2013_source_rows VALUES (?,?,?,?)", [(r["source_row"],r["geometry_validation_status"],r["row_status"],dumps(r)) for r in rows])
        connection.executemany("INSERT INTO pmd_2013_lakes VALUES (?,?,?,?,?,?,?,?)", [(r["pmd_id"],r["original_pmd_lake_id"],r["basin"],ORGANIZATION,2013,ROLE,r["geometry_validation_status"],dumps(r)) for r in lakes])
        connection.executemany("INSERT INTO pmd_2013_row_memberships VALUES (?,?,?,?)", [(r["source_row"],r["pmd_id"],r["resolution"],dumps(r)) for r in memberships])
        connection.executemany("INSERT INTO pmd_2013_current_coverage VALUES (?,?,?,?)", [(r["lake_id"],r["pmd_id"],r["status"],dumps(r)) for r in coverage])
        connection.executemany("INSERT INTO pmd_2013_crosswalk VALUES (?,?,?,?)", [(r["pmd_id"],r["current_lake_id"],r["status"],dumps(r)) for r in matches])
        connection.executemany("INSERT INTO pmd_2013_match_candidates VALUES (?,?,?,?)", [(r["pmd_id"],r["current_lake_id"],r["distance_m"],dumps(r)) for r in candidates])
        connection.execute("INSERT INTO pmd_2013_metadata VALUES (?,?)", ("validation",dumps(summary)))
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Reference foreign-key validation failed")
        connection.commit()
    finally:
        connection.close()


def build(source: Path, raw_root: Path, output: Path, current_db: Path) -> dict:
    source, raw_root, output, current_db = (p.resolve() for p in (source,raw_root,output,current_db))
    if output.exists():
        raise ValueError("Output directory already exists; use a new directory for a new review version")
    if any(parent == child or parent in child.parents or child in parent.parents
           for parent, child in ((source, output), (raw_root, output), (source, raw_root))):
        raise ValueError("Source, raw and output directories must not overlap")
    protected = [p for p in (current_db, ROOT/"data/glofguard.sqlite3", ROOT/"data/lake_observations.csv") if p.exists()]
    before = {str(p):digest(p) for p in protected}
    raw_manifest = preserve_source(source, raw_root/"source")
    write_json(raw_root/"manifest.json",raw_manifest)
    frame = gpd.read_file(raw_root/"source/na_lakes.shp")
    lakes, rows, memberships, geometries = normalize_source(frame)
    current = load_current(current_db)
    matches, coverage, candidates = crosswalk(lakes,geometries,current)
    after = {str(p):digest(p) for p in protected}
    if before != after:
        raise RuntimeError("Protected inventory/observations changed during build; investigate concurrent writer")
    summary = {
        "source_name": SOURCE_NAME, "source_organization": ORGANIZATION, "source_year": 2013,
        "source_role": ROLE, "badge": BADGE, "disclaimer": DISCLAIMER,
        "provenance_basis": "User-supplied PMD archive attribution; no independent publisher certification",
        "raw_feature_rows":len(rows), "unique_pmd_lakes":len(lakes),
        "basin_totals":dict(Counter(r["basin"] for r in lakes)),
        "expected_basin_totals":{b:spec[2] for b,spec in BASINS.items()},
        "row_id_memberships":len(memberships), "extra_id_memberships_collapsed":len(memberships)-len(lakes),
        "repeated_pmd_identifiers":sum(r["source_row_count"]>1 for r in lakes),
        "raw_row_statuses":dict(Counter(r["row_status"] for r in rows)),
        "raw_geometry_statuses":dict(Counter(r["geometry_validation_status"] for r in rows)),
        "normalized_geometry_statuses":dict(Counter(r["geometry_validation_status"] for r in lakes)),
        "current_inventory_lakes":len(current),
        "spatial_matches_proposed":sum(r["current_lake_id"] is not None for r in matches),
        "unmatched_pmd_lakes":sum(r["current_lake_id"] is None for r in matches),
        "unmatched_current_lakes":sum(r["pmd_id"] is None for r in coverage),
        "pmd_crosswalk_statuses":dict(Counter(r["status"] for r in matches)),
        "current_coverage_statuses":dict(Counter(r["status"] for r in coverage)),
        "candidate_pairs":len(candidates), "source_crs":"EPSG:32643", "geojson_crs":"EPSG:4326",
        "match_parameters":{"radius_m":750,"mutual_nearest_margin_m":100,"distance":"polygon-to-current-centroid, UTM 43N metres"},
        "remote_import_status":"NOT_AUTHORIZED_PENDING_REVIEW", "remote_writes":0,
        "protected_files_unchanged":True, "protected_file_sha256":{Path(p).relative_to(ROOT).as_posix() if Path(p).is_relative_to(ROOT) else Path(p).name:h for p,h in after.items()},
        "current_inventory_sha256":digest(current_db), "raw_manifest":raw_manifest,
        "local_tables":list(TABLES), "code_version":"pmd-reference-v1",
    }
    output.mkdir(parents=True)
    projection=Transformer.from_crs(32643,4326,always_xy=True)
    features=[{"type":"Feature","id":r["pmd_id"],"properties":{k:v for k,v in r.items() if k!='geometry_wkt_epsg32643'},
               "geometry":mapping(transform(projection.transform,geometries[r["pmd_id"]])) if geometries[r["pmd_id"]] is not None else None} for r in lakes]
    write_json(output/"normalized_lakes.geojson",{"type":"FeatureCollection","features":features})
    for name,data in (("source_rows",rows),("row_memberships",memberships),("crosswalk",matches),("current_coverage",coverage),("match_candidates",candidates)):
        write_jsonl(output/(name+".jsonl"),data)
    create_reference_db(output/"pmd_2013_reference.sqlite3",lakes,rows,memberships,matches,coverage,candidates,summary)
    write_json(output/"validation_report.json",summary)
    write_report(output/"VALIDATION_REPORT.md",summary,lakes)
    write_json(output/"output_manifest.json",{"files":[{"file":p.name,"sha256":digest(p),"bytes":p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()]})
    return summary


def write_report(path: Path, summary: dict, lakes: list[dict]) -> None:
    lines=["# PMD Glacial Lakes Inventory 2013 — validation and reconciliation", "",SOURCE_NAME,"",DISCLAIMER,"",
           "Status: **LOCAL REVIEW CANDIDATE — NO SUPABASE IMPORT**.","",
           "## Counts","", "| Measurement | Result |", "|---|---:|"]
    for key in ("raw_feature_rows","unique_pmd_lakes","row_id_memberships","extra_id_memberships_collapsed","repeated_pmd_identifiers","current_inventory_lakes","spatial_matches_proposed","unmatched_pmd_lakes","unmatched_current_lakes","candidate_pairs"):
        lines.append(f"| {key} | {summary[key]:,} |")
    lines += ["","## Basin validation","","| Basin | Expected | Normalized |","|---|---:|---:|"]
    lines += [f"| {b} | {spec[2]} | {summary['basin_totals'][b]} |" for b,spec in BASINS.items()]
    lines += ["","## Row and geometry accounting","",
              "3,080 raw rows are NOT 3,080 independent lakes, and 36 is NOT a defensible duplicate count. Every nonblank basin-specific identifier becomes a row membership; those memberships are grouped by basin plus original PMD ID. No records are trimmed to reach 3,044. Rows without any basin ID are retained in source_rows, not assigned invented IDs.","",
              f"Raw row statuses: `{dumps(summary['raw_row_statuses'])}`.","",
              f"Raw geometry statuses: `{dumps(summary['raw_geometry_statuses'])}`.","",
              f"Normalized geometry statuses: `{dumps(summary['normalized_geometry_statuses'])}`.","",
              "All raw attributes and original EPSG:32643 geometry are retained in source_rows.jsonl and the standalone SQLite source-row table. row_memberships.jsonl records each ID association, its resolution, and whether it supplies the normalized geometry. Source-row references are one-based shapefile feature positions.","",
              "Different geometry variants, multi-basin rows, or geometry shared by different IDs are quarantined: the lake identity remains present but normalized geometry is null. No union or arbitrary largest/first polygon is used to resolve conflicting geometry. Invalid, missing, or area-under-10-m² geometry is also excluded from matching; the 10-m² threshold is a conservative review heuristic, not a PMD scientific cutoff. Identical geometry within the same ID can be collapsed while retaining all row references. No raw geometry is repaired.","",
              "### Identities requiring geometry review","","| PMD ID | Source rows | Status | Issues |","|---|---|---|---|"]
    lines += [f"| {r['pmd_id']} | {', '.join(map(str,r['source_row_references']))} | {r['geometry_validation_status']} | {', '.join(r['geometry_issues'])} |" for r in lakes if r['review_required']]
    lines += ["","## Spatial crosswalk limits","",
              "Matches are reviewable spatial proposals, NOT PMD-certified equivalences. PMD polygons are compared with the current inventory's centroids in EPSG:32643. Candidate radius: 750 metres; require mutual nearest candidates and more than 100 metres of separation from the second candidate on both sides. All ties/competing matches remain unresolved. Current invalid-source-geometry records remain visible but cannot be accepted automatically. All in-radius candidate pairs are saved. Geometry-quarantined PMD identities have no automatic spatial candidates.","",
              "These conservative thresholds are configurable in the matching function and have not been calibrated against labeled matches. UTM distances are projected approximations, and the current data exposes centroids rather than lake footprints. A failure to match does not prove a lake disappeared or is newly formed. Different inventory years, geometry defects and centroid offsets may explain mismatches. PMD IDs are never compared to PKGL IDs as equivalent keys.","",
              f"PMD outcomes: `{dumps(summary['pmd_crosswalk_statuses'])}`.","",
              f"Current outcomes: `{dumps(summary['current_coverage_statuses'])}`.","",
              "crosswalk.jsonl includes every PMD lake, including unmatched and ambiguous entries. current_coverage.jsonl includes every one of the 8,806 current lakes, including unmatched entries. Review candidate distances in match_candidates.jsonl before accepting any match.","",
              "## Preservation and provenance","",
              "The supplied directory is untouched. All eight supplied shapefile components are copied byte-for-byte into data/raw/pmd_glacial_lakes_2013/source; the manifest records SHA-256 hashes. No original ZIP archive was supplied in that directory. Attribution to PMD and year 2013 follows the user's supplied source description, not an independent publisher-signature verification.","",
              "The current map SQLite database, live-pipeline SQLite database and observation CSV were read-only inputs (where present). Their before/after SHA-256 hashes match. No public.lakes, official_lakes, current environmental observations or source measurements were modified. This package imports no live pipeline or Supabase client and never loads .env.","",
              "## Exact review artifacts and future tables","",
              "Local files: normalized_lakes.geojson, source_rows.jsonl, row_memberships.jsonl, crosswalk.jsonl, current_coverage.jsonl, match_candidates.jsonl, pmd_2013_reference.sqlite3, validation_report.json, VALIDATION_REPORT.md and output_manifest.json.","",
              "Standalone SQLite tables (not live database tables):",""]
    lines += [f"- `{name}`" for name in TABLES]
    lines += ["","Proposed remote destinations only: the same seven names in a separate `reference` schema, subject to review of geometry decisions, match thresholds, schema exposure and restricted grants. No remote schema or migration has been created. No remote import command is included. A reviewed migration and a separate explicit import approval are required; do not apply old inventory-replacement commands to this dataset.","",
              "The existing glofguard/ package was already deleted from the working tree when this task started. Those deletions were preserved; the new pmd_reference module runs independently. The historical source badge belongs to a separate UI reference panel, not to current measurements or every PKGL lake.",""]
    path.write_text("\n".join(lines),encoding="utf-8")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=ROOT/"data/Glacial lakes_2013")
    parser.add_argument("--raw-root",type=Path,default=ROOT/"data/raw/pmd_glacial_lakes_2013")
    parser.add_argument("--output",type=Path,default=ROOT/"data/reference/pmd_2013")
    parser.add_argument("--current-database",type=Path,default=ROOT/"data/map/glof_map.sqlite3")
    args=parser.parse_args()
    summary=build(args.source,args.raw_root,args.output,args.current_database)
    print(dumps({k:v for k,v in summary.items() if k not in {'raw_manifest','protected_file_sha256'}}))
