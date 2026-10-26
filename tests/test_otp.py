"""Unit tests for commute_agent.transit.otp."""

import copy
import json
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from commute_agent.models import EmissionFactor, LegMode, Point
from commute_agent.streets import StreetGraphs
from commute_agent.transit import otp

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Minimal emission factors so the converter can score CO2 without a data file.
_FACTORS: dict[LegMode, EmissionFactor] = {
    mode: EmissionFactor(value=0.0, unit="g_per_passenger_km", source="test")
    for mode in (
        "walk",
        "bike",
        "bluebikes",
        "drive",
        "subway",
        "light_rail",
        "bus",
        "commuter_rail",
        "ferry",
    )
}


@pytest.fixture
def graphs(fixture_graph) -> StreetGraphs:
    return StreetGraphs(
        walk=fixture_graph,
        bike=fixture_graph,
        drive=fixture_graph,
        stations=pd.DataFrame(columns=["station_id", "name", "lat", "lon"]),
        crash_source="test crash source label",
    )


def test_transit_routes_fields_corner(graphs: StreetGraphs) -> None:
    # Real OTP 2.10.0 response for fields-corner-to-downtown, Tuesday 08:15.
    raw = json.loads((FIXTURES / "otp_fields_corner.json").read_text())

    routes = otp.transit_routes(raw, graphs, _FACTORS)

    assert [route.id for route in routes] == [f"transit-{n}" for n in range(1, 6)]
    for route in routes:
        assert route.kind == "transit"
        assert [leg.mode for leg in route.legs] == ["walk", "subway", "walk"]
        subway = route.legs[1]
        assert subway.transit is not None
        assert subway.transit.route_id == "Red"
        assert subway.transit.direction_id == 1
        assert isinstance(subway.transit.direction_id, int)
        assert subway.transit.stop_ids[:2] == ["70090", "place-fldcr"]
        assert subway.transit.stop_ids[-2:] == ["70078", "place-dwnxg"]
        assert "place-jfk" in subway.transit.stop_ids
        # Feed prefix is stripped from every id.
        assert all(not sid.startswith("mbta:") for sid in subway.transit.stop_ids)

        # Vehicle legs carry crash risk 0; walk legs get a real score.
        assert subway.crash_risk == 0.0
        assert subway.coords and subway.coords[0] != subway.coords[-1]


def test_cache_key_stable() -> None:
    origin = Point(lat=42.3003, lon=-71.0614)
    dest = Point(lat=42.3555, lon=-71.0603)
    key = otp.otp_cache_key(origin, dest, "tuesday", "08:15")
    assert key == otp.otp_cache_key(origin, dest, "tuesday", "08:15")
    assert len(key) == 16
    assert all(c in "0123456789abcdef" for c in key)


def test_fetch_offline_missing_cache(tmp_path: Path) -> None:
    origin = Point(lat=42.3003, lon=-71.0614)
    dest = Point(lat=42.3555, lon=-71.0603)
    depart = datetime(2026, 10, 27, 8, 15, tzinfo=otp.TIMEZONE)

    raw, reason = otp.fetch_itineraries(origin, dest, depart, live=False, cache_dir=tmp_path)

    assert raw is None
    assert reason == "transit not cached for this trip"


def test_fetch_offline_hit(tmp_path: Path) -> None:
    origin = Point(lat=42.3003, lon=-71.0614)
    dest = Point(lat=42.3555, lon=-71.0603)
    depart = datetime(2026, 10, 27, 8, 15, tzinfo=otp.TIMEZONE)
    key = otp._cache_key_for_datetime(origin, dest, depart)
    payload = {"data": {"planConnection": {"edges": []}}}
    (tmp_path / f"{key}.json").write_text(json.dumps(payload))

    raw, reason = otp.fetch_itineraries(origin, dest, depart, live=False, cache_dir=tmp_path)

    assert reason is None
    assert raw == payload


def test_offline_miss_asks_the_configured_otp_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = Point(lat=42.3097, lon=-71.1151)
    dest = Point(lat=42.3503, lon=-71.0810)
    depart = datetime(2026, 10, 27, 8, 15, tzinfo=otp.TIMEZONE)
    payload = {"data": {"planConnection": {"edges": []}}}
    seen: list[str] = []

    def fake_post(url: str, **kwargs):
        seen.append(url)
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(otp.httpx, "post", fake_post)
    monkeypatch.setenv("COMMUTE_OTP_URL", "http://otp.internal:8080/otp/gtfs/v1")

    first = otp.fetch_itineraries(origin, dest, depart, live=False, cache_dir=tmp_path)
    second = otp.fetch_itineraries(origin, dest, depart, live=False, cache_dir=tmp_path)

    assert first == second == (payload, None)
    assert seen == ["http://otp.internal:8080/otp/gtfs/v1"]  # the second call hit the cache


