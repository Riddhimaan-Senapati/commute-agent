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

# MassGIS municipalities (cities and towns, one multipart polygon each), which give
# Somerville and Brookline. The download is a shapefile zip that the operator places
# at data/raw/geo/massgis_municipalities.zip; this script cannot fetch it unattended
# because the mass.gov download page did not answer a scripted request (HTTP 403).
# Its license was not stated on the metadata pages checked, so the manifest says so.
MASSGIS_MUNICIPALITIES_PAGE = "https://www.mass.gov/info-details/massgis-data-municipalities"
MASSGIS_LICENSE = (
    "MassGIS (Commonwealth of Massachusetts). No license text found on the metadata pages "
    "checked; confirm the terms on the download page before redistributing."
)
MUNICIPALITIES_ZIP = GEO / "massgis_municipalities.zip"

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

    if not MUNICIPALITIES_ZIP.exists():
        sys.exit(
            f"missing {MUNICIPALITIES_ZIP}. Download the MassGIS 'Massachusetts "
            f"Municipalities' shapefile zip from {MASSGIS_MUNICIPALITIES_PAGE}, save it "
            "at that path, and rerun this script."
        )
    entries.append(manifest_entry(
        MUNICIPALITIES_ZIP, MASSGIS_MUNICIPALITIES_PAGE, MASSGIS_LICENSE, None,
        notes="MassGIS municipalities polygons (Somerville and Brookline are read from it "
              "by the boundary ingest step, by its TOWN field). Supplied by hand; see the "
              "script comment.",
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
