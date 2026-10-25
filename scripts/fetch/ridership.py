"""Fetch the `ridership` group: MBTA Gated Station Entries, historical by year (2014 to 2026).

Rerunnable, stdlib-only. Run with:
    uv run --no-project --python 3.12 python scripts/fetch/ridership.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import RAW, download, manifest_entry, write_manifest

GROUP = "ridership"
OUT = RAW / GROUP

# ArcGIS item 7859894afb5641ce91a2bb03599fdf5b: "MBTA Gated Station Entries - Historical"
# on the MBTA/MassDOT open data portal. The item's licenseInfo is CC0; the portal page
# is https://mbta-massdot.opendata.arcgis.com/datasets/7859894afb5641ce91a2bb03599fdf5b
GSE_PAGE = "https://mbta-massdot.opendata.arcgis.com/datasets/7859894afb5641ce91a2bb03599fdf5b"
GSE_URL = "https://www.arcgis.com/sharing/rest/content/items/7859894afb5641ce91a2bb03599fdf5b/data"
GSE_LICENSE = "CC0 1.0 (ArcGIS item licenseInfo), https://mbta-massdot.opendata.arcgis.com/datasets/7859894afb5641ce91a2bb03599fdf5b"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dest = download(GSE_URL, OUT / "GSE_by_year.zip")
    entries = [
        manifest_entry(
            dest,
            GSE_URL,
            GSE_LICENSE,
            None,
            "MBTA Gated Station Entries - Historical, GSE_by_year.zip (CSV per year, "
            f"listed on {GSE_PAGE}). Unscaled gated entries by service date and 30-minute period.",
        )
    ]
    path = write_manifest(GROUP, entries)
    print(f"wrote {path} with {len(entries)} entries")


if __name__ == "__main__":
    main()
