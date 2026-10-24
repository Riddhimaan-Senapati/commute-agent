"""Warm the OTP response cache for every Showcase_Trip and eval OD pair.

Resolves each trip's weekday and local time to the next such date in
America/New_York, requests a live OTP plan, and prints one line per trip with
its itinerary count. Exits 1 when any Showcase_Trip has zero itineraries or when
the Fields Corner trip has no leg on the Red Line.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from commute_agent.emissions import EmissionFactorsError, load_emission_factors
from commute_agent.models import Point, ShowcaseTrips, Weekday
from commute_agent.paths import OTP_CACHE, ROOT, SHOWCASE_TRIPS
from commute_agent.streets import load_street_graphs
from commute_agent.transit.alerts import list_alert_sources, load_alerts, suspended_routes
from commute_agent.transit.otp import TIMEZONE, fetch_itineraries, transit_routes

EVAL_OD_PAIRS = ROOT / "eval" / "od_pairs.yaml"

_WEEKDAYS: tuple[Weekday, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
FIELDS_CORNER_NAME = "fields-corner-to-downtown"


@dataclass(frozen=True)
class Trip:
    name: str
    origin: Point
    destination: Point
    weekday: Weekday
    depart_local: str  # "HH:MM"


def next_departure(weekday: Weekday, depart_local: str, *, now: datetime | None = None) -> datetime:
    """Next America/New_York datetime on ``weekday`` at ``depart_local`` from ``now``."""
    now = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE)
    hour, minute = (int(x) for x in depart_local.split(":"))
    target_dow = _WEEKDAYS.index(weekday)
    days_ahead = (target_dow - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def load_showcase_trips(path: Path = SHOWCASE_TRIPS) -> list[Trip]:
    data = yaml.safe_load(path.read_text())
    trips = ShowcaseTrips.model_validate({"trips": data}).trips
    return [Trip(t.name, t.origin, t.destination, t.weekday, t.depart_local) for t in trips]


def load_od_pairs(path: Path = EVAL_OD_PAIRS) -> list[Trip]:
    if not path.exists():
        return []
    rows = yaml.safe_load(path.read_text()) or []
    trips: list[Trip] = []
    for row in rows:
        trips.append(
            Trip(
                name=row["name"],
                origin=Point(**row["origin"]),
                destination=Point(**row["destination"]),
                weekday=row["weekday"],
                depart_local=row["depart_local"],
            )
        )
    return trips


def cache_alternatives(trip: Trip, depart_at: datetime, routes, graphs, factors) -> int:
    """Cache the route-excluding request each committed snapshot would trigger offline."""
    used = {leg.transit.route_id for r in routes for leg in r.legs if leg.transit}
    status = 0
    for source in list_alert_sources()[2:]:
        alerts, clock = load_alerts(source, live=False)
        suspended = suspended_routes(alerts, clock or depart_at, used)
        if not suspended:
            continue
        raw, reason = fetch_itineraries(
            trip.origin, trip.destination, depart_at, live=True, exclude_routes=suspended
        )
        found = len(transit_routes(raw, graphs, factors)) if raw else 0
        print(f"{trip.name} without {','.join(suspended)} ({source}): {found} itineraries")
        status |= int(raw is None)
    return status


def main(argv: list[str] | None = None) -> int:
    try:
        factors = load_emission_factors()
    except EmissionFactorsError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    graphs = load_street_graphs()
    showcase = load_showcase_trips()
    od_pairs = load_od_pairs()

    exit_code = 0
    for trip in showcase + od_pairs:
        depart_at = next_departure(trip.weekday, trip.depart_local)
        raw, reason = fetch_itineraries(
            trip.origin, trip.destination, depart_at, live=True, cache_dir=OTP_CACHE
        )
        if raw is None:
            print(f"{trip.name}: 0 itineraries ({reason})")
            if trip.name == FIELDS_CORNER_NAME or trip in showcase:
                exit_code = 1
            continue

        routes = transit_routes(raw, graphs, factors)
        print(f"{trip.name}: {len(routes)} itineraries")
        if trip in showcase:
            exit_code |= cache_alternatives(trip, depart_at, routes, graphs, factors)

        if trip in showcase and not routes:
            exit_code = 1
        if trip.name == FIELDS_CORNER_NAME:
            has_red = any(
                leg.transit and leg.transit.route_id == "Red"
                for route in routes
                for leg in route.legs
            )
            if not has_red:
                print(f"{trip.name}: no Red Line leg", file=sys.stderr)
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
