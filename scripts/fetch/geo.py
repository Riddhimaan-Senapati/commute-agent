"""Fetch the `geo` group: Boston + Cambridge municipal boundaries and OSM data.

Rerunnable: run as
    uv run --no-project --python 3.12 python scripts/fetch/geo.py
Stdlib only, plus `osmium` on PATH for the OSM clip.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import RAW, download, manifest_entry, write_manifest  # noqa: E402

GEO = RAW / "geo"

# City of Boston municipal boundary. The authoritative dataset is Analyze Boston's
# "City of Boston Outline Boundary (Water Excluded)" (CKAN a70595d2). Its CKAN
# GeoJSON download 302-redirects to a signed S3 URL behind Cloudflare, which
# refuses urllib (403) while the dataset's own ArcGIS service serves the same
# layer. Per the prompt's fallback, query that service with f=geojson&outSR=4326.
BOSTON_GEOJSON_URL = (
    "https://gisportal.boston.gov/arcgis/rest/services/Planning/OpenData/"
    "MapServer/9/query?where=1%3D1&outFields=*&outSR=4326&f=geojson"
)
BOSTON_LICENSE = "Open Data Commons Public Domain Dedication and License (PDDL)"

# City of Cambridge boundary, published by Cambridge GIS. Already WGS84 (no CRS
# member -> RFC 7946 default), so no reprojection is needed.
CAMBRIDGE_GEOJSON_URL = (
    "https://raw.githubusercontent.com/cambridgegis/cambridgegis_data/main/"
    "Boundary/City_Boundary/BOUNDARY_CityBoundary.geojson"
)
CAMBRIDGE_LICENSE = "Open Data Commons Public Domain Dedication and License (PDDL)"

MASS_PBF_URL = "https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf"
OSM_LICENSE = "ODbL 1.0, © OpenStreetMap contributors"
# Clip box around Boston: west,south,east,north.
CLIP_BBOX = "-71.22,42.20,-70.89,42.42"  # Boston bounds plus about 2 km, Harbor Islands included


def feature_count(path: Path) -> int:
    """Number of features in a GeoJSON FeatureCollection, for the manifest `rows`."""
    import json

    data = json.loads(path.read_text())
    return len(data.get("features", []))


def osmium_timestamp(path: Path) -> str:
    """Geofabrik file's data timestamp from `osmium fileinfo`, i.e. data freshness."""
    out = subprocess.run(
        ["osmium", "fileinfo", "-e", "-g", "header.option.timestamp", str(path)],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def main() -> None:
    GEO.mkdir(parents=True, exist_ok=True)
    entries = []

    boston = download(BOSTON_GEOJSON_URL, GEO / "boston_boundary.geojson")
    entries.append(manifest_entry(
        boston, BOSTON_GEOJSON_URL, BOSTON_LICENSE, feature_count(boston),
        notes="City of Boston municipal boundary (water excluded = land, "
              "~125-130 km2). Served as WGS84 GeoJSON from the Analyze Boston "
              "dataset's ArcGIS service because its CKAN S3 download 403s urllib.",
    ))

    cambridge = download(CAMBRIDGE_GEOJSON_URL, GEO / "cambridge_boundary.geojson")
    entries.append(manifest_entry(
        cambridge, CAMBRIDGE_GEOJSON_URL, CAMBRIDGE_LICENSE, feature_count(cambridge),
        notes="Cambridge GIS BOUNDARY_CityBoundary.geojson, already WGS84 "
              "(no CRS member -> RFC 7946 default); no reprojection needed.",
    ))

    mass_pbf = download(MASS_PBF_URL, GEO / "massachusetts-latest.osm.pbf")
    data_ts = osmium_timestamp(mass_pbf)
    entries.append(manifest_entry(
        mass_pbf, MASS_PBF_URL, OSM_LICENSE, None,
        notes=f"Geofabrik Massachusetts extract; data timestamp {data_ts}.",
    ))

    boston_pbf = GEO / "boston.osm.pbf"
    subprocess.run(
        ["osmium", "extract", "-b", CLIP_BBOX, "--overwrite",
         "-o", str(boston_pbf), str(mass_pbf)],
        check=True,
    )
    entries.append(manifest_entry(
        boston_pbf, MASS_PBF_URL, OSM_LICENSE, None,
        notes=f"Clipped from massachusetts-latest.osm.pbf with "
              f"`osmium extract -b {CLIP_BBOX}`. Source data timestamp {data_ts}.",
    ))

    path = write_manifest("geo", entries)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
