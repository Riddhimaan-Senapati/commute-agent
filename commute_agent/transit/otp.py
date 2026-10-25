"""OpenTripPlanner client, response cache, and itinerary-to-route conversion.

Offline mode reads a cached raw OTP response from ``data/cache/otp/<key>.json``.
Live mode posts ``PLAN_QUERY`` to a local OTP instance, writes the cache, and
falls back with an omit reason when OTP is unreachable.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import polyline

from commute_agent.emissions import co2_source, leg_co2_g
from commute_agent.models import (
    EmissionFactor,
    Leg,
    LegMode,
    Point,
    RouteOption,
    TransitRef,
    Weekday,
)
from commute_agent.paths import OTP_CACHE
from commute_agent.routes import assemble_route
from commute_agent.streets import StreetGraphs, score_bike_coords, score_walk_coords

OTP_URL = "http://localhost:8080/otp/gtfs/v1"
# When set, offline mode asks this OTP server for trips the cache lacks (and caches them),
# and live mode uses it instead of OTP_URL.
OTP_URL_ENV = "COMMUTE_OTP_URL"
FEED_PREFIX = "mbta:"
TIMEZONE = ZoneInfo("America/New_York")
OTP_TIMEOUT_S = 20.0

# How the trip reaches the first transit stop. "bike" is a second query whose cache entries
# are kept apart from the walk ones by an access suffix on the key.
TransitAccess = Literal["walk", "bike"]

OTP_MODE_TO_LEG_MODE: dict[str, LegMode] = {
    "WALK": "walk",
    "BICYCLE": "bike",
    "SUBWAY": "subway",
    "TRAM": "light_rail",
    "BUS": "bus",
    "RAIL": "commuter_rail",
    "FERRY": "ferry",
}

# Source label for the time objective of a scheduled transit itinerary.
TRANSIT_TIME_SOURCE = "OTP scheduled itinerary from the published MBTA GTFS feed"
# Bike legs take OTP's cycling estimate, not BIKE_KPH from the street router.
BIKE_TRANSIT_TIME_SOURCE = (
    "OTP scheduled itinerary from the published MBTA GTFS feed; "
    "bike legs use OTP's cycling time estimate"
)

NOT_CACHED = "transit not cached for this trip"
OTP_UNREACHABLE = "OTP unreachable"

PLAN_QUERY = """\
query Plan($from: PlanCoordinateInput!, $to: PlanCoordinateInput!, $when: OffsetDateTime!) {
  planConnection(
    origin: {location: {coordinate: $from}}
    destination: {location: {coordinate: $to}}
    dateTime: {earliestDeparture: $when}
    modes: {transitOnly: true}
    first: 5
  ) {
    edges { node {
      start end duration
      legs {
        mode duration distance
        from { name stop { gtfsId parentStation { gtfsId } } }
        to { name stop { gtfsId parentStation { gtfsId } } }
        route { gtfsId shortName longName type }
        trip { directionId }
        intermediateStops { gtfsId parentStation { gtfsId } }
        legGeometry { points }
      }
    } }
  }
}"""

# OTP rejects an empty exclude list, so the excluding variant is a separate query.
PLAN_QUERY_EXCLUDING = PLAN_QUERY.replace(
    "$when: OffsetDateTime!)", "$when: OffsetDateTime!, $exclude: [String!]!)"
).replace(
    "    first: 5\n",
    "    first: 5\n    preferences: {transit: {filters: [{exclude: [{routes: $exclude}]}]}}\n",
)

# OTP 2.10 rejects BICYCLE for access unless it is also used for egress and transfer, so a
# bike-access trip is bike on every street leg. BICYCLE_PARKING returns no transit on this
# graph (no bike park-and-ride stations are built), so it is not queried.
# trip.bikesAllowed is added so the GTFS bike flag of each transit leg can be read.
PLAN_QUERY_BIKE = PLAN_QUERY.replace(
    "modes: {transitOnly: true}",
    "modes: {transit: {access: [BICYCLE], egress: [BICYCLE], transfer: [BICYCLE]}}",
).replace("trip { directionId }", "trip { directionId bikesAllowed }")


def otp_cache_key(origin: Point, destination: Point, weekday: Weekday, depart_local: str) -> str:
    """Stable 16-hex-char key for one origin/destination/weekday/time request."""
    raw = (
        f"{origin.lat:.4f},{origin.lon:.4f};"
        f"{destination.lat:.4f},{destination.lon:.4f};"
        f"{weekday};{depart_local}"
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _cache_key_for_datetime(
    origin: Point,
    destination: Point,
    depart_at: datetime,
    exclude_routes: tuple[str, ...] = (),
    access: TransitAccess = "walk",
) -> str:
    local = depart_at.astimezone(TIMEZONE)
    weekday: Weekday = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )[local.weekday()]
    clock = local.strftime("%H:%M")
    if exclude_routes:
        clock += ";exclude=" + ",".join(sorted(exclude_routes))
    if access == "bike":
        # Walk-access keys carry no suffix, so their existing cache entries still resolve.
        clock += ";access=bike"
    return otp_cache_key(origin, destination, weekday, clock)


def fetch_itineraries(
    origin: Point,
    destination: Point,
    depart_at: datetime,
    *,
    live: bool,
    cache_dir: Path = OTP_CACHE,
    exclude_routes: tuple[str, ...] = (),
    access: TransitAccess = "walk",
) -> tuple[dict | None, str | None]:
    """Return ``(raw_response, None)`` or ``(None, omit_reason)``.

    Offline reads ``cache_dir/<key>.json``. On a miss it returns ``NOT_CACHED``, unless
    ``COMMUTE_OTP_URL`` is set, in which case it queries OTP as live mode does. Live posts
    ``PLAN_QUERY`` to OTP (20 s timeout), writes the cache, and returns
    ``OTP_UNREACHABLE`` on any ``httpx.HTTPError``.
    ``exclude_routes`` holds MBTA route ids (no feed prefix) that OTP must not use; the
    excluding request has its own cache entry.
    ``access="bike"`` posts ``PLAN_QUERY_BIKE`` instead, with its own cache entry. It takes
    no exclusions, so it cannot be combined with ``exclude_routes``.
    """
    if access == "bike" and exclude_routes:
        raise ValueError("bike access does not support route exclusions")
    key = _cache_key_for_datetime(origin, destination, depart_at, exclude_routes, access)
    cache_path = cache_dir / f"{key}.json"

    url = os.environ.get(OTP_URL_ENV)
    if not live:
        if cache_path.exists():
            return json.loads(cache_path.read_text()), None
        if not url:
            return None, NOT_CACHED

    local = depart_at.astimezone(TIMEZONE)
    variables = {
        "from": {"latitude": origin.lat, "longitude": origin.lon},
        "to": {"latitude": destination.lat, "longitude": destination.lon},
        "when": local.isoformat(),
    }
    query = PLAN_QUERY
    if access == "bike":
        query = PLAN_QUERY_BIKE
    if exclude_routes:
        variables["exclude"] = [FEED_PREFIX + route for route in sorted(exclude_routes)]
        query = PLAN_QUERY_EXCLUDING
    try:
        response = httpx.post(
            url or OTP_URL,
            json={"query": query, "variables": variables},
            timeout=OTP_TIMEOUT_S,
        )
        response.raise_for_status()
        raw = response.json()
    except httpx.HTTPError:
        return None, OTP_UNREACHABLE

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(raw, indent=2))
    return raw, None


def _strip_prefix(gtfs_id: str | None) -> str | None:
    if gtfs_id is None:
        return None
    if gtfs_id.startswith(FEED_PREFIX):
        return gtfs_id[len(FEED_PREFIX) :]
    return gtfs_id


def _decode_coords(leg: dict) -> list[tuple[float, float]]:
    geometry = leg.get("legGeometry") or {}
    points = geometry.get("points")
    if not points:
        return []
    # polyline.decode returns (lat, lon) pairs; swap to (lon, lat).
    return [(lon, lat) for lat, lon in polyline.decode(points)]


def _stop_ids(leg: dict) -> list[str]:
    """Board, intermediate, and alight stop ids plus parent stations, deduped, prefix removed."""
    ids: list[str] = []

    def add_stop(stop: dict | None) -> None:
        if not stop:
            return
        gtfs_id = _strip_prefix(stop.get("gtfsId"))
        if gtfs_id:
            ids.append(gtfs_id)
        parent = stop.get("parentStation") or {}
        parent_id = _strip_prefix(parent.get("gtfsId"))
        if parent_id:
            ids.append(parent_id)

    add_stop((leg.get("from") or {}).get("stop"))
    for intermediate in leg.get("intermediateStops") or []:
        # Intermediate stops may be bare stop objects or wrapped under "stop".
        add_stop(intermediate.get("stop") if "stop" in intermediate else intermediate)
    add_stop((leg.get("to") or {}).get("stop"))

    return list(dict.fromkeys(ids))


def _transit_ref(leg: dict) -> TransitRef:
    route = leg.get("route") or {}
    trip = leg.get("trip") or {}
    short = route.get("shortName") or ""
    long = route.get("longName") or ""
    return TransitRef(
        route_id=_strip_prefix(route.get("gtfsId")) or "",
        route_name=short or long,
        route_type=int(route.get("type") or 0),
        direction_id=int(trip.get("directionId")),
        stop_ids=_stop_ids(leg),
    )


def _build_leg(
    otp_leg: dict,
    graphs: StreetGraphs,
    factors: dict[LegMode, EmissionFactor],
) -> Leg:
    otp_mode = otp_leg.get("mode", "")
    mode = OTP_MODE_TO_LEG_MODE.get(otp_mode, "walk")
    coords = _decode_coords(otp_leg)
    distance_m = float(otp_leg.get("distance") or 0.0)
    duration_min = float(otp_leg.get("duration") or 0.0) / 60.0

    if mode == "walk":
        crash_risk, risk_basis_m = score_walk_coords(graphs.walk, coords)
        transit = None
    elif mode == "bike":
        crash_risk, risk_basis_m = score_bike_coords(graphs.bike, coords)
        transit = None
    else:
        crash_risk, risk_basis_m = 0.0, {}
        transit = _transit_ref(otp_leg)

    return Leg(
        mode=mode,
        coords=coords,
        distance_m=distance_m,
        duration_min=duration_min,
        co2_g=leg_co2_g(factors, mode, distance_m),
        crash_risk=crash_risk,
        risk_basis_m=risk_basis_m,
        transit=transit,
    )


def _summary(legs: list[Leg]) -> str:
    """Transit route names, with "Bike" for each bike leg, e.g. "Bike, then Red Line"."""
    labels: list[str] = []
    for leg in legs:
        if leg.mode == "bike":
            labels.append("Bike")
        elif leg.transit and leg.transit.route_name:
            labels.append(leg.transit.route_name)
    if not labels:
        return "Transit"
    if len(labels) == 1:
        return labels[0]
    return labels[0] + ", then " + ", then ".join(labels[1:])


def transit_routes(
    raw: dict,
    graphs: StreetGraphs,
    factors: dict[LegMode, EmissionFactor],
    first_id: int = 1,
) -> list[RouteOption]:
    """One RouteOption per itinerary, ids ``transit-<first_id>`` onward.

    Itineraries with no transit leg are dropped. Walk legs get crash risk from
    ``score_walk_coords`` and bike legs from ``score_bike_coords``; vehicle legs get crash
    risk 0 and a fixed source label.
    """
    edges = (((raw or {}).get("data") or {}).get("planConnection") or {}).get("edges") or []

    routes: list[RouteOption] = []
    n = first_id - 1
    for edge in edges:
        node = edge.get("node") or {}
        legs = [_build_leg(leg, graphs, factors) for leg in node.get("legs") or []]
        if not any(leg.transit is not None for leg in legs):
            continue

        n += 1
        has_bike = any(leg.mode == "bike" for leg in legs)
        routes.append(
            assemble_route(
                route_id=f"transit-{n}",
                kind="transit",
                summary=_summary(legs),
                legs=legs,
                time_source=BIKE_TRANSIT_TIME_SOURCE if has_bike else TRANSIT_TIME_SOURCE,
                co2_source=co2_source(factors, [leg.mode for leg in legs]),
                crash_source=graphs.crash_source,
            )
        )
    return routes
