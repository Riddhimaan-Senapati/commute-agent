import zipfile
from pathlib import Path

import pandas as pd

from commute_agent.ingest.meta import (
    atomic_write,
    manifest_entry,
    require_columns,
    write_meta,
)
from commute_agent.paths import PROCESSED, RAW

GSE_FILE = "ridership/GSE_by_year.zip"
GSE_MEMBER = "GSE_by_year/GSE_{year}.csv"
GTFS_FILE = "transit/MBTA_GTFS.zip"
ENTRIES_YEAR = 2025
GSE_COLUMNS = {"service_date", "station_name", "gated_entries", "stop_id"}
GTFS_STOP_COLUMNS = {"stop_id", "stop_lat", "stop_lon", "location_type"}
OUTPUT_COLUMNS = ["stop_id", "station_name", "lat", "lon", "entries_per_day", "service_days"]


def _parent_station_coords(gtfs_zip: Path) -> pd.DataFrame:
    """Parent-station (location_type 1) coordinates from the MBTA GTFS stops.txt."""
    with zipfile.ZipFile(gtfs_zip) as z:
        stops = pd.read_csv(z.open("stops.txt"), dtype=str)
    require_columns(stops, "transit/MBTA_GTFS stops.txt", GTFS_STOP_COLUMNS)
    parents = stops[stops["location_type"] == "1"]
    return pd.DataFrame(
        {
            "stop_id": parents["stop_id"],
            "lat": parents["stop_lat"].astype(float),
            "lon": parents["stop_lon"].astype(float),
        }
    )


def station_entries(
    raw_dir: Path = RAW, out_dir: Path = PROCESSED, year: int = ENTRIES_YEAR
) -> Path:
    """Mean gated entries per service day for each station in one calendar year.

    Joins the MBTA gated-entry counts (per stop_id, which is a GTFS parent station)
    to the parent-station coordinates in the MBTA GTFS feed. Stations with no
    coordinates in the feed are dropped and counted in the sidecar notes.
    """
    member = GSE_MEMBER.format(year=year)
    with zipfile.ZipFile(raw_dir / GSE_FILE) as z:
        gse = pd.read_csv(
            z.open(member),
            dtype={"stop_id": str, "service_date": str},
            usecols=["service_date", "station_name", "gated_entries", "stop_id"],
        )
    require_columns(gse, f"ridership/GSE_{year}", GSE_COLUMNS)

    per_station = (
        gse.groupby("stop_id")
        .agg(
            station_name=("station_name", "first"),
            total_entries=("gated_entries", "sum"),
            service_days=("service_date", "nunique"),
        )
        .reset_index()
    )
    per_station["entries_per_day"] = per_station["total_entries"] / per_station["service_days"]

    coords = _parent_station_coords(raw_dir / GTFS_FILE)
    out = per_station.merge(coords, on="stop_id", how="inner")
    dropped = len(per_station) - len(out)
    out = out[OUTPUT_COLUMNS].sort_values("stop_id").reset_index(drop=True)

    path = out_dir / "station_entries.parquet"
    atomic_write(path, lambda p: out.to_parquet(p, index=False))
    write_meta(
        path,
        [
            manifest_entry(raw_dir, "ridership", GSE_FILE),
            manifest_entry(raw_dir, "transit", GTFS_FILE),
        ],
        row_count=len(out),
        notes=(
            f"Mean gated entries per service day in {year} for each MBTA station "
            "(GTFS parent station stop_id). Entries are unscaled and include employee taps. "
            f"Coordinates from the MBTA GTFS feed. {dropped} stations with no GTFS "
            "parent-station coordinates were dropped."
        ),
    )
    return path