def test_offline_miss_with_otp_http_error_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_post(url: str, **kwargs):
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr(otp.httpx, "post", fake_post)
    monkeypatch.setenv("COMMUTE_OTP_URL", "http://otp.internal:8080/otp/gtfs/v1")
    depart = datetime(2026, 10, 27, 8, 15, tzinfo=otp.TIMEZONE)
    raw, reason = otp.fetch_itineraries(
        Point(lat=42.3097, lon=-71.1151),
        Point(lat=42.3503, lon=-71.0810),
        depart,
        live=False,
        cache_dir=tmp_path,
    )
    assert (raw, reason) == (None, "OTP unreachable")
    assert not list(tmp_path.iterdir())


def test_fetch_live_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    origin = Point(lat=42.3003, lon=-71.0614)
    dest = Point(lat=42.3555, lon=-71.0603)
    depart = datetime(2026, 10, 27, 8, 15, tzinfo=otp.TIMEZONE)

    def fake_post(url: str, **kwargs):
        request = httpx.Request("POST", url)
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(otp.httpx, "post", fake_post)

    raw, reason = otp.fetch_itineraries(origin, dest, depart, live=True, cache_dir=tmp_path)

    assert raw is None
    assert reason == "OTP unreachable"
    # Nothing was written to the cache on failure.
    assert not list(tmp_path.iterdir())


def test_fetch_live_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    origin = Point(lat=42.3003, lon=-71.0614)
    dest = Point(lat=42.3555, lon=-71.0603)
    depart = datetime(2026, 10, 27, 8, 15, tzinfo=otp.TIMEZONE)

    def fake_post(url: str, **kwargs):
        request = httpx.Request("POST", url)
        raise httpx.TimeoutException("timed out", request=request)

    monkeypatch.setattr(otp.httpx, "post", fake_post)

    raw, reason = otp.fetch_itineraries(origin, dest, depart, live=True, cache_dir=tmp_path)

    assert raw is None
    assert reason == "OTP unreachable"


def test_excluding_request_has_its_own_cache_entry(tmp_path) -> None:
    from datetime import datetime

    from commute_agent.models import Point
    from commute_agent.transit.otp import fetch_itineraries

    o, d = Point(lat=42.3003, lon=-71.0614), Point(lat=42.3555, lon=-71.0603)
    when = datetime.fromisoformat("2026-10-27T08:15:00-04:00")
    (tmp_path / "x.json").write_text("{}")
    base = fetch_itineraries(o, d, when, live=False, cache_dir=tmp_path)
    excluded = fetch_itineraries(
        o, d, when, live=False, cache_dir=tmp_path, exclude_routes=("Red",)
    )
    assert base == excluded == (None, "transit not cached for this trip")
    from commute_agent.transit.otp import _cache_key_for_datetime

    assert _cache_key_for_datetime(o, d, when) != _cache_key_for_datetime(o, d, when, ("Red",))


BIKE_RAW = FIXTURES / "otp_bike_access.json"
FIELDS_CORNER = Point(lat=42.3003, lon=-71.0614)
DOWNTOWN_CROSSING = Point(lat=42.3555, lon=-71.0603)
TUESDAY_0815 = datetime.fromisoformat("2026-10-27T08:15:00-04:00")
# Walk-access key for this trip, computed before the bike access suffix existed.
WALK_KEY_FIELDS_CORNER_TUESDAY_0815 = "ffc582e57e619f4e"


def test_bike_access_transit_routes_have_bike_first_leg(graphs: StreetGraphs) -> None:
    # Real OTP 2.10.0 response for the bike-access query, Fields Corner to Downtown Crossing.
    raw = json.loads(BIKE_RAW.read_text())

    routes = otp.transit_routes(raw, graphs, _FACTORS)

    assert [route.id for route in routes] == [f"transit-{n}" for n in range(1, 6)]
    assert any(route.legs[0].mode == "bike" for route in routes)
    for route in routes:
        assert route.kind == "transit"
        assert route.summary.startswith("Bike, then ")
        bike_leg = route.legs[0]
        assert bike_leg.mode == "bike"
        assert bike_leg.transit is None
        assert bike_leg.coords and bike_leg.distance_m > 0
        assert bike_leg.risk_basis_m
        assert any(leg.transit is not None for leg in route.legs)


def test_bike_legs_are_scored_on_the_bike_graph(fixture_graph) -> None:
    bike = copy.deepcopy(fixture_graph)
    for _u, _v, data in bike.edges(data=True):
        data["risk"] = 1.0
    walk = copy.deepcopy(fixture_graph)
    for _u, _v, data in walk.edges(data=True):
        data["risk"] = 0.0
    graphs = StreetGraphs(
        walk=walk,
        bike=bike,
        drive=fixture_graph,
        stations=pd.DataFrame(columns=["station_id", "name", "lat", "lon"]),
        crash_source="test crash source label",
    )
    raw = json.loads(BIKE_RAW.read_text())

    routes = otp.transit_routes(raw, graphs, _FACTORS)

    bike_legs = [leg for route in routes for leg in route.legs if leg.mode == "bike"]
    assert bike_legs
    assert all(leg.crash_risk > 0 for leg in bike_legs)
    assert all(leg.co2_g == 0.0 for leg in bike_legs)


