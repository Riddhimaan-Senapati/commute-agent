import asyncio
from dataclasses import replace
from datetime import datetime

import httpx
import pytest
from shapely.geometry import box

from commute_agent import mcp_server, planner
from commute_agent.mcp_server import SessionState, load_trips
from commute_agent.models import PlanResult, Point, PreferenceWeights


def call(name: str, arguments: dict):
    _, structured = asyncio.run(mcp_server.mcp.call_tool(name, arguments))
    return structured


def call_error(name: str, arguments: dict) -> str:
    with pytest.raises(Exception) as info:
        asyncio.run(mcp_server.mcp.call_tool(name, arguments))
    return str(info.value)


PLAN_ARGS = {
    "origin_lat": 42.3550,
    "origin_lon": -71.0650,
    "destination_lat": 42.3580,
    "destination_lon": -71.0620,
    "depart_at": "2026-10-27T08:15:00-04:00",
}


def test_plan_trip_strips_coords_and_get_plan_keeps_them(server: SessionState) -> None:
    agent_view = PlanResult.model_validate(call("plan_trip", PLAN_ARGS))
    assert agent_view.routes
    assert all(leg.coords == [] for route in agent_view.routes for leg in route.legs)

    full = PlanResult.model_validate(call("get_plan", {"query_id": agent_view.query_id}))
    assert full.query_id == agent_view.query_id
    assert agent_view.filtered == []
    assert len(full.routes) + len(full.filtered) == full.candidate_count
    assert any(leg.coords for route in full.routes for leg in route.legs)


def test_unknown_route_is_an_error_and_server_keeps_serving(server: SessionState) -> None:
    plan = PlanResult.model_validate(call("plan_trip", PLAN_ARGS))
    message = call_error(
        "compare_routes",
        {"query_id": plan.query_id, "route_id": "nope", "reference_route_id": plan.routes[0].id},
    )
    assert "nope" in message
    assert call("get_preferences", {})["insufficient_data"] is True


def test_live_alert_source_is_rejected_offline(server: SessionState) -> None:
    message = call_error("set_alert_source", {"name": "live"})
    assert "live" in message


def test_alert_source_argument_overrides_the_stored_source(server: SessionState) -> None:
    synthetic = "Synthetic: Red Line suspended JFK/UMass to Park Street"
    plan = call("plan_trip", {**PLAN_ARGS, "alert_source": synthetic})
    assert plan["alert_source"] == synthetic
    assert call("get_active_alerts", {"alert_source": synthetic})["synthetic"] is True
    assert call("list_alert_sources", {})["active"] == "none"


def test_unknown_alert_source_argument_is_an_error(server: SessionState) -> None:
    assert "nope" in call_error("get_active_alerts", {"alert_source": "nope"})


def test_geocode_unknown_place_is_not_found(server: SessionState) -> None:
    assert call("geocode_place", {"query": "Zzyzx"})["status"] == "not_found"


def test_showcase_trips_lists_five_trips(server: SessionState) -> None:
    assert len(call("list_showcase_trips", {})["trips"]) == 5


def test_record_choice_then_clear_restores_equal_weights(server: SessionState) -> None:
    plan = PlanResult.model_validate(call("plan_trip", PLAN_ARGS))
    weights = PreferenceWeights.model_validate(
        call("record_choice", {"query_id": plan.query_id, "route_id": plan.routes[0].id})
    )
    assert weights.n_choices == 1
    cleared = PreferenceWeights.model_validate(call("clear_preferences", {}))
    assert cleared.weights == {o: 0.25 for o in cleared.weights}


@pytest.mark.parametrize("value", [None, "", "  ", "null", "NULL", "None"])
def test_empty_or_null_departure_means_default(value) -> None:
    assert mcp_server.parse_depart_at(value) is None


def test_naive_departure_is_boston_local_time() -> None:
    parsed = mcp_server.parse_depart_at("2026-10-27T08:15")
    assert parsed.isoformat() == "2026-10-27T08:15:00-04:00"


def test_invalid_departure_is_a_recoverable_tool_error(server: SessionState) -> None:
    message = call_error("plan_trip", {**PLAN_ARGS, "depart_at": "tomorrow morning"})
    assert "Invalid depart_at 'tomorrow morning'" in message
    assert "ISO 8601" in message
    assert call("get_preferences", {})["insufficient_data"] is True


def test_null_departure_plans_with_the_default(server: SessionState) -> None:
    plan = PlanResult.model_validate(call("plan_trip", {**PLAN_ARGS, "depart_at": "null"}))
    assert plan.depart_at.strftime("%A %H:%M") == "Tuesday 08:15"


