import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point as ShapelyPoint

from commute_agent.ingest.meta import atomic_write, manifest_entry, require_columns, write_meta
from commute_agent.paths import PROCESSED, RAW

BLUEBIKES_ZIP = "transit/202411-bluebikes-tripdata.zip"
ZIP_MEMBER = "202411-bluebikes-tripdata.csv"
BOSTON_BOUNDARY = "geo/boston_boundary.geojson"

BUFFER_M = 2000

TRIP_COLUMNS = {
    "start_station_id",
    "end_station_id",
    "start_station_name",
    "start_lat",
    "start_lng",
    "end_lat",
    "end_lng",
}


def _read_trips(raw_dir: Path) -> pd.DataFrame:
    with zipfile.ZipFile(raw_dir / BLUEBIKES_ZIP) as zf:
        with zf.open(ZIP_MEMBER) as f:
            return pd.read_csv(f, low_memory=False)


def aggregate_trips(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows without an end (or start) station, count trips per station pair."""
    df = df[df["start_station_id"].notna() & (df["start_station_id"].astype(str) != "")]
    df = df[df["end_station_id"].notna() & (df["end_station_id"].astype(str) != "")]
    trips = (
        df.groupby(["start_station_id", "end_station_id"], as_index=False)
        .size()
        .rename(columns={"size": "trips"})
    )
    return trips[["start_station_id", "end_station_id", "trips"]]


def station_medians(df: pd.DataFrame) -> pd.DataFrame:
    """Median coordinate per start station, with its name."""
    df = df[df["start_station_id"].notna() & (df["start_station_id"].astype(str) != "")].copy()
    grouped = df.groupby("start_station_id")
    stations = pd.DataFrame(
        {
            "station_id": list(grouped.groups.keys()),
            "name": grouped["start_station_name"].first().values,
            "lat": grouped["start_lat"].median().values,
            "lon": grouped["start_lng"].median().values,
        }
    )
    return stations.reset_index(drop=True)


def _within_buffered_boston(stations: pd.DataFrame, boundary_path: Path) -> pd.DataFrame:
    boundary = gpd.read_file(boundary_path)
    # buffer in a metric CRS (EPSG:26986 Mass State Plane), then back to WGS84
    buffered = boundary.to_crs(26986).buffer(BUFFER_M).to_crs(4326).union_all()
    mask = [
        buffered.contains(ShapelyPoint(lon, lat))
        for lat, lon in zip(stations["lat"], stations["lon"], strict=True)
    ]
    return stations[mask].reset_index(drop=True)


def bluebikes(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    df = _read_trips(raw_dir)
    require_columns(df, "bluebikes", TRIP_COLUMNS)

    trips = aggregate_trips(df)
    stations = station_medians(df)
    stations = _within_buffered_boston(stations, raw_dir / BOSTON_BOUNDARY)

    keep = set(stations["station_id"])
    trips = trips[trips["start_station_id"].isin(keep) & trips["end_station_id"].isin(keep)]
    trips = trips.reset_index(drop=True)

    trips_path = out_dir / "bluebikes_trips.parquet"
    atomic_write(trips_path, lambda p: trips.to_parquet(p, index=False))
    stations_path = out_dir / "bluebikes_stations.parquet"
    atomic_write(stations_path, lambda p: stations.to_parquet(p, index=False))

    entry = manifest_entry(raw_dir, "transit", BLUEBIKES_ZIP)
    write_meta(
        trips_path,
        entry,
        row_count=len(trips),
        notes=(
            "Trip counts per station pair from zip member 202411-bluebikes-tripdata.csv. "
            "Rows without a start or end station dropped."
        ),
    )
    write_meta(
        stations_path,
        entry,
        row_count=len(stations),
        notes=(
            "Station median coordinates, clipped to the 2 km-buffered Boston boundary "
            "(removes Salem and Hingham clusters)."
        ),
    )
    return trips_path
