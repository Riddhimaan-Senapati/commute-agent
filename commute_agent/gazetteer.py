"""Local gazetteer: resolve a place name or a literal lat,lon to a Place.

Pure functions plus one parquet read in build_gazetteer. See the design's
"WS7. Gazetteer" section.
"""

import math
import re
from pathlib import Path

import pandas as pd

from commute_agent.models import GeocodeResult, Place, Point, ShowcaseTrip

COORDS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
SAME_PLACE_M = 150.0

# Lower index wins when merging near-duplicate candidates.
_SOURCE_ORDER: dict[str, int] = {
    "coordinates": 0,
    "mbta_station": 1,
    "showcase": 2,
    "bluebikes_station": 3,
}

_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Lowercase, '&' -> 'and', 'st' token -> 'street', drop punctuation, collapse
    spaces, and drop a trailing 'station'."""
    text = name.lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    tokens = ["street" if tok == "st" else tok for tok in text.split()]
    if tokens and tokens[-1] == "station":
        tokens.pop()
    return _SPACES.sub(" ", " ".join(tokens)).strip()


def build_gazetteer(processed: Path, trips: list[ShowcaseTrip]) -> list[Place]:
    """Rapid transit stations, Bluebikes stations, and each trip's origin and
    destination, as Place objects."""
    places: list[Place] = []

    rapid = pd.read_parquet(processed / "rapid_transit_stations.parquet")
    for row in rapid.itertuples(index=False):
        places.append(
            Place(
                name=row.name,
                point=Point(lat=float(row.lat), lon=float(row.lon)),
                source="mbta_station",
            )
        )

    bikes = pd.read_parquet(processed / "bluebikes_stations.parquet")
    for row in bikes.itertuples(index=False):
        places.append(
            Place(
                name=row.name,
                point=Point(lat=float(row.lat), lon=float(row.lon)),
                source="bluebikes_station",
            )
        )

    for trip in trips:
        places.append(Place(name=trip.origin_name, point=trip.origin, source="showcase"))
        places.append(Place(name=trip.destination_name, point=trip.destination, source="showcase"))

    return places


def _haversine_m(a: Point, b: Point) -> float:
    r = 6_371_000.0
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _score(query_norm: str, query_tokens: set[str], place: Place) -> int:
    name_norm = normalize_name(place.name)
    if name_norm == query_norm:
        return 2
    name_tokens = set(name_norm.split())
    if query_tokens and query_tokens.issubset(name_tokens):
        return 1
    return 0


def geocode(gazetteer: list[Place], query: str) -> GeocodeResult:
    """Resolve a query to a GeocodeResult.

    A COORDS match returns status ok with source 'coordinates'. Otherwise score 2 for
    an exact normalized match, 1 when every query token is in the name tokens, else 0.
    Keep the top-scoring candidates, merge those within SAME_PLACE_M of each other
    (keeping the first by source order), then: one -> ok, several -> ambiguous,
    none -> not_found.
    """
    m = COORDS.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        place = Place(name=query.strip(), point=Point(lat=lat, lon=lon), source="coordinates")
        return GeocodeResult(status="ok", candidates=[place])

    query_norm = normalize_name(query)
    query_tokens = set(query_norm.split())

    scored = [(place, _score(query_norm, query_tokens, place)) for place in gazetteer]
    best = max((s for _, s in scored), default=0)
    if best == 0:
        return GeocodeResult(status="not_found", candidates=[])

    candidates = [place for place, s in scored if s == best]
    merged = _merge_near(candidates)

    status = "ok" if len(merged) == 1 else "ambiguous"
    return GeocodeResult(status=status, candidates=merged)


def _merge_near(candidates: list[Place]) -> list[Place]:
    """Collapse candidates within SAME_PLACE_M of each other, keeping the first by
    source order (mbta_station, showcase, bluebikes_station)."""
    kept: list[Place] = []
    for place in candidates:
        near = next((k for k in kept if _haversine_m(k.point, place.point) <= SAME_PLACE_M), None)
        if near is None:
            kept.append(place)
        elif _SOURCE_ORDER[place.source] < _SOURCE_ORDER[near.source]:
            kept[kept.index(near)] = place
    return kept
