import re
from pathlib import Path

import pandas as pd

from commute_agent.ingest.meta import atomic_write, manifest_entry, require_columns, write_meta
from commute_agent.paths import PROCESSED, RAW

VISION_ZERO_FILE = "crashes/boston_vision_zero.csv"
CAMBRIDGE_FILE = "crashes/cambridge_police_crashes.csv"
SOMERVILLE_FILE = "crashes/somerville_police_crashes.csv"

VZ_COLUMNS = {"dispatch_ts", "mode_type", "lat", "long"}
CAMBRIDGE_COLUMNS = {"Date Time", "May involve cyclist", "May Involve Pedestrian", "Location"}
# The CSV download uses display names (some with trailing spaces, stripped on read).
SOM_WHEN = "Date and Time of Crash"
SOM_LAT = "Latitude"
SOM_LON = "Longitude"
SOM_PED = "Pedestrian Involvement (Non-Motorist)"
SOM_CYCLIST = "Cyclist Involvement (Non-Motorist)"
SOMERVILLE_COLUMNS = {SOM_WHEN, SOM_LAT, SOM_LON, SOM_PED, SOM_CYCLIST}

LOCATION_RE = re.compile(r"\((-?\d+\.\d+), (-?\d+\.\d+)\)$")

DEFINITIONS_NOTE = (
    "Boston Vision Zero and Cambridge police records use different crash definitions. "
    "Neither is the MassDOT state crash file."
)

OUTPUT_COLUMNS = ["source_dataset", "occurred_at", "mode", "lat", "lon"]


def _boston_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Vision Zero: one row per bike/ped crash, UTC occurred_at, mv dropped."""
    kept = df[df["mode_type"].isin(["bike", "ped"])].copy()
    occurred_at = pd.to_datetime(kept["dispatch_ts"], utc=True)
    return pd.DataFrame(
        {
            "source_dataset": "boston_vision_zero",
            "occurred_at": occurred_at.reset_index(drop=True),
            "mode": kept["mode_type"].reset_index(drop=True),
            "lat": pd.to_numeric(kept["lat"], errors="coerce").reset_index(drop=True),
            "lon": pd.to_numeric(kept["long"], errors="coerce").reset_index(drop=True),
        }
    )


def _parse_location(value: object) -> tuple[float, float] | None:
    if not isinstance(value, str):
        return None
    m = LOCATION_RE.search(value.strip())
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def _cambridge_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Cambridge: expand to one row per involved mode, coordinates parsed from Location.

    Returns the rows and a notes-count dict (empty_location, address_only).
    """
    coords = df["Location"].map(_parse_location)
    has_coords = coords.notna()

    empty_location = int(df["Location"].isna().sum() + (df["Location"] == "").sum())
    address_only = int((~has_coords).sum() - empty_location)

    kept = df[has_coords].copy()
    parsed = coords[has_coords]
    kept["lat"] = [c[0] for c in parsed]
    kept["lon"] = [c[1] for c in parsed]
    occurred_at = pd.to_datetime(kept["Date Time"], format="%m/%d/%Y %I:%M:%S %p").dt.tz_localize(
        "America/New_York", ambiguous=True, nonexistent="shift_forward"
    )
    occurred_at = occurred_at.dt.tz_convert("UTC")

    # read_csv parses the "1"/blank flag columns as 1.0/NaN, so compare numerically.
    cyclist = pd.to_numeric(kept["May involve cyclist"], errors="coerce") == 1
    pedestrian = pd.to_numeric(kept["May Involve Pedestrian"], errors="coerce") == 1

    frames = []
    for flag, mode in ((cyclist, "bike"), (pedestrian, "ped")):
        sel = kept[flag]
        frames.append(
            pd.DataFrame(
                {
                    "source_dataset": "cambridge_police",
                    "occurred_at": occurred_at[flag].reset_index(drop=True),
                    "mode": mode,
                    "lat": sel["lat"].reset_index(drop=True),
                    "lon": sel["lon"].reset_index(drop=True),
                }
            )
        )
    rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=OUTPUT_COLUMNS)
    counts = {
        "cambridge_empty_location": empty_location,
        "cambridge_address_only": address_only,
    }
    return rows, counts


def _somerville_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Somerville: one row per involved mode (cyclist or pedestrian count above zero).

    Returns the rows and the number of records dropped for missing coordinates.
    """
    lat = pd.to_numeric(df[SOM_LAT], errors="coerce")
    lon = pd.to_numeric(df[SOM_LON], errors="coerce")
    has_coords = lat.notna() & lon.notna()
    dropped = int((~has_coords).sum())

    kept = df[has_coords].copy()
    kept_lat = lat[has_coords]
    kept_lon = lon[has_coords]
    occurred_at = pd.to_datetime(kept[SOM_WHEN], format="ISO8601").dt.tz_localize(
        "America/New_York", ambiguous=True, nonexistent="shift_forward"
    )
    occurred_at = occurred_at.dt.tz_convert("UTC")

    cyclist = pd.to_numeric(kept[SOM_CYCLIST], errors="coerce").fillna(0) > 0
    pedestrian = pd.to_numeric(kept[SOM_PED], errors="coerce").fillna(0) > 0

    frames = []
    for flag, mode in ((cyclist, "bike"), (pedestrian, "ped")):
        frames.append(
            pd.DataFrame(
                {
                    "source_dataset": "somerville_police",
                    "occurred_at": occurred_at[flag].reset_index(drop=True),
                    "mode": mode,
                    "lat": kept_lat[flag].reset_index(drop=True),
                    "lon": kept_lon[flag].reset_index(drop=True),
                }
            )
        )
    rows = pd.concat(frames, ignore_index=True)
    return rows, dropped


def crashes(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    vz_df = pd.read_csv(raw_dir / VISION_ZERO_FILE, low_memory=False)
    require_columns(vz_df, "crashes/boston_vision_zero", VZ_COLUMNS)
    cam_df = pd.read_csv(raw_dir / CAMBRIDGE_FILE, low_memory=False)
    require_columns(cam_df, "crashes/cambridge_police", CAMBRIDGE_COLUMNS)
    som_df = pd.read_csv(raw_dir / SOMERVILLE_FILE, dtype=str, low_memory=False)
    som_df.columns = [str(c).strip() for c in som_df.columns]
    require_columns(som_df, "crashes/somerville_police", SOMERVILLE_COLUMNS)

    boston = _boston_rows(vz_df)
    cambridge, cam_counts = _cambridge_rows(cam_df)
    somerville, som_dropped = _somerville_rows(som_df)

    out = pd.concat([boston, cambridge, somerville], ignore_index=True)[OUTPUT_COLUMNS]

    path = out_dir / "crashes.parquet"
    atomic_write(path, lambda p: out.to_parquet(p, index=False))

    notes = (
        f"{DEFINITIONS_NOTE} Cambridge rows without coordinates dropped: "
        f"{cam_counts['cambridge_empty_location']} empty Location, "
        f"{cam_counts['cambridge_address_only']} address only. "
        f"Somerville records without coordinates dropped: {som_dropped}. "
        "Somerville excludes state roads and crashes under investigation."
    )
    write_meta(
        path,
        [
            manifest_entry(raw_dir, "crashes", VISION_ZERO_FILE),
            manifest_entry(raw_dir, "crashes", CAMBRIDGE_FILE),
            manifest_entry(raw_dir, "crashes", SOMERVILLE_FILE),
        ],
        row_count=len(out),
        notes=notes,
    )
    return path
