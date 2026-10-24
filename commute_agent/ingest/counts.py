from pathlib import Path

import pandas as pd

from commute_agent.ingest.meta import (
    SchemaMismatch,
    atomic_write,
    manifest_entry,
    require_columns,
    write_meta,
)
from commute_agent.paths import NUMINA_ZONES, PROCESSED, RAW

CAMBRIDGE_FILE = "counts/cambridge_citywide_bike_counts.csv"
SOMERVILLE_FILE = "counts/somerville_bike_ped_counts.csv"
NUMINA_DIR = "counts/numina"
NUMINA_ZONE_IDS = [51600, 51671, 52110, 52112, 52113, 52114, 52115, 52116, 52117]
NUMINA_PEAK_HOURS = {7, 8, 16, 17, 18}

CAMBRIDGE_COLUMNS = {"Count Location", "Year", "Date", "Time", "Count", "Latitude", "Longitude"}
SOMERVILLE_COLUMNS = {"Year", "Location Name", "Latitude", "Longitude", "AM/PM", "Mode", "Count"}
NUMINA_COLUMNS = {"zone_id", "metric", "objClass", "result", "time"}
NUMINA_ZONES_COLUMNS = {"zone_id", "lat", "lon"}

OUTPUT_COLUMNS = ["source", "location_id", "lat", "lon", "mode", "peak_hour_volume", "n_sessions"]

NUMINA_SITES = {
    51671: "jackson_square",
    52113: "jackson_square",
    52114: "jackson_square",
    52115: "jackson_square",
    51600: "northern_ave",
    52110: "northern_ave",
    52112: "tremont_boylston",
    52116: "tremont_boylston",
    52117: "tremont_boylston",
}


def _cambridge(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["Year"] >= 2018].copy()
    rows = []
    for (loc, date), grp in df.groupby(["Count Location", "Date"]):
        hours = grp["Time"].nunique() * 0.25
        if hours == 0:
            continue
        rows.append(
            {
                "Count Location": loc,
                "Date": date,
                "volume": grp["Count"].sum() / hours,
                "lat": grp["Latitude"].iloc[0],
                "lon": grp["Longitude"].iloc[0],
            }
        )
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    per_date = pd.DataFrame(rows)
    out = []
    for loc, grp in per_date.groupby("Count Location"):
        out.append(
            {
                "source": "cambridge_citywide",
                "location_id": str(loc),
                "lat": float(grp["lat"].iloc[0]),
                "lon": float(grp["lon"].iloc[0]),
                "mode": "bike",
                "peak_hour_volume": float(grp["volume"].mean()),
                "n_sessions": int(len(grp)),
            }
        )
    return pd.DataFrame(out, columns=OUTPUT_COLUMNS)


def _somerville(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["Year"] >= 2018].copy()
    df["mode"] = df["Mode"].map({"Bike": "bike", "Ped": "ped"})
    df = df[df["mode"].notna()]
    rows = []
    # one hour of AM + one hour of PM -> sum / 2 per (location, year, mode)
    for (loc, year, mode), grp in df.groupby(["Location Name", "Year", "mode"]):
        volume = grp["Count"].sum() / 2
        rows.append(
            {
                "Location Name": loc,
                "Year": year,
                "mode": mode,
                "volume": volume,
                "lat": grp["Latitude"].iloc[0],
                "lon": grp["Longitude"].iloc[0],
            }
        )
    if not rows:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    per_year = pd.DataFrame(rows)
    out = []
    for (loc, mode), grp in per_year.groupby(["Location Name", "mode"]):
        out.append(
            {
                "source": "somerville",
                "location_id": str(loc),
                "lat": float(grp["lat"].iloc[0]),
                "lon": float(grp["lon"].iloc[0]),
                "mode": mode,
                "peak_hour_volume": float(grp["volume"].mean()),
                "n_sessions": int(len(grp)),
            }
        )
    return pd.DataFrame(out, columns=OUTPUT_COLUMNS)


