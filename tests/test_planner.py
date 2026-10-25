import json
from datetime import datetime

import pytest

from commute_agent import planner
from commute_agent.models import PlanResult, Point
from commute_agent.planner import (
    FIXED_NOTES,
    FREE_FLOW_NOTE,
    OutsideServiceArea,
    PlannerContext,
    plan_trip,
    resolve_departure,
)

ORIGIN = Point(lat=42.3550, lon=-71.0650)
DESTINATION = Point(lat=42.3580, lon=-71.0620)


def test_plan_returns_front_with_fastest_label(
    planner_ctx: PlannerContext, otp_cache: None
) -> None:
    plan = plan_trip(
        planner_ctx,
        ORIGIN,
        DESTINATION,
        datetime.fromisoformat("2026-10-27T08:15:00-04:00"),
        alert_source="none",
        alerts=[],
        alert_clock=None,
    )
    PlanResult.model_validate_json(plan.model_dump_json())
    assert plan.routes
    fastest = [route for route in plan.routes if "fastest" in route.labels]
    assert len(fastest) == 1
    assert plan.run_mode == "offline"
    assert plan.notes == FIXED_NOTES
    assert plan.candidate_count >= len(plan.routes)
    assert len(plan.tradeoffs_vs_fastest) == len(plan.routes)


def test_point_outside_boundary_names_origin(planner_ctx: PlannerContext) -> None:
    far = Point(lat=42.5, lon=-71.0)
    with pytest.raises(OutsideServiceArea, match="origin"):
        planner.gather_candidates(
            planner_ctx, far, DESTINATION, datetime(2026, 10, 27, 8, 15), [], None
        )


def test_offline_departure_defaults_to_next_tuesday_0815() -> None:
    departure = resolve_departure(None, live=False)
    assert departure.weekday() == 1
    assert (departure.hour, departure.minute) == (8, 15)
    assert departure.tzinfo is not None
    assert departure.tzname() in ("EDT", "EST")


def test_explicit_departure_is_used_as_given() -> None:
    chosen = datetime(2026, 10, 28, 17, 0)
    assert resolve_departure(chosen, live=False) == chosen


def test_suspended_route_adds_alternatives_that_avoid_it(planner_ctx, fixture_paths, monkeypatch):
    from commute_agent.models import Alert
    from commute_agent.transit.alerts import parse_mbta_alerts

    raw = json.loads((fixture_paths / "otp_fields_corner.json").read_text())
    calls = []

    def fetch(origin, destination, depart_at, *, live, cache_dir=None, exclude_routes=()):
        calls.append(exclude_routes)
        return raw, None

    monkeypatch.setattr(planner, "fetch_itineraries", fetch)
    alerts: list[Alert] = [
        a
        for a in parse_mbta_alerts(json.loads((fixture_paths / "mbta_alerts.json").read_text()))
        if a.id == "synthetic-red-1"
    ]
    when = datetime.fromisoformat("2026-10-27T08:15:00-04:00")
    candidates, _, _ = planner.gather_candidates(
        planner_ctx, ORIGIN, DESTINATION, when, alerts, None
    )
    transit_ids = [r.id for r in candidates if r.kind == "transit"]
    assert calls == [(), ("Red",)]
    assert transit_ids == [f"transit-{n}" for n in range(1, 11)]


def test_drive_note_names_factor_when_departure_has_one(
    planner_ctx: PlannerContext, otp_cache: None
) -> None:
    from dataclasses import replace

    from commute_agent.models import TrafficFactor
    from commute_agent.traffic import TrafficBucket, TrafficFactors

    buckets = tuple(
        TrafficBucket(
            day,
            label,
            start,
            end,
            TrafficFactor(value=1.25 if label == "16-19" else 1.0, source="Test figure"),
        )
        for day in ("weekday", "weekend")
        for label, start, end in (("0-16", 0, 16), ("16-19", 16, 19), ("19-24", 19, 24))
    )
    ctx = replace(planner_ctx, traffic=TrafficFactors(buckets=buckets))
    plan = plan_trip(
        ctx,
        ORIGIN,
        DESTINATION,
        datetime.fromisoformat("2026-10-27T17:00:00-04:00"),
        alert_source="none",
        alerts=[],
        alert_clock=None,
    )
    drive_note = (
        "Drive time uses a time-of-day traffic factor (1.25 for weekday 16-19, "
        "source: Test figure)."
    )
    assert drive_note in plan.notes
    assert FREE_FLOW_NOTE not in plan.notes
    assert plan.notes == [
        FIXED_NOTES[0],
        drive_note,
        FIXED_NOTES[2],
        FIXED_NOTES[3],
    ]
    drive = next(route for route in plan.routes if route.kind == "drive")
    assert "1.25 for weekday 16-19" in drive.sources["time_min"]
