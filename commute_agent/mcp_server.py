"""MCP tool server. Owns all planner state for one session: alert source, plans, and choices.

plan_trip and get_active_alerts also take an alert_source argument, which the API uses to
give each browser session its own source without changing the stored one. plan_trip,
record_choice, get_preferences and clear_preferences take a session_id that keys the
stored choices; null means the shared "" session."""

import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import httpx
import yaml
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from commute_agent import planner, preferences
from commute_agent.emissions import EmissionFactorsError, load_emission_factors
from commute_agent.gazetteer import _haversine_m, build_gazetteer, geocode
from commute_agent.models import (
    Alert,
    AlertRef,
    AlertSnapshot,
    AlertSources,
    AlertsView,
    ChoiceEvent,
    GeocodeResult,
    Objectives,
    Place,
    PlanResult,
    Point,
    PreferenceWeights,
    RouteOption,
    ShowcaseTrip,
    ShowcaseTrips,
    Tradeoff,
)
from commute_agent.pareto import tradeoff
from commute_agent.paths import ALERT_SNAPSHOTS, PROCESSED, SHOWCASE_TRIPS
from commute_agent.planner import PlannerContext
from commute_agent.traffic import TrafficFactorsError
from commute_agent.transit.alerts import MBTA_LIVE_SOURCE, effect_score, load_alerts
from commute_agent.transit.alerts import list_alert_sources as alert_source_names

