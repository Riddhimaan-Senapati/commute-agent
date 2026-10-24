import asyncio

import pytest

from commute_agent import mcp_server
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
