"""Fetch the `geo` group: service-area municipal boundaries and the Massachusetts OSM extract.

Rerunnable: run as
    uv run --no-project --python 3.12 python scripts/fetch/geo.py
Stdlib only, plus `osmium` on PATH for `osmium fileinfo` (the OSM clip runs in
`python -m commute_agent.ingest otp-inputs`, which needs the service-area polygon).
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

# Somerville and Brookline come from MassGIS's Census 2020 towns layer, one polygon per
# city or town, queried from its ArcGIS service as WGS84 GeoJSON. (The mass.gov download
# page answers scripted requests with 403; the service does not.)
MASSGIS_TOWNS_URL = (
    "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/"
    "Census2020_Towns/MapServer/2/query?where=TOWN20%20IN%20(%27SOMERVILLE%27,"
    "%27BROOKLINE%27)&outFields=TOWN20,TOWN_ID&outSR=4326&f=geojson"
)
MASSGIS_TOWNS_LICENSE = (
    "U.S. Census Bureau 2020 municipal boundaries (public domain), processed by MassGIS"
)

MASS_PBF_URL = "https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf"
OSM_LICENSE = "ODbL 1.0, © OpenStreetMap contributors"


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

    towns = download(MASSGIS_TOWNS_URL, GEO / "massgis_towns.geojson")
    entries.append(manifest_entry(
        towns, MASSGIS_TOWNS_URL, MASSGIS_TOWNS_LICENSE, feature_count(towns),
        notes="MassGIS Census 2020 Towns layer, Somerville and Brookline only, WGS84. The "
              "boundary ingest step reads them by the TOWN20 field.",
    ))

    mass_pbf = download(MASS_PBF_URL, GEO / "massachusetts-latest.osm.pbf")
    data_ts = osmium_timestamp(mass_pbf)
    entries.append(manifest_entry(
        mass_pbf, MASS_PBF_URL, OSM_LICENSE, None,
        notes=f"Geofabrik Massachusetts extract; data timestamp {data_ts}. The OTP "
              "service-area clip is made from it by `ingest otp-inputs`.",
    ))

    path = write_manifest("geo", entries)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
