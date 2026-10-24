"""Tests for commute_agent.gazetteer."""

from pathlib import Path

import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

from commute_agent.gazetteer import build_gazetteer, geocode, normalize_name
from commute_agent.models import Place, Point, ShowcaseTrip


def _place(name: str, lat: float, lon: float, source: str = "mbta_station") -> Place:
    return Place(name=name, point=Point(lat=lat, lon=lon), source=source)


def test_normalize_name() -> None:
    assert normalize_name("Park Street Station") == "park street"
    assert normalize_name("Park St") == "park street"
    assert normalize_name("JFK/UMass") == "jfk umass"
    assert normalize_name("Dudley & Washington") == "dudley and washington"


def test_park_st_matches_park_street_station() -> None:
    gaz = [
        _place("Park Street", 42.3564, -71.0624),
        _place("Davis", 42.3967, -71.1218),
    ]
    result = geocode(gaz, "Park St")
    assert result.status == "ok"
    assert len(result.candidates) == 1
    assert result.candidates[0].name == "Park Street"


def test_two_far_candidates_are_ambiguous() -> None:
    # Two equally scored exact matches more than 150 m apart do not merge.
    gaz = [
        _place("Central", 42.3654, -71.1037),
        _place("Central", 42.3600, -71.0900),  # ~1.4 km away
    ]
    result = geocode(gaz, "Central")
    assert result.status == "ambiguous"
    assert len(result.candidates) == 2


def test_two_near_candidates_merge_to_one() -> None:
    # Two equally scored exact matches within 150 m collapse to one; mbta wins bluebikes.
    gaz = [
        _place("Harvard", 42.37340, -71.11890, source="bluebikes_station"),
        _place("Harvard", 42.37345, -71.11895, source="mbta_station"),  # ~7 m away
    ]
    result = geocode(gaz, "Harvard")
    assert result.status == "ok"
    assert len(result.candidates) == 1
    assert result.candidates[0].source == "mbta_station"


def test_unknown_place_not_found() -> None:
    gaz = [_place("Park Street", 42.3564, -71.0624)]
    result = geocode(gaz, "Zzyzx")
    assert result.status == "not_found"
    assert result.candidates == []


def test_coords_literal_returns_ok() -> None:
    result = geocode([], "42.3601, -71.0589")
    assert result.status == "ok"
    assert result.candidates[0].source == "coordinates"
    assert result.candidates[0].point.lat == 42.3601
    assert result.candidates[0].point.lon == -71.0589


def test_build_gazetteer_reads_parquet(tmp_path: Path) -> None:
    pd.DataFrame(
        {
            "name": ["Park Street", "Davis"],
            "line": ["Red", "Red"],
            "lat": [42.3564, 42.3967],
            "lon": [-71.0624, -71.1218],
        }
    ).to_parquet(tmp_path / "rapid_transit_stations.parquet")
    pd.DataFrame(
        {
            "station_id": ["A1", "A2"],
            "name": ["MIT at Mass Ave", "Kendall T"],
            "lat": [42.3581, 42.3625],
            "lon": [-71.0936, -71.0862],
        }
    ).to_parquet(tmp_path / "bluebikes_stations.parquet")

    trip = ShowcaseTrip(
        name="demo",
        origin_name="Home",
        destination_name="Work",
        origin=Point(lat=42.30, lon=-71.06),
        destination=Point(lat=42.36, lon=-71.06),
        weekday="monday",
        depart_local="08:00",
        description="a demo trip",
    )

    places = build_gazetteer(tmp_path, [trip])
    by_source: dict[str, list[Place]] = {}
    for p in places:
        by_source.setdefault(p.source, []).append(p)

    assert len(by_source["mbta_station"]) == 2
    assert len(by_source["bluebikes_station"]) == 2
    assert {p.name for p in by_source["showcase"]} == {"Home", "Work"}
    park = next(p for p in places if p.name == "Park Street")
    assert park.point.lat == 42.3564


# Property 14: Coordinate geocoding round trip.
@given(
    lat=st.floats(min_value=42.2, max_value=42.4, allow_nan=False),
    lon=st.floats(min_value=-71.2, max_value=-70.9, allow_nan=False),
)
def test_property_coordinate_round_trip(lat: float, lon: float) -> None:
    result = geocode([], f"{lat},{lon}")
    assert result.status == "ok"
    assert len(result.candidates) == 1
    point = result.candidates[0].point
    assert point.lat == lat
    assert point.lon == lon