def test_geocoded_points_near_a_showcase_trip_use_its_points_and_departure() -> None:
    fields_corner = load_trips()[0]
    near_origin = Point(lat=42.300010284513554, lon=-71.06192451044731)
    near_destination = Point(lat=42.355458759733466, lon=-71.06049369759702)
    assert mcp_server.match_showcase(load_trips(), near_origin, near_destination) == fields_corner
    departure = mcp_server.showcase_departure(fields_corner)
    assert (
        departure.strftime("%A %H:%M %Z") == "Tuesday 08:15 EDT"
        or departure.strftime("%A %H:%M %Z") == "Tuesday 08:15 EST"
    )


def test_points_far_from_every_showcase_trip_do_not_match() -> None:
    assert (
        mcp_server.match_showcase(
            load_trips(), Point(lat=42.33, lon=-71.07), Point(lat=42.34, lon=-71.08)
        )
        is None
    )


def record_transit_lookups(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Record the departure of every transit lookup; every lookup reports 'not cached'."""
    seen: list[datetime] = []

    def fetch(origin, destination, depart_at, *, live, cache_dir=None, exclude_routes=()):
        seen.append(depart_at)
        return None, "transit not cached for this trip"

    monkeypatch.setattr(planner, "fetch_itineraries", fetch)
    return seen


def widen_boundary(server: SessionState, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture boundary is a small box; the showcase trips lie outside it."""
    wide = replace(server.ctx, boundary=box(-72.0, 42.0, -70.5, 43.0))
    monkeypatch.setattr(server, "ctx", wide)


def showcase_args(trip) -> dict:
    return {
        **PLAN_ARGS,
        "origin_lat": trip.origin.lat,
        "origin_lon": trip.origin.lon,
        "destination_lat": trip.destination.lat,
        "destination_lon": trip.destination.lon,
    }


def test_chosen_departure_reaches_fetch_itineraries(
    server: SessionState, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = record_transit_lookups(monkeypatch)
    chosen = datetime.fromisoformat("2026-10-27T17:30:00-04:00")
    plan = PlanResult.model_validate(
        call("plan_trip", {**PLAN_ARGS, "depart_at": chosen.isoformat()})
    )
    assert seen == [chosen]
    assert plan.depart_at == chosen


def test_chosen_departure_near_a_showcase_trip_keeps_the_chosen_time(
    server: SessionState, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = record_transit_lookups(monkeypatch)
    widen_boundary(server, monkeypatch)
    trip = load_trips()[0]
    chosen = "2026-10-27T17:30:00-04:00"
    plan = PlanResult.model_validate(
        call("plan_trip", {**showcase_args(trip), "depart_at": chosen})
    )
    assert seen == [datetime.fromisoformat(chosen)]
    assert plan.notes[0].startswith("Matched showcase trip")
    assert "fixed demo departure" not in plan.notes[0]


def test_no_departure_near_a_showcase_trip_uses_its_fixed_departure(
    server: SessionState, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = record_transit_lookups(monkeypatch)
    widen_boundary(server, monkeypatch)
    trip = load_trips()[0]
    plan = PlanResult.model_validate(call("plan_trip", {**showcase_args(trip), "depart_at": None}))
    assert seen == [mcp_server.showcase_departure(trip)]
    assert plan.notes[0].startswith("Planned as showcase trip")
    assert "fixed demo departure" in plan.notes[0]


def test_mbta_live_is_selectable_offline(server: SessionState) -> None:
    sources = call("set_alert_source", {"name": "MBTA live"})
    assert sources["active"] == "MBTA live"
    assert "MBTA live" in sources["sources"]


def test_mbta_live_with_a_failed_fetch_gives_no_alerts_and_a_note(
    server: SessionState, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(source, *, live, snapshot_dir):
        raise httpx.ConnectError("MBTA is unreachable")

    monkeypatch.setattr(mcp_server, "load_alerts", fail)
    plan = PlanResult.model_validate(call("plan_trip", {**PLAN_ARGS, "alert_source": "MBTA live"}))
    assert plan.alert_source == "MBTA live"
    assert plan.alert_note == mcp_server.MBTA_UNAVAILABLE_NOTE
    assert plan.routes
    view = call("get_active_alerts", {"alert_source": "MBTA live"})
    assert view["alerts"] == []
    assert view["note"] == mcp_server.MBTA_UNAVAILABLE_NOTE