def test_walk_access_cache_key_is_unchanged() -> None:
    key = otp._cache_key_for_datetime(FIELDS_CORNER, DOWNTOWN_CROSSING, TUESDAY_0815)

    assert key == WALK_KEY_FIELDS_CORNER_TUESDAY_0815
    bike_key = otp._cache_key_for_datetime(
        FIELDS_CORNER, DOWNTOWN_CROSSING, TUESDAY_0815, access="bike"
    )
    assert bike_key != key


def test_existing_walk_cache_entry_still_resolves(tmp_path: Path) -> None:
    payload = {"data": {"planConnection": {"edges": []}}}
    (tmp_path / f"{WALK_KEY_FIELDS_CORNER_TUESDAY_0815}.json").write_text(json.dumps(payload))

    raw, reason = otp.fetch_itineraries(
        FIELDS_CORNER, DOWNTOWN_CROSSING, TUESDAY_0815, live=False, cache_dir=tmp_path
    )

    assert (raw, reason) == (payload, None)


def test_bike_access_cache_entry_is_separate(tmp_path: Path) -> None:
    payload = json.loads(BIKE_RAW.read_text())
    bike_key = otp._cache_key_for_datetime(
        FIELDS_CORNER, DOWNTOWN_CROSSING, TUESDAY_0815, access="bike"
    )
    (tmp_path / f"{bike_key}.json").write_text(json.dumps(payload))

    bike = otp.fetch_itineraries(
        FIELDS_CORNER,
        DOWNTOWN_CROSSING,
        TUESDAY_0815,
        live=False,
        cache_dir=tmp_path,
        access="bike",
    )
    walk = otp.fetch_itineraries(
        FIELDS_CORNER, DOWNTOWN_CROSSING, TUESDAY_0815, live=False, cache_dir=tmp_path
    )

    assert bike == (payload, None)
    assert walk == (None, "transit not cached for this trip")


def test_bike_access_offline_miss_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COMMUTE_OTP_URL", raising=False)

    raw, reason = otp.fetch_itineraries(
        FIELDS_CORNER,
        DOWNTOWN_CROSSING,
        TUESDAY_0815,
        live=False,
        cache_dir=tmp_path,
        access="bike",
    )

    assert (raw, reason) == (None, "transit not cached for this trip")
    assert not list(tmp_path.iterdir())


def test_bike_access_rejects_route_exclusions(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        otp.fetch_itineraries(
            FIELDS_CORNER,
            DOWNTOWN_CROSSING,
            TUESDAY_0815,
            live=False,
            cache_dir=tmp_path,
            exclude_routes=("Red",),
            access="bike",
        )


def test_step_free_cache_key_differs_and_default_key_is_unchanged() -> None:
    origin = Point(lat=42.3003, lon=-71.0614)
    dest = Point(lat=42.3555, lon=-71.0603)
    when = datetime.fromisoformat("2026-10-27T08:15:00-04:00")
    default = otp._cache_key_for_datetime(origin, dest, when)
    # The default key is the pre-feature key: the same hash of the same raw string.
    assert default == otp.otp_cache_key(origin, dest, "tuesday", "08:15")
    step_free = otp._cache_key_for_datetime(origin, dest, when, step_free=True)
    assert step_free != default
    assert step_free == otp.otp_cache_key(origin, dest, "tuesday", "08:15;step-free")


def test_plan_query_default_is_plan_query_and_step_free_adds_wheelchair() -> None:
    assert otp.plan_query() == otp.PLAN_QUERY
    step_free = otp.plan_query(step_free=True)
    assert "preferences: {accessibility: {wheelchair: {enabled: true}}}" in step_free
    both = otp.plan_query(excluding=True, step_free=True)
    # One preferences input carries both the exclusion and the wheelchair preference.
    assert both.count("preferences:") == 1
    assert "$exclude" in both and "wheelchair" in both


def test_fetch_step_free_queries_otp_and_caches_under_its_own_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = Point(lat=42.3003, lon=-71.0614)
    dest = Point(lat=42.3555, lon=-71.0603)
    depart = datetime(2026, 10, 27, 8, 15, tzinfo=otp.TIMEZONE)
    payload = {"data": {"planConnection": {"edges": []}}}
    sent: list[str] = []

    def fake_post(url: str, **kwargs):
        sent.append(kwargs["json"]["query"])
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(otp.httpx, "post", fake_post)
    raw, reason = otp.fetch_itineraries(
        origin, dest, depart, live=True, cache_dir=tmp_path, step_free=True
    )
    assert (raw, reason) == (payload, None)
    assert "wheelchair" in sent[0]
    key = otp._cache_key_for_datetime(origin, dest, depart, step_free=True)
    assert (tmp_path / f"{key}.json").exists()
    # The default key holds nothing, so default offline lookups still miss.
    assert otp.fetch_itineraries(origin, dest, depart, live=False, cache_dir=tmp_path) == (
        None,
        "transit not cached for this trip",
    )
