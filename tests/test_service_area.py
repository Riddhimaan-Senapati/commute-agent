"""Service area: boundary union, crash coverage, Somerville crash rows, and the outside message.

The boundaries here are small rectangles written into tmp_path, not real city outlines.
"""

import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from commute_agent.ingest import geo
from commute_agent.ingest.crashes import _somerville_rows
from commute_agent.ingest.meta import SchemaMismatch
from commute_agent.service_area import SERVICE_CITIES, outside_message

CITY_BOXES = {
    "Boston": (-71.07, 42.35, -71.05, 42.37),
    "Cambridge": (-71.13, 42.36, -71.09, 42.39),
    "Somerville": (-71.13, 42.39, -71.09, 42.42),
    "Brookline": (-71.18, 42.31, -71.13, 42.34),
}
NEWTON_BOX = (-71.23, 42.30, -71.19, 42.36)

INSIDE = {
    "Boston": (-71.06, 42.36),
    "Cambridge": (-71.11, 42.37),
    "Somerville": (-71.11, 42.405),
    "Brookline": (-71.155, 42.325),
}
NEWTON = (-71.1956, 42.3297)


def _write_raw(raw: Path) -> None:
    """Boston and Cambridge GeoJSON, and a MassGIS-style shapefile zip with four towns."""
    geo_dir = raw / "geo"
    geo_dir.mkdir(parents=True)
    for name, city in (("boston_boundary", "Boston"), ("cambridge_boundary", "Cambridge")):
        gpd.GeoDataFrame({"NAME": [city]}, geometry=[box(*CITY_BOXES[city])], crs=4326).to_file(
            geo_dir / f"{name}.geojson", driver="GeoJSON"
        )

    towns = gpd.GeoDataFrame(
        {"TOWN": ["SOMERVILLE", "BROOKLINE", "NEWTON"]},
        geometry=[
            box(*CITY_BOXES["Somerville"]),
            box(*CITY_BOXES["Brookline"]),
            box(*NEWTON_BOX),
        ],
        crs=4326,
    )
    shp_dir = raw / "shp_tmp"
    shp_dir.mkdir()
    towns.to_file(shp_dir / "municipalities.shp")
    with zipfile.ZipFile(geo_dir / "massgis_municipalities.zip", "w") as zf:
        for part in shp_dir.iterdir():
            zf.write(part, arcname=part.name)

    files = ["boston_boundary.geojson", "cambridge_boundary.geojson"]
    manifest = {
        "group": "geo",
        "files": [
            {"file": f"geo/{name}", "source_url": "https://example.test/x", "license": "test"}
            for name in [*files, "massgis_municipalities.zip"]
        ],
    }
    (geo_dir / "MANIFEST.json").write_text(json.dumps(manifest))


def test_boundary_unions_four_cities_and_leaves_brookline_out_of_coverage(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_raw(raw)
    out = tmp_path / "processed"

    path = geo.boundary(raw, out)

    assert path == out / "service_area.geojson"
    area = gpd.read_file(out / "service_area.geojson").union_all()
    coverage = gpd.read_file(out / "crash_coverage.geojson").union_all()
    for city in SERVICE_CITIES:
        assert area.contains(Point(INSIDE[city])), city
    assert not area.contains(Point(NEWTON))
    for city in ("Boston", "Cambridge", "Somerville"):
        assert coverage.contains(Point(INSIDE[city])), city
    assert not coverage.contains(Point(INSIDE["Brookline"]))
    assert (out / "service_area_buffered.geojson").exists()


def test_missing_town_is_named_in_the_error(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    _write_raw(raw)
    towns = gpd.GeoDataFrame(
        {"TOWN": ["BROOKLINE"]}, geometry=[box(*CITY_BOXES["Brookline"])], crs=4326
    )
    shp_dir = tmp_path / "only_brookline"
    shp_dir.mkdir()
    towns.to_file(shp_dir / "municipalities.shp")
    with zipfile.ZipFile(raw / "geo" / "massgis_municipalities.zip", "w") as zf:
        for part in shp_dir.iterdir():
            zf.write(part, arcname=part.name)

    with pytest.raises(SchemaMismatch, match="Somerville"):
        geo.service_area(raw)


def test_somerville_rows_one_per_mode_and_drops_missing_coordinates() -> None:
    df = pd.DataFrame(
        {
            "dtcrash": ["2026-09-24 15:35:00", "2026-09-17 13:42:00", "2026-09-01 08:00:00"],
            "latitude": ["42.3946", "42.3975", ""],
            "longitude": ["-71.081", "-71.1036", ""],
            "nonmotoristpedestrian": ["0", "1", "0"],
            "nonmotoristcyclist": ["0", "1", "0"],
        }
    )

    rows, dropped = _somerville_rows(df)

    assert dropped == 1
    assert sorted(rows["mode"]) == ["bike", "ped"]
    assert set(rows["source_dataset"]) == {"somerville_police"}
    assert rows["lat"].tolist() == [42.3975, 42.3975]


def test_outside_message_names_the_service_cities() -> None:
    assert outside_message(["origin"]) == (
        "Outside the service area (Boston, Cambridge, Somerville, Brookline): origin"
    )
    assert outside_message(["origin", "destination"]).endswith(": origin and destination")
