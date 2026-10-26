"""Trip planner: candidates from street and transit routing, Pareto filter, labels, trade-offs.

The MCP server passes the active alerts in. The planner never reads session state.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from commute_agent import bluebikes_live
from commute_agent.emissions import load_emission_factors
from commute_agent.models import (
    WEEKDAYS,
    Alert,
    AlertRef,
    DockCounts,
    EmissionFactor,
    LegMode,
    PlanResult,
    Point,
    RouteKind,
    RouteOption,
    Tradeoff,
)
from commute_agent.pareto import assign_labels, filtered_out, pareto_front, tradeoff
from commute_agent.paths import PREFERENCES_DB, PROCESSED
from commute_agent.preferences import current_weights, order_routes
from commute_agent.service_area import SERVICE_AREA_FILE, outside_message
from commute_agent.streets import StreetGraphs, load_street_graphs, street_routes
from commute_agent.traffic import (
    TrafficBucket,
    TrafficFactors,
    free_flow_factors,
    load_traffic_factors,
)
from commute_agent.transit.alerts import apply_disruptions, effect_score, suspended_routes
from commute_agent.transit.otp import fetch_itineraries, transit_routes

TIMEZONE = ZoneInfo("America/New_York")
DEFAULT_OFFLINE_DEPARTURE = ("tuesday", "08:15")
FREE_FLOW_NOTE = "Drive time is free-flow and ignores traffic."
FIXED_NOTES = [
    "Crash risk covers walking and biking segments only. "
    "Driving and transit vehicle crash risk is not modeled.",
    FREE_FLOW_NOTE,
    "Disruption risk is a prototype index from MBTA alert effects, not an MBTA prediction.",
]
BLUEBIKES_UNCHECKED_NOTE = "Bluebikes dock availability is not checked."


@dataclass(frozen=True)
class PlannerContext:
    graphs: StreetGraphs
    factors: dict[LegMode, EmissionFactor]
    boundary: BaseGeometry
    live: bool
    preferences_db: Path = PREFERENCES_DB
    traffic: TrafficFactors = field(default_factory=free_flow_factors)


def _drive_note(bucket: TrafficBucket) -> str:
    """Free-flow when the departure's factor is 1.0; otherwise names the bucket and source."""
    if bucket.factor.value == 1.0:
        return FREE_FLOW_NOTE
    return (
        f"Drive time uses a time-of-day traffic factor ({bucket.factor.value:g} for "
        f"{bucket.day_type} {bucket.label}, source: {bucket.factor.source})."
    )


def _notes(bucket: TrafficBucket, availability: Mapping[str, DockCounts] | None) -> list[str]:
    drive = _drive_note(bucket)
    notes = [drive if note == FREE_FLOW_NOTE else note for note in FIXED_NOTES]
    if availability is None:
        notes.append(BLUEBIKES_UNCHECKED_NOTE)
    return notes


class OutsideServiceArea(Exception):
    """Raised when a trip point lies outside the service area (see commute_agent.service_area)."""


def load_context(live: bool, processed: Path = PROCESSED) -> PlannerContext:
    geojson = json.loads((processed / SERVICE_AREA_FILE).read_text())
    boundary = unary_union([shape(feature["geometry"]) for feature in geojson["features"]])
    return PlannerContext(
        graphs=load_street_graphs(processed),
        factors=load_emission_factors(),
        boundary=boundary,
        live=live,
        traffic=load_traffic_factors(),
    )


