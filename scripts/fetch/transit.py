"""Fetch raw transit datasets for the Boston commute-routing project (group: transit).

Rerunnable, stdlib-only. Run with:
    uv run --no-project --python 3.12 python scripts/fetch/transit.py
"""
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import RAW, USER_AGENT, download, manifest_entry, write_manifest

TRANSIT = RAW / "transit"

# License URLs verified to resolve (200) against the official sites. The MBTA
# license is linked from mbta.com/developers/gtfs; the path named in the task
# brief (/developers/mbta-performance/license-agreement) 404s.
MBTA_LICENSE = (
    "MBTA/MassDOT Developer License Agreement, "
    "https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf"
)
BLUEBIKES_LICENSE = (
    "Bluebikes Data License Agreement, https://www.bluebikes.com/data-license-agreement"
)

# Unauthenticated MBTA V3 API is capped at 20 req/min; we make far fewer, but
# space API calls so a rerun never trips the limit.
MBTA_PACE_SECONDS = 4


def get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def fetch_gtfs(entries: list[dict]) -> None:
    url = "https://cdn.mbta.com/MBTA_GTFS.zip"
    dest = download(url, TRANSIT / "MBTA_GTFS.zip")
    entries.append(manifest_entry(dest, url, MBTA_LICENSE, None, "MBTA GTFS static feed (zip)"))


def fetch_routes(entries: list[dict]) -> None:
    url = "https://api-v3.mbta.com/routes?filter[type]=0,1"
    payload = get_json(url)
    dest = TRANSIT / "mbta_routes.json"
    dest.write_text(json.dumps(payload, indent=2) + "\n")
    rows = len(payload.get("data", []))
    entries.append(manifest_entry(dest, url, MBTA_LICENSE, rows, "MBTA V3 subway routes (type 0,1)"))


def fetch_alerts(entries: list[dict]) -> None:
    url = "https://api-v3.mbta.com/alerts?filter[route_type]=0,1,3&page[limit]=500"
    payload = get_json(url)
    # New timestamped snapshot each run so raw alert history accumulates.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = TRANSIT / f"mbta_alerts_{stamp}.json"
    dest.write_text(json.dumps(payload, indent=2) + "\n")
    rows = len(payload.get("data", []))
    entries.append(
        manifest_entry(dest, url, MBTA_LICENSE, rows, "MBTA V3 alerts snapshot (route_type 0,1,3)")
    )


def fetch_massgis(entries: list[dict]) -> None:
    base = (
        "https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/"
        "MBTA_Rapid_Transit/MapServer/0/query"
        "?where=1%3D1&outFields=STATION,LINE,ROUTE&returnGeometry=true&outSR=4326&f=json"
    )
    features: list[dict] = []
    offset = 0
    while True:
        page = get_json(f"{base}&resultOffset={offset}")
        batch = page.get("features", [])
        features.extend(batch)
        if page.get("exceededTransferLimit") and batch:
            offset += len(batch)
            continue
        # Keep last page's metadata but replace the feature list with all pages.
        page["features"] = features
        payload = page
        break
    dest = TRANSIT / "massgis_rapid_transit_stations.json"
    dest.write_text(json.dumps(payload, indent=2) + "\n")
    entries.append(
        manifest_entry(
            dest, base, "MassGIS open data (Commonwealth of Massachusetts)",
            len(features), "MassGIS MBTA Rapid Transit stations with geometry (EPSG:4326)",
        )
    )


def fetch_bluebikes(entries: list[dict]) -> None:
    url = "https://s3.amazonaws.com/hubway-data/202411-bluebikes-tripdata.zip"
    dest = download(url, TRANSIT / "202411-bluebikes-tripdata.zip")
    entries.append(
        manifest_entry(dest, url, BLUEBIKES_LICENSE, None, "Bluebikes trip data, Nov 2024 (zip)")
    )


def main() -> None:
    TRANSIT.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []

    fetch_gtfs(entries)

    fetch_routes(entries)
    time.sleep(MBTA_PACE_SECONDS)
    fetch_alerts(entries)
    time.sleep(MBTA_PACE_SECONDS)

    fetch_massgis(entries)
    fetch_bluebikes(entries)

    path = write_manifest("transit", entries)
    print(f"wrote {path} with {len(entries)} entries")


if __name__ == "__main__":
    main()
