"""Offline-readiness check.

Lists every Data_Store file an offline demo needs and reports the ones missing.
Prints ``offline ready`` and exits 0 when all are present; otherwise prints each
missing path and exits 1.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from commute_agent.models import ShowcaseTrip, ShowcaseTrips
from commute_agent.paths import (
    ALERT_SNAPSHOTS,
    EMISSION_FACTORS,
    OTP_CACHE,
    PROCESSED,
    SHOWCASE_TRIPS,
    WEB,
)
from commute_agent.transit.otp import otp_cache_key

GRAPHML_FILES = ("walk.graphml", "bike.graphml", "drive.graphml")
BOUNDARY_FILES = ("service_area.geojson", "crash_coverage.geojson")
PROCESSED_FILES = (
    "bluebikes_stations.parquet",
    "rapid_transit_stations.parquet",
    "basemap.geojson",
    "risk_meta.json",
)
VENDOR_WEB_FILES = (
    "vendor/chart.umd.min.js",
    "vendor/leaflet/leaflet.js",
    "vendor/leaflet/leaflet.css",
    "vendor/leaflet/images/layers.png",
    "vendor/leaflet/images/layers-2x.png",
    "vendor/leaflet/images/marker-icon.png",
    "vendor/leaflet/images/marker-icon-2x.png",
    "vendor/leaflet/images/marker-shadow.png",
)


def load_showcase_trips(path: Path = SHOWCASE_TRIPS) -> list[ShowcaseTrip]:
    data = yaml.safe_load(path.read_text())
    return ShowcaseTrips.model_validate({"trips": data}).trips


def required_offline_files(
    trips: list[ShowcaseTrip],
    *,
    emission_factors: Path = EMISSION_FACTORS,
    processed: Path = PROCESSED,
    alert_snapshots: Path = ALERT_SNAPSHOTS,
    web: Path = WEB,
    otp_cache: Path = OTP_CACHE,
) -> list[Path]:
    """Every file an offline plan of each Showcase_Trip reads from the Data_Store."""
    paths: list[Path] = [emission_factors]
    paths += [processed / name for name in GRAPHML_FILES]
    paths += [processed / name for name in BOUNDARY_FILES]
    paths += [processed / name for name in PROCESSED_FILES]

    if alert_snapshots.is_dir():
        paths += sorted(alert_snapshots.glob("*.json"))

    paths += [web / name for name in VENDOR_WEB_FILES]

    for trip in trips:
        key = otp_cache_key(trip.origin, trip.destination, trip.weekday, trip.depart_local)
        paths.append(otp_cache / f"{key}.json")

    return paths


def main(
    argv: list[str] | None = None,
    *,
    showcase_trips: Path = SHOWCASE_TRIPS,
    emission_factors: Path = EMISSION_FACTORS,
    processed: Path = PROCESSED,
    alert_snapshots: Path = ALERT_SNAPSHOTS,
    web: Path = WEB,
    otp_cache: Path = OTP_CACHE,
) -> int:
    trips = load_showcase_trips(showcase_trips)
    required = required_offline_files(
        trips,
        emission_factors=emission_factors,
        processed=processed,
        alert_snapshots=alert_snapshots,
        web=web,
        otp_cache=otp_cache,
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        for path in missing:
            print(path)
        return 1
    print("offline ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
