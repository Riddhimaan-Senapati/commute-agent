import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd

from commute_agent.ingest.geo import SERVICE_BUFFER_FILE
from commute_agent.ingest.meta import SchemaMismatch, atomic_write, manifest_entry, write_meta
from commute_agent.paths import OTP_DIR, PROCESSED, RAW

STATIONS_FILE = "transit/massgis_rapid_transit_stations.json"
GTFS_FILE = "transit/MBTA_GTFS.zip"
MASS_PBF = "geo/massachusetts-latest.osm.pbf"
SERVICE_PBF = "service_area.osm.pbf"


def _parse_stations(raw: dict) -> pd.DataFrame:
    features = raw.get("features")
    if not isinstance(features, list):
        raise SchemaMismatch("stations: massgis JSON has no 'features' list")
    rows = []
    for feat in features:
        attrs = feat.get("attributes", {})
        geom = feat.get("geometry", {})
        if "STATION" not in attrs or "x" not in geom or "y" not in geom:
            raise SchemaMismatch(
                "stations: each feature needs attributes.STATION and geometry.x/geometry.y"
            )
        rows.append(
            {
                "name": attrs["STATION"],
                "line": attrs.get("LINE"),
                "lat": float(geom["y"]),
                "lon": float(geom["x"]),
            }
        )
    return pd.DataFrame(rows, columns=["name", "line", "lat", "lon"])


def stations(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    raw = json.loads((raw_dir / STATIONS_FILE).read_text())
    df = _parse_stations(raw)

    path = out_dir / "rapid_transit_stations.parquet"
    atomic_write(path, lambda p: df.to_parquet(p, index=False))
    write_meta(
        path,
        manifest_entry(raw_dir, "transit", STATIONS_FILE),
        row_count=len(df),
        notes="MBTA rapid transit stations from MassGIS (EPSG:4326).",
    )
    return path


def otp_inputs(raw_dir: Path = RAW, out_dir: Path = OTP_DIR, processed: Path = PROCESSED) -> Path:
    """Copy the GTFS zip and extract the service-area OSM pbf into data/otp/.

    The extract clips the Massachusetts pbf to the 2 km-buffered service-area polygon
    (osmium extract -p), so it covers Boston, Cambridge, Somerville and Brookline.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    gtfs_dst = out_dir / "mbta-gtfs.zip"
    pbf_dst = out_dir / SERVICE_PBF
    _atomic_copy(raw_dir / GTFS_FILE, gtfs_dst)
    subprocess.run(
        [
            "osmium",
            "extract",
            "-p",
            str(processed / SERVICE_BUFFER_FILE),
            "--overwrite",
            "-f",
            "pbf",
            "-o",
            str(pbf_dst.with_name(pbf_dst.name + ".tmp")),
            str(raw_dir / MASS_PBF),
        ],
        check=True,
    )
    pbf_dst.with_name(pbf_dst.name + ".tmp").replace(pbf_dst)
    return gtfs_dst


def _atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)