LIVE_ALERT_TTL_S = 60.0
SHOWCASE_MATCH_M = 300.0
NO_DEPARTURE = {"", "null", "none"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
LIVE_SOURCE = "live"
NO_ALERTS = "none"
MBTA_UNAVAILABLE_NOTE = "MBTA live alerts could not be fetched, so no alerts were applied."

mcp = FastMCP("pareto-commute")


@dataclass
class SessionState:
    ctx: PlannerContext
    trips: list[ShowcaseTrip]
    gazetteer: list[Place]
    alert_source: str = NO_ALERTS
    plans: dict[str, PlanResult] = field(default_factory=dict)
    plan_alerts: dict[str, dict[str, AlertRef]] = field(default_factory=dict)
    # Monotonic fetch time, alerts, and the failure note (None when the fetch worked).
    live_alerts: tuple[float, list[Alert], str | None] | None = None


STATE: SessionState | None = None


def _state() -> SessionState:
    if STATE is None:
        raise RuntimeError("MCP server state is not configured")
    return STATE


def _load_alerts_for_source(source: str) -> tuple[list[Alert], datetime | None, str | None]:
    """Alerts, their clock, and a note when a live fetch failed. Live fetches share a 60 s cache.
    A failed MBTA fetch returns no alerts and the note; it is cached like a successful one."""
    state = _state()
    if source not in (LIVE_SOURCE, MBTA_LIVE_SOURCE):
        alerts, clock = load_alerts(source, live=False, snapshot_dir=ALERT_SNAPSHOTS)
        return alerts, clock, None
    now = time.monotonic()
    if state.live_alerts and now - state.live_alerts[0] < LIVE_ALERT_TTL_S:
        _, alerts, note = state.live_alerts
        return alerts, None, note
    try:
        alerts, clock = load_alerts(source, live=True, snapshot_dir=ALERT_SNAPSHOTS)
        note = None
    except (httpx.HTTPError, ValueError):
        alerts, clock, note = [], None, MBTA_UNAVAILABLE_NOTE
    state.live_alerts = (now, alerts, note)
    return alerts, clock, note


def _without_coords(route: RouteOption) -> RouteOption:
    legs = [leg.model_copy(update={"coords": []}) for leg in route.legs]
    return route.model_copy(update={"legs": legs})


def _agent_view(plan: PlanResult) -> PlanResult:
    # The agent describes every route it gets, so it never sees routes outside the set.
    routes = [_without_coords(r) for r in plan.routes]
    return plan.model_copy(update={"routes": routes, "filtered": []})


def _route(plan: PlanResult, route_id: str) -> RouteOption:
    for route in plan.routes:
        if route.id == route_id:
            return route
    raise KeyError(f"Unknown route_id {route_id} in plan {plan.query_id}")


def _plan(query_id: str) -> PlanResult:
    plans = _state().plans
    if query_id not in plans:
        raise KeyError(f"Unknown query_id {query_id}")
    return plans[query_id]


def _alert_source_view() -> AlertSources:
    return AlertSources(
        sources=alert_source_names(ALERT_SNAPSHOTS),
        active=_state().alert_source,
        live_enabled=_state().ctx.live,
    )


def _check_alert_source(name: str) -> None:
    known = alert_source_names(ALERT_SNAPSHOTS)
    if name not in known:
        raise ValueError(f"Unknown alert source {name!r}. Known sources: {known}")
    if name == LIVE_SOURCE and not _state().ctx.live:
        raise ValueError("The live alert source needs --live mode.")


def _source(alert_source: str | None) -> str:
    """The given alert source after validation, or the one set with set_alert_source."""
    if alert_source is None:
        return _state().alert_source
    _check_alert_source(alert_source)
    return alert_source


def _is_synthetic(source: str) -> bool:
    if source in (NO_ALERTS, LIVE_SOURCE):
        return False
    for path in ALERT_SNAPSHOTS.glob("*.json"):
        snapshot = AlertSnapshot.model_validate_json(path.read_text())
        if snapshot.label == source:
            return snapshot.synthetic
    return False


@mcp.tool()
def geocode_place(
    query: Annotated[str, Field(description="A place name or a 'lat,lon' pair.")],
) -> GeocodeResult:
    """Resolve a Boston place name to candidate points from the local gazetteer."""
    return geocode(_state().gazetteer, query)


@mcp.tool()
def plan_trip(
    origin_lat: Annotated[float, Field(description="Origin latitude.")],
    origin_lon: Annotated[float, Field(description="Origin longitude.")],
    destination_lat: Annotated[float, Field(description="Destination latitude.")],
    destination_lon: Annotated[float, Field(description="Destination longitude.")],
    depart_at: Annotated[
        str | None, Field(description="Departure time in ISO 8601, or null for the default.")
    ] = None,
    alert_source: Annotated[
        str | None,
        Field(description="Alert source to use, or null for the one set with set_alert_source."),
    ] = None,
    session_id: Annotated[
        str | None,
        Field(
            description="Session whose choices order the routes, or null for the shared session."
        ),
    ] = None,
) -> PlanResult:
    """Plan a trip inside Boston and return the Pareto set of routes with trade-offs."""
    state = _state()
    depart = parse_depart_at(depart_at)
    origin = Point(lat=origin_lat, lon=origin_lon)
    destination = Point(lat=destination_lat, lon=destination_lon)
    showcase_note = None
    trip = None if state.ctx.live else match_showcase(state.trips, origin, destination)
    if trip is not None:
        # Offline transit comes from a cache keyed on the showcase points and departure.
        origin, destination = trip.origin, trip.destination
        name = f"'{trip.origin_name} to {trip.destination_name}'"
        if depart is None:
            depart = showcase_departure(trip)
            showcase_note = (
                f"Planned as showcase trip {name} at its fixed demo departure, "
                f"{trip.weekday.capitalize()} {trip.depart_local}."
            )
        else:
            showcase_note = f"Matched showcase trip {name}; planned at the chosen departure time."
    source = _source(alert_source)
    alerts, clock, alert_note = _load_alerts_for_source(source)
    plan = planner.plan_trip(
        state.ctx, origin, destination, depart, source, alerts, clock, session_id or ""
    )
    if showcase_note:
        plan = plan.model_copy(update={"notes": [showcase_note, *plan.notes]})
    if alert_note:
        plan = plan.model_copy(update={"alert_note": alert_note})
    state.plans[plan.query_id] = plan
    state.plan_alerts[plan.query_id] = {
        alert.id: AlertRef(
            id=alert.id, header=alert.header, effect=alert.effect, score=effect_score(alert)
        )
        for alert in alerts
    }
    return _agent_view(plan)


@mcp.tool()
def get_plan(
    query_id: Annotated[str, Field(description="The query_id returned by plan_trip.")],
) -> PlanResult:
    """Return a full stored plan, including leg coordinates for drawing the map."""
    return _plan(query_id)


@mcp.tool()
def compare_routes(
    query_id: Annotated[str, Field(description="The query_id of a stored plan.")],
    route_id: Annotated[str, Field(description="The route to compare.")],
    reference_route_id: Annotated[str, Field(description="The reference route.")],
) -> Tradeoff:
    """Compare two routes in one stored plan and return the signed differences."""
    plan = _plan(query_id)
    refs = _state().plan_alerts[query_id]
    return tradeoff(_route(plan, route_id), _route(plan, reference_route_id), refs)


@mcp.tool()
def get_active_alerts(
    alert_source: Annotated[
        str | None,
        Field(description="Alert source to use, or null for the one set with set_alert_source."),
    ] = None,
) -> AlertsView:
    """Return the alert source in use and its alerts."""
    source = _source(alert_source)
    alerts, clock, note = _load_alerts_for_source(source)
    refs = [
        AlertRef(id=a.id, header=a.header, effect=a.effect, score=effect_score(a)) for a in alerts
    ]
    return AlertsView(
        source=source,
        synthetic=_is_synthetic(source),
        alert_clock=clock,
        alerts=refs,
        note=note,
    )


@mcp.tool()
def record_choice(
    query_id: Annotated[str, Field(description="The query_id of the plan the user chose from.")],
    route_id: Annotated[str, Field(description="The route the user chose.")],
    session_id: Annotated[
        str | None,
        Field(description="Session that made the choice, or null for the shared session."),
    ] = None,
) -> PreferenceWeights:
    """Store the user's choice of one route from a plan and return the refit preference weights."""
    state = _state()
    plan = _plan(query_id)
    chosen = _route(plan, route_id)
    rejected: list[Objectives] = [r.objectives for r in plan.routes if r.id != route_id]
    event = ChoiceEvent(
        query_id=query_id,
        chosen_at=datetime.now(UTC),
        chosen=chosen.objectives,
        rejected=rejected,
    )
    session = session_id or ""
    preferences.record_choice(event, state.ctx.preferences_db, session)
    return preferences.current_weights(state.ctx.preferences_db, session)


@mcp.tool()
def get_preferences(
    session_id: Annotated[
        str | None,
        Field(description="Session whose choices to summarize, or null for the shared session."),
    ] = None,
) -> PreferenceWeights:
    """Return the current preference weights and how many choices they came from."""
    return preferences.current_weights(_state().ctx.preferences_db, session_id or "")


@mcp.tool()
def list_alert_sources() -> AlertSources:
    """List the alert sources the user can select."""
    return _alert_source_view()


@mcp.tool()
def set_alert_source(
    name: Annotated[str, Field(description="none, a snapshot label, or live in live mode.")],
) -> AlertSources:
    """Select the alert source that later plans use."""
    _check_alert_source(name)
    _state().alert_source = name
    return _alert_source_view()


@mcp.tool()
def clear_preferences(
    session_id: Annotated[
        str | None,
        Field(description="Session whose choices to delete, or null for the shared session."),
    ] = None,
) -> PreferenceWeights:
    """Delete every choice stored for one session and return equal preference weights."""
    session = session_id or ""
    db = _state().ctx.preferences_db
    preferences.clear_choices(db, session)
    return preferences.current_weights(db, session)


@mcp.tool()
def list_showcase_trips() -> ShowcaseTrips:
    """List the five demo trips."""
    return ShowcaseTrips(trips=_state().trips)


def parse_depart_at(value: str | None) -> datetime | None:
    """None for a missing, empty, or 'null' departure. A naive time is America/New_York."""
    if value is None or value.strip().lower() in NO_DEPARTURE:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(
            f"Invalid depart_at {value!r}. Use ISO 8601 such as 2026-10-27T08:15:00-04:00, "
            "or omit depart_at for the default departure."
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=planner.TIMEZONE)


def match_showcase(
    trips: list[ShowcaseTrip], origin: Point, destination: Point
) -> ShowcaseTrip | None:
    """The showcase trip whose both ends lie within SHOWCASE_MATCH_M of the request."""
    for trip in trips:
        if (
            _haversine_m(trip.origin, origin) <= SHOWCASE_MATCH_M
            and _haversine_m(trip.destination, destination) <= SHOWCASE_MATCH_M
        ):
            return trip
    return None


def showcase_departure(trip: ShowcaseTrip) -> datetime:
    """The next date on the trip's weekday at its local demo time, America/New_York."""
    now = datetime.now(planner.TIMEZONE)
    hour, minute = (int(part) for part in trip.depart_local.split(":"))
    days = (WEEKDAYS.index(trip.weekday) - now.weekday()) % 7
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days)
    return candidate if candidate > now else candidate + timedelta(days=7)


def load_trips(path: Path = SHOWCASE_TRIPS) -> list[ShowcaseTrip]:
    raw = yaml.safe_load(path.read_text())
    return [ShowcaseTrip.model_validate(item) for item in raw]


def configure(state: SessionState) -> None:
    global STATE
    STATE = state


def build_state(live: bool) -> SessionState:
    ctx = planner.load_context(live=live, processed=PROCESSED)
    trips = load_trips()
    return SessionState(ctx=ctx, trips=trips, gazetteer=build_gazetteer(PROCESSED, trips))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    live = "--live" in args
    try:
        load_emission_factors()
        configure(build_state(live))
    except EmissionFactorsError as error:
        print(f"emission factors: {error}", file=sys.stderr)
        return 2
    except TrafficFactorsError as error:
        print(f"traffic factors: {error}", file=sys.stderr)
        return 2
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
