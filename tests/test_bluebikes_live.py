"""Live Bluebikes availability: parsing, flag and cache behaviour, and router and planner use.

The GBFS fetch is monkeypatched; tests never touch the network. The fixture is a trimmed
real ``station_status`` snapshot in ``tests/fixtures/bluebikes_station_status.json``.
"""

import json
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from commute_agent import bluebikes_live, planner
from commute_agent.models import DockCounts, EmissionFactor, LegMode, Point
from commute_agent.planner import BLUEBIKES_UNCHECKED_NOTE, FIXED_NOTES, PlannerContext, plan_trip
from commute_agent.streets import router
from commute_agent.streets.router import StreetGraphs, street_routes

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bluebikes_station_status.json"
CRASH_SOURCE = "test crash source label"
DEPARTS = datetime.fromisoformat("2026-10-27T08:15:00-04:00")

# Fixture stations: 529 has no bikes, 374 has bikes, 54 has a dock free.
NO_BIKES = "529"
HAS_BIKES = "374"
HAS_DOCKS = "54"
ORIGIN_COORDS = (42.3551, -71.0649)
DESTINATION_COORDS = (42.3589, -71.0611)


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bluebikes_live, "_cache", None)


@pytest.fixture
def live_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bluebikes_live.LIVE_ENV, "1")


@pytest.fixture
def status_payload() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def availability(status_payload: dict) -> dict[str, DockCounts]:
    return bluebikes_live.parse_station_status(status_payload)


def _stations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station_id": [NO_BIKES, HAS_BIKES, HAS_DOCKS],
            "name": ["Empty dock", "Has bikes", "Has docks"],
            "lat": [ORIGIN_COORDS[0], ORIGIN_COORDS[0] + 0.0001, DESTINATION_COORDS[0]],
            "lon": [ORIGIN_COORDS[1], ORIGIN_COORDS[1] + 0.0001, DESTINATION_COORDS[1]],
        }
    )


def _graphs(G) -> StreetGraphs:
    return StreetGraphs(walk=G, bike=G, drive=G, stations=_stations(), crash_source=CRASH_SOURCE)


def _factors() -> dict[LegMode, EmissionFactor]:
    return {
        mode: EmissionFactor(value=value, unit="g_per_passenger_km", source=mode)
        for mode, value in (("walk", 0.0), ("bike", 0.0), ("bluebikes", 0.0), ("drive", 170.0))
    }


def _bluebikes(options):
    return next(opt for opt in options if opt.kind == "bluebikes")


def test_flag_off_returns_none_without_fetching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(bluebikes_live.LIVE_ENV, raising=False)

    def fail() -> dict:
        raise AssertionError("network must not be touched when the flag is off")

    monkeypatch.setattr(bluebikes_live, "_fetch_station_status", fail)
    assert bluebikes_live.fetch_availability() is None


def test_fixture_parses_by_station_id_and_legacy_id(availability: dict[str, DockCounts]) -> None:
    empty = availability[NO_BIKES]
    assert empty.bikes == 0
    assert availability[HAS_BIKES].bikes == 8
    assert availability[HAS_BIKES].renting and availability[HAS_BIKES].returning
    assert availability[HAS_DOCKS].docks == 1
    # Each station is also indexed by its UUID station_id, which the trip data does not use.
    uuid_key = "a3b94ed8-8301-4ec6-baf2-64ac493b5106"
    assert availability[uuid_key] == empty


def test_station_without_bikes_is_skipped(fixture_graph, availability) -> None:
    G = fixture_graph
    options, omitted = street_routes(
        _graphs(G), _factors(), _origin(), _destination(), availability=availability
    )

    bluebikes = _bluebikes(options)
    # Without availability the empty station is the nearest start; with it, HAS_BIKES is.
    assert bluebikes.summary == "Bluebikes from Has bikes to Has docks"
    assert "8 bikes" in bluebikes.sources["time_min"]
    assert "1 docks" in bluebikes.sources["time_min"]


def test_unknown_availability_keeps_nearest_station(fixture_graph) -> None:
    G = fixture_graph
    options, _ = street_routes(_graphs(G), _factors(), _origin(), _destination(), availability=None)

    bluebikes = _bluebikes(options)
    assert bluebikes.summary == "Bluebikes from Empty dock to Has docks"
    assert bluebikes.sources["time_min"] == router.SPEED_SOURCE["bluebikes"]


def test_no_station_with_bikes_omits_bluebikes(fixture_graph, availability) -> None:
    G = fixture_graph
    empty_only = {NO_BIKES: availability[NO_BIKES]}
    options, omitted = street_routes(
        _graphs(G), _factors(), _origin(), _destination(), availability=empty_only
    )

    assert not any(opt.kind == "bluebikes" for opt in options)
    assert omitted["bluebikes"] == "no station with bikes available"


def test_cache_serves_repeat_calls_within_ttl(
    live_flag, monkeypatch: pytest.MonkeyPatch, status_payload: dict
) -> None:
    calls = []

    def fetch() -> dict:
        calls.append(1)
        return status_payload

    monkeypatch.setattr(bluebikes_live, "_fetch_station_status", fetch)
    first = bluebikes_live.fetch_availability()
    second = bluebikes_live.fetch_availability()
    assert first is not None and first == second
    assert len(calls) == 1


def test_fetch_failure_returns_none_and_is_cached(
    live_flag, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> dict:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(bluebikes_live, "_fetch_station_status", fail)
    assert bluebikes_live.fetch_availability() is None
    assert bluebikes_live._cache is not None and bluebikes_live._cache[1] is None


def test_failure_falls_back_to_unchanged_plan(
    planner_ctx: PlannerContext, otp_cache: None, live_flag, monkeypatch
) -> None:
    def fail() -> dict:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(bluebikes_live, "_fetch_station_status", fail)
    plan = _plan(planner_ctx)

    assert plan.routes
    assert BLUEBIKES_UNCHECKED_NOTE in plan.notes
    assert plan.notes == [*FIXED_NOTES, BLUEBIKES_UNCHECKED_NOTE]


def test_note_absent_when_availability_checked(
    planner_ctx: PlannerContext, otp_cache: None, availability, monkeypatch
) -> None:
    monkeypatch.setattr(planner.bluebikes_live, "fetch_availability", lambda: availability)
    plan = _plan(planner_ctx)

    assert BLUEBIKES_UNCHECKED_NOTE not in plan.notes
    assert plan.notes == list(FIXED_NOTES)


def test_note_present_when_flag_off(planner_ctx: PlannerContext, otp_cache: None) -> None:
    plan = _plan(planner_ctx)
    assert plan.notes[-1] == BLUEBIKES_UNCHECKED_NOTE


def _plan(ctx: PlannerContext):
    return plan_trip(
        ctx,
        Point(lat=ORIGIN_COORDS[0], lon=ORIGIN_COORDS[1]),
        Point(lat=DESTINATION_COORDS[0], lon=DESTINATION_COORDS[1]),
        DEPARTS,
        alert_source="none",
        alerts=[],
        alert_clock=None,
    )


def _origin() -> Point:
    return Point(lat=ORIGIN_COORDS[0], lon=ORIGIN_COORDS[1])


def _destination() -> Point:
    return Point(lat=DESTINATION_COORDS[0], lon=DESTINATION_COORDS[1])