def resolve_departure(depart_at: datetime | None, live: bool) -> datetime:
    """Live: depart_at or now. Offline: depart_at, or the next Tuesday 08:15 from today."""
    now = datetime.now(TIMEZONE)
    if depart_at is not None:
        return depart_at
    if live:
        return now
    weekday_name, clock = DEFAULT_OFFLINE_DEPARTURE
    hour, minute = (int(part) for part in clock.split(":"))
    days_ahead = (WEEKDAYS.index(weekday_name) - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _outside(ctx: PlannerContext, origin: Point, destination: Point) -> list[str]:
    names = []
    if not ctx.boundary.contains(ShapelyPoint(origin.lon, origin.lat)):
        names.append("origin")
    if not ctx.boundary.contains(ShapelyPoint(destination.lon, destination.lat)):
        names.append("destination")
    return names


def _alternatives(
    ctx: PlannerContext,
    origin: Point,
    destination: Point,
    depart_at: datetime,
    transit: list[RouteOption],
    alerts: list[Alert],
    at: datetime,
    first_id: int,
    step_free: bool = False,
) -> list[RouteOption]:
    """Transit itineraries that avoid every route an active alert suspends, or [] when
    nothing is suspended or the excluding request is not available (not cached offline).
    ``transit`` holds every transit candidate so far, for the suspended-route check."""
    used = {leg.transit.route_id for r in transit for leg in r.legs if leg.transit}
    suspended = suspended_routes(alerts, at, used)
    if not suspended:
        return []
    raw, _ = fetch_itineraries(
        origin,
        destination,
        depart_at,
        live=ctx.live,
        exclude_routes=suspended,
        step_free=step_free,
    )
    if raw is None:
        return []
    return transit_routes(raw, ctx.graphs, ctx.factors, first_id=first_id)


def _bike_transit(
    ctx: PlannerContext, origin: Point, destination: Point, depart_at: datetime, first_id: int
) -> list[RouteOption]:
    """Bike-access transit itineraries, or [] when the bike query is not available (offline
    with no cache entry, or OTP unreachable)."""
    raw, _ = fetch_itineraries(origin, destination, depart_at, live=ctx.live, access="bike")
    if raw is None:
        return []
    return transit_routes(raw, ctx.graphs, ctx.factors, first_id=first_id)


def gather_candidates(
    ctx: PlannerContext,
    origin: Point,
    destination: Point,
    depart_at: datetime,
    alerts: list[Alert],
    alert_clock: datetime | None,
    availability: Mapping[str, DockCounts] | None = None,
    *,
    step_free: bool = False,
) -> tuple[list[RouteOption], dict[RouteKind, str], list[AlertRef]]:
    """Street plus transit candidates with disruption applied. Raises OutsideServiceArea.

    ``step_free`` asks OTP for wheelchair-accessible transit and scores elevator closures."""
    outside = _outside(ctx, origin, destination)
    if outside:
        raise OutsideServiceArea(outside_message(outside))

    candidates, omitted = street_routes(
        ctx.graphs, ctx.factors, origin, destination, depart_at, ctx.traffic, availability
    )
    at = alert_clock or depart_at
    raw, reason = fetch_itineraries(
        origin, destination, depart_at, live=ctx.live, step_free=step_free
    )
    transit: list[RouteOption] = []
    if raw is None:
        omitted["transit"] = reason
    else:
        transit = transit_routes(raw, ctx.graphs, ctx.factors)
    # A bike leg is not step-free, so step-free plans skip bike-access transit.
    bike_transit = (
        []
        if step_free
        else _bike_transit(ctx, origin, destination, depart_at, first_id=len(transit) + 1)
    )
    candidates += transit + bike_transit
    if raw is not None:
        candidates += _alternatives(
            ctx,
            origin,
            destination,
            depart_at,
            transit + bike_transit,
            alerts,
            at,
            first_id=len(transit) + len(bike_transit) + 1,
            step_free=step_free,
        )
        if not any(route.kind == "transit" for route in candidates):
            omitted["transit"] = "no transit itinerary"

    scored, unscored = apply_disruptions(candidates, alerts, at, step_free=step_free)
    return scored, omitted, unscored


def _alert_refs(alerts: list[Alert], step_free: bool) -> dict[str, AlertRef]:
    return {
        alert.id: AlertRef(
            id=alert.id,
            header=alert.header,
            effect=alert.effect,
            score=effect_score(alert, step_free=step_free),
        )
        for alert in alerts
    }


def plan_trip(
    ctx: PlannerContext,
    origin: Point,
    destination: Point,
    depart_at: datetime | None,
    alert_source: str,
    alerts: list[Alert],
    alert_clock: datetime | None,
    session_id: str = "",
    *,
    step_free: bool = False,
) -> PlanResult:
    depart = resolve_departure(depart_at, ctx.live)
    availability = bluebikes_live.fetch_availability()
    candidates, omitted, unscored = gather_candidates(
        ctx, origin, destination, depart, alerts, alert_clock, availability, step_free=step_free
    )
    front = pareto_front(candidates)
    labeled = assign_labels(front)
    weights = current_weights(ctx.preferences_db, session_id)
    ordered = order_routes(labeled, weights)

    fastest = next((route for route in labeled if "fastest" in route.labels), None)
    refs = _alert_refs(alerts, step_free)
    trades: list[Tradeoff] = (
        [tradeoff(route, fastest, refs) for route in ordered] if fastest is not None else []
    )
    return PlanResult(
        query_id=uuid4().hex[:8],
        origin=origin,
        destination=destination,
        depart_at=depart,
        run_mode="live" if ctx.live else "offline",
        alert_source=alert_source,
        routes=ordered,
        tradeoffs_vs_fastest=trades,
        omitted=omitted,
        candidate_count=len(candidates),
        unscored_alerts=unscored,
        preference=weights,
        notes=_notes(ctx.traffic.factor_for(depart), availability),
        filtered=filtered_out(candidates, front),
    )