def _numina(raw_dir: Path, zones: pd.DataFrame) -> pd.DataFrame:
    zone_coords = {int(r.zone_id): (float(r.lat), float(r.lon)) for r in zones.itertuples()}
    # zone_id -> {mode -> mean peak-hour result}
    per_zone: list[dict] = []
    for zone_id in NUMINA_ZONE_IDS:
        path = raw_dir / NUMINA_DIR / f"zone_{zone_id}.csv"
        df = pd.read_csv(path)
        require_columns(df, f"counts/numina/zone_{zone_id}", NUMINA_COLUMNS)
        df = df[df["metric"] == "count"].copy()
        ts = pd.to_datetime(df["time"])
        df = df[ts.dt.weekday < 5]
        ts = pd.to_datetime(df["time"])
        df = df[ts.dt.hour.isin(NUMINA_PEAK_HOURS)]
        df["mode"] = df["objClass"].map({"bicycle": "bike", "pedestrian": "ped"})
        df = df[df["mode"].notna()]
        if zone_id not in zone_coords:
            raise SchemaMismatch(f"counts/numina_zones: zone {zone_id} missing from {NUMINA_ZONES}")
        for mode, grp in df.groupby("mode"):
            per_zone.append(
                {
                    "site": NUMINA_SITES[zone_id],
                    "zone_id": zone_id,
                    "mode": mode,
                    "mean": float(grp["result"].mean()),
                    "n": int(len(grp)),
                }
            )
    pz = pd.DataFrame(per_zone)
    out = []
    # per site and mode, peak_hour_volume is the max zone mean (overlapping camera zones)
    for (site, mode), grp in pz.groupby(["site", "mode"]):
        top = grp.loc[grp["mean"].idxmax()]
        lat, lon = zone_coords[int(top["zone_id"])]
        out.append(
            {
                "source": "numina",
                "location_id": site,
                "lat": lat,
                "lon": lon,
                "mode": mode,
                "peak_hour_volume": float(top["mean"]),
                "n_sessions": int(grp["n"].sum()),
            }
        )
    return pd.DataFrame(out, columns=OUTPUT_COLUMNS)


def counts(raw_dir: Path = RAW, out_dir: Path = PROCESSED, zones_path: Path = NUMINA_ZONES) -> Path:
    if not zones_path.exists():
        raise SchemaMismatch(
            f"counts: Numina zones file not found at {zones_path}; expected columns "
            f"{sorted(NUMINA_ZONES_COLUMNS)}"
        )
    zones = pd.read_csv(zones_path)
    require_columns(zones, "counts/numina_zones", NUMINA_ZONES_COLUMNS)

    cam_df = pd.read_csv(raw_dir / CAMBRIDGE_FILE, low_memory=False)
    require_columns(cam_df, "counts/cambridge_citywide", CAMBRIDGE_COLUMNS)
    som_df = pd.read_csv(raw_dir / SOMERVILLE_FILE, low_memory=False)
    require_columns(som_df, "counts/somerville", SOMERVILLE_COLUMNS)

    out = pd.concat(
        [_cambridge(cam_df), _somerville(som_df), _numina(raw_dir, zones)],
        ignore_index=True,
    )[OUTPUT_COLUMNS]

    path = out_dir / "counts.parquet"
    atomic_write(path, lambda p: out.to_parquet(p, index=False))

    write_meta(
        path,
        [
            manifest_entry(raw_dir, "counts", CAMBRIDGE_FILE),
            manifest_entry(raw_dir, "counts", SOMERVILLE_FILE),
        ],
        row_count=len(out),
        notes=(
            "Peak-hour volumes: Cambridge citywide (bike, Year>=2018), Somerville (bike and "
            "ped, Year>=2018), Numina (weekday peak hours, max zone mean per site). Numina "
            "site coordinates joined from numina_zones.csv."
        ),
    )
    return path
