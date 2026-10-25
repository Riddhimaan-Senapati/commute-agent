"""Live Bluebikes dock availability from the GBFS feed.

The feed is discovered from ``gbfs.json`` and its ``station_status`` entry is read.
Results are cached for ``TTL_S`` seconds, failures included, so an offline planner
does not wait on the network for every request. Availability is used only when
``COMMUTE_BLUEBIKES_LIVE=1``; otherwise ``fetch_availability`` returns None without
touching the network, and callers plan as if availability were unknown.

Trip data keys stations by their legacy id, which GBFS also publishes as
``legacy_id``. Each station is indexed under both its GBFS ``station_id`` and its
``legacy_id`` so either id finds it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping

import httpx

from commute_agent.models import DockCounts

GBFS_ROOT_URL = "https://gbfs.bluebikes.com/gbfs/gbfs.json"
GBFS_LANGUAGE = "en"
TTL_S = 60.0
TIMEOUT_S = 5.0
LIVE_ENV = "COMMUTE_BLUEBIKES_LIVE"

_cache: tuple[float, Mapping[str, DockCounts] | None] | None = None


def live_enabled() -> bool:
    return os.environ.get(LIVE_ENV) == "1"


def _feed_url(root: dict, name: str) -> str:
    """URL of the named feed in a GBFS discovery document."""
    for feed in root["data"][GBFS_LANGUAGE]["feeds"]:
        if feed["name"] == name:
            return str(feed["url"])
    raise ValueError(f"GBFS feed {name} not listed")


def _fetch_station_status() -> dict:
    """Discover the station_status feed from gbfs.json and return its JSON body."""
    root = httpx.get(GBFS_ROOT_URL, timeout=TIMEOUT_S)
    root.raise_for_status()
    response = httpx.get(_feed_url(root.json(), "station_status"), timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def parse_station_status(payload: dict) -> dict[str, DockCounts]:
    """Index a GBFS station_status document by station_id and legacy_id.

    Raises ValueError or KeyError when the document does not have the expected shape.
    """
    stations = payload["data"]["stations"]
    if not isinstance(stations, list):
        raise ValueError("GBFS station_status has no stations list")

    counts: dict[str, DockCounts] = {}
    for station in stations:
        status = DockCounts(
            bikes=int(station["num_bikes_available"]),
            docks=int(station["num_docks_available"]),
            renting=bool(station["is_renting"]),
            returning=bool(station["is_returning"]),
        )
        counts[str(station["station_id"])] = status
        legacy = station.get("legacy_id")
        if legacy is not None:
            counts[str(legacy)] = status
    return counts


def fetch_availability() -> Mapping[str, DockCounts] | None:
    """Station counts keyed by id, or None when live availability is off or unavailable."""
    global _cache
    if not live_enabled():
        return None
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < TTL_S:
        return _cache[1]
    try:
        counts: Mapping[str, DockCounts] | None = parse_station_status(_fetch_station_status())
    except Exception:  # offline, timeout, HTTP error, or a changed feed: plan without it
        counts = None
    _cache = (now, counts)
    return counts


__all__ = ["LIVE_ENV", "fetch_availability", "live_enabled", "parse_station_status"]
