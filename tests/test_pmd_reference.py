"""Regression tests for the isolated PMD-2013 historical reference builder."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import Polygon

from pmd_reference.build import (
    BADGE,
    BASINS,
    DISCLAIMER,
    SOURCE_NAME,
    TABLES,
    create_reference_db,
    crosswalk,
    normalize_source,
    preserve_source,
)


def polygon(x: float, y: float, size: float = 100.0) -> Polygon:
    return Polygon([(x, y), (x + size, y), (x + size, y + size), (x, y + size)])


def source_frame() -> gpd.GeoDataFrame:
    """Small EPSG:32643 fixture covering every non-silent resolution branch."""
    id_columns = {column: None for column, _, _ in BASINS.values()}

    def row(**values):
        result = id_columns.copy()
        result.update(values)
        return result

    shared = polygon(500000, 3900000)
    another = polygon(501000, 3901000)
    invalid = Polygon([(503000, 3903000), (503100, 3903100), (503100, 3903000), (503000, 3903100)])
    rows = [
        row(LAKE_NO="Swat_gl_1"),
        row(LAKE_NO="Swat_gl_1"),  # repeated ID with identical geometry is auditable and valid.
        row(LAKE_NO="Swat_gl_2", Lake_No_17="Chi_gl_1"),  # multi-basin assignment.
        row(Lake_No_16="Gil_gl_1"),
        row(Lake_No_15="Hunz_gl_1"),  # shares a geometry with another ID.
        row(LAKE_NO_14="Ind_gl_1"),  # invalid geometry.
        row(),  # feature retained despite no basin identifier.
        row(LAKE_NO="Swat_gl_3"),
        row(LAKE_NO="Swat_gl_3"),  # same ID with a distinct geometry variant.
    ]
    geometries = [
        shared,
        shared,
        polygon(500400, 3900400),
        another,
        another,
        invalid,
        polygon(504000, 3904000),
        polygon(505000, 3905000),
        polygon(506000, 3906000),
    ]
    return gpd.GeoDataFrame(rows, geometry=geometries, crs="EPSG:32643")


class PmdReferenceTests(unittest.TestCase):
    def test_source_metadata_uses_the_required_historical_labels(self):
        self.assertEqual(SOURCE_NAME, "Pakistan Meteorological Department (PMD) " + chr(0x2014) + " Glacial Lakes Inventory 2013")
        self.assertEqual(BADGE, "PMD Official Inventory " + chr(0x00B7) + " 2013")
        self.assertEqual(DISCLAIMER, "Historical reference inventory " + chr(0x2014) + " not a live warning feed.")

    def test_normalization_retains_rows_and_quarantines_ambiguous_geometry(self):
        lakes, rows, memberships, selected = normalize_source(source_frame(), strict=False)
        by_id = {lake["pmd_id"]: lake for lake in lakes}

        self.assertEqual(len(rows), 9)
        self.assertEqual(len(memberships), 9)
        self.assertEqual({row["source_row"] for row in rows if row["row_status"] == "NO_BASIN_ID"}, {7})
        self.assertEqual(by_id["PMD2013:Swat:Swat_gl_1"]["geometry_validation_status"], "VALID")
        self.assertEqual(by_id["PMD2013:Swat:Swat_gl_1"]["source_row_references"], [1, 2])
        self.assertEqual(by_id["PMD2013:Swat:Swat_gl_1"]["resolution"], "IDENTICAL_GEOMETRY_COLLAPSED_KEEP_ALL_REFERENCES")
        self.assertIsNotNone(selected["PMD2013:Swat:Swat_gl_1"])

        for pmd_id in (
            "PMD2013:Swat:Swat_gl_2",
            "PMD2013:Chitral:Chi_gl_1",
            "PMD2013:Gilgit:Gil_gl_1",
            "PMD2013:Hunza:Hunz_gl_1",
            "PMD2013:Swat:Swat_gl_3",
        ):
            self.assertEqual(by_id[pmd_id]["geometry_validation_status"], "AMBIGUOUS")
            self.assertTrue(by_id[pmd_id]["review_required"])
            self.assertIsNone(selected[pmd_id])

        self.assertEqual(by_id["PMD2013:Indus:Ind_gl_1"]["geometry_validation_status"], "INVALID")
        self.assertTrue(all("resolution" in membership for membership in memberships))
        self.assertTrue(all("geometry_review_required" in membership for membership in memberships))

    def test_crosswalk_is_spatial_and_keeps_unmatched_current_lakes_visible(self):
        lakes, _, _, selected = normalize_source(source_frame(), strict=False)
        lake = next(row for row in lakes if row["pmd_id"] == "PMD2013:Swat:Swat_gl_1")
        transformer = Transformer.from_crs(32643, 4326, always_xy=True)
        longitude, latitude = transformer.transform(500050, 3900050)
        current = [
            {"lake_id": "PKGL-TEST-1", "latitude": latitude, "longitude": longitude, "source_geometry_valid": True, "inventory_version": "test"},
            {"lake_id": "PKGL-TEST-2", "latitude": 31.0, "longitude": 72.0, "source_geometry_valid": True, "inventory_version": "test"},
        ]
        matches, coverage, candidates = crosswalk([lake], {lake["pmd_id"]: selected[lake["pmd_id"]]}, current)

        self.assertEqual(matches[0]["status"], "SPATIAL_MATCH_PROPOSED")
        self.assertEqual(matches[0]["current_lake_id"], "PKGL-TEST-1")
        self.assertEqual(candidates[0]["method"], "PMD_POLYGON_TO_CURRENT_CENTROID_EPSG32643")
        self.assertEqual({row["lake_id"] for row in coverage if row["pmd_id"] is None}, {"PKGL-TEST-2"})

    def test_reference_database_has_all_expected_tables_and_foreign_keys(self):
        lakes, rows, memberships, selected = normalize_source(source_frame(), strict=False)
        valid_lake = next(row for row in lakes if row["pmd_id"] == "PMD2013:Swat:Swat_gl_1")
        transformer = Transformer.from_crs(32643, 4326, always_xy=True)
        longitude, latitude = transformer.transform(500050, 3900050)
        current = [{"lake_id": "PKGL-TEST-1", "latitude": latitude, "longitude": longitude, "source_geometry_valid": True, "inventory_version": "test"}]
        matches, coverage, candidates = crosswalk(
            lakes,
            selected,
            current,
        )
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "reference.sqlite3"
            create_reference_db(database, lakes, rows, memberships, matches, coverage, candidates, {"test": True})
            connection = sqlite3.connect(database)
            try:
                found = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(set(TABLES).issubset(found))
                self.assertEqual(connection.execute("SELECT count(*) FROM pmd_2013_lakes").fetchone()[0], len(lakes))
                self.assertEqual(connection.execute("SELECT count(*) FROM pmd_2013_source_rows").fetchone()[0], len(rows))
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                connection.close()

    def test_raw_preservation_is_byte_identical_and_refuses_conflicts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "preserved"
            source.mkdir()
            (source / "na_lakes.shp").write_bytes(b"unchanged source bytes")
            (source / "readme.txt").write_bytes(b"PMD provenance")
            first = preserve_source(source, destination)
            self.assertTrue(first["byte_identical_copy_verified"])
            self.assertEqual((destination / "na_lakes.shp").read_bytes(), (source / "na_lakes.shp").read_bytes())
            self.assertEqual(preserve_source(source, destination)["files"], first["files"])
            (destination / "readme.txt").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Raw preservation conflict"):
                preserve_source(source, destination)


if __name__ == "__main__":
    unittest.main()
