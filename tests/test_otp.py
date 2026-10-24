"""Unit tests for commute_agent.transit.otp."""

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
