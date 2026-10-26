"""Unit tests for the street router (WS3) against the small fixture graph.

The fixture ``graph_small.graphml`` is a 3x3 grid in WGS84 with ``risk``,
``risk_basis``, ``length``, ``highway``, ``speed_kph``, and ``travel_time`` edge
attributes and ``x``/``y`` node attributes. We build a ``StreetGraphs`` directly
from it (used for walk, bike, and drive) and pass a crash-source test value, so
the tests need no processed data files.
"""

import networkx as nx
import pandas as pd
import pytest

from commute_agent.models import EmissionFactor, LegMode, Point
from commute_agent.streets import router
from commute_agent.streets.router import StreetGraphs, score_walk_coords, street_routes

CRASH_SOURCE = "test crash source label"


def _factors() -> dict[LegMode, EmissionFactor]:
    return {
        mode: EmissionFactor(value=value, unit="g_per_passenger_km", source=mode)
        for mode, value in (
            ("walk", 0.0),
            ("bike", 0.0),
            ("bluebikes", 0.0),
            ("drive", 170.0),
        )
    }


def _stations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "station_id": ["A", "B"],
            "name": ["Dock A", "Dock B"],
            "lat": [42.3551, 42.3589],
            "lon": [-71.0649, -71.0611],
        }
    )


def _graphs(G: nx.MultiDiGraph) -> StreetGraphs:
    return StreetGraphs(walk=G, bike=G, drive=G, stations=_stations(), crash_source=CRASH_SOURCE)


def _point(G: nx.MultiDiGraph, node: int) -> Point:
    return Point(lat=float(G.nodes[node]["y"]), lon=float(G.nodes[node]["x"]))


def _uses_edge(route, G: nx.MultiDiGraph, u: int, v: int) -> bool:
    """True when the route's single leg traverses the undirected street (u, v)."""
    coords = route.legs[0].coords
    endpoints = {
        (float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
        (float(G.nodes[v]["x"]), float(G.nodes[v]["y"])),
    }
    hits = [c for c in coords if (c[0], c[1]) in endpoints]
    # Both node endpoints appear consecutively in the polyline when the edge is used.
    return len(hits) >= 2 and _consecutive(coords, u, v, G)


def _consecutive(coords, u, v, G) -> bool:
    pu = (float(G.nodes[u]["x"]), float(G.nodes[u]["y"]))
    pv = (float(G.nodes[v]["x"]), float(G.nodes[v]["y"]))
    for a, b in zip(coords, coords[1:], strict=False):
        if {(a[0], a[1]), (b[0], b[1])} == {pu, pv}:
            return True
    return False


def test_safer_variant_avoids_raised_edge_walk1_still_uses_it(fixture_graph) -> None:
    """Raising one edge's risk makes the safer variant avoid that edge while
    ``walk-1`` still uses it (Requirement 6.1, 6.5)."""
    G = fixture_graph
    # Edge (0, 1) is the shortest first hop from node 0 toward node 2. Raise its
    # risk so the time-shortest path still takes it but a safer variant detours.
    G.edges[(0, 1, 0)]["risk"] = 500.0

    origin = _point(G, 0)
    destination = _point(G, 2)
    options, omitted = street_routes(_graphs(G), _factors(), origin, destination)

    walks = {opt.id: opt for opt in options if opt.kind == "walk"}
    assert "walk-1" in walks, omitted
    assert _uses_edge(walks["walk-1"], G, 0, 1)

    safer = [opt for oid, opt in walks.items() if oid != "walk-1"]
    assert safer, "expected at least one safer walk variant"
    assert any(not _uses_edge(opt, G, 0, 1) for opt in safer)
    assert walks["walk-1"].summary == "Walk, fastest"
    assert any(opt.summary.startswith("Walk, safer variant ") for opt in safer)


def test_score_walk_coords_returns_edge_risk_within_one_percent(fixture_graph) -> None:
    """A polyline along one fixture edge returns that edge's risk within 1%
    (Requirement 6.6)."""
    G = fixture_graph
    target = 7.5
    # Set both directed edges of the 4-5 street so whichever direction the
    # midpoint snaps to carries the same risk.
    G.edges[(4, 5, 0)]["risk"] = target
    G.edges[(5, 4, 0)]["risk"] = target

    coords = [
        (float(G.nodes[4]["x"]), float(G.nodes[4]["y"])),
        (float(G.nodes[5]["x"]), float(G.nodes[5]["y"])),
    ]
    risk, risk_basis_m = score_walk_coords(G, coords)

    assert risk == pytest.approx(target, rel=0.01)
    assert sum(risk_basis_m.values()) == pytest.approx(
        float(G.edges[(4, 5, 0)]["length"]), rel=0.01
    )


def test_disconnected_origin_reports_no_path(fixture_graph) -> None:
    """An origin on a disconnected fixture node reports ``no path`` in omitted
    kinds (Requirement 4.6)."""
    G = fixture_graph
    # Add an isolated node far from the grid; no edge touches it.
    G.add_node(999, x=-71.040, y=42.360)

    origin = Point(lat=42.360, lon=-71.040)  # snaps to the isolated node
    destination = _point(G, 2)
    options, omitted = street_routes(_graphs(G), _factors(), origin, destination)

    assert omitted.get("walk") == "no path"
    assert omitted.get("bike") == "no path"
    assert not any(opt.kind == "walk" for opt in options)


def test_street_routes_ids_summaries_and_sources(fixture_graph) -> None:
    """Route ids, summaries, and the crash source label follow the design."""
    G = fixture_graph
    origin = _point(G, 0)
    destination = _point(G, 2)
    options, _ = street_routes(_graphs(G), _factors(), origin, destination)

    by_id = {opt.id: opt for opt in options}
    assert "walk-1" in by_id
    assert by_id["walk-1"].summary == "Walk, fastest"
    assert by_id["walk-1"].sources["crash_risk"] == CRASH_SOURCE
    assert by_id["walk-1"].sources["time_min"] == router.SPEED_SOURCE["walk"]

    assert "bike-1" in by_id
    assert by_id["bike-1"].summary == "Bike, fastest"

    assert "drive-1" in by_id
    assert by_id["drive-1"].summary == "Drive"
    assert by_id["drive-1"].objectives.crash_risk == 0.0


def test_bluebikes_composition_three_legs(fixture_graph) -> None:
    """A bluebikes option composes walk, bluebikes, walk legs and names both
    stations (Requirement 6.4)."""
    G = fixture_graph
    graphs = _graphs(G)
    # Origin near station A (node 0 corner), destination near station B (node 8).
    origin = _point(G, 0)
    destination = _point(G, 8)
    options, omitted = street_routes(graphs, _factors(), origin, destination)

    bluebikes = [opt for opt in options if opt.kind == "bluebikes"]
    if not bluebikes:
        # Acceptable only when both ends snap to the same station.
        assert omitted.get("bluebikes") == "same station at both ends"
        return

    opt = bluebikes[0]
    assert opt.id == "bluebikes-1"
    assert [leg.mode for leg in opt.legs] == ["walk", "bluebikes", "walk"]
    assert opt.summary == "Bluebikes from Dock A to Dock B"


def test_drive_route_cites_the_crash_source_label(fixture_graph):
    routes, _ = street_routes(
        _graphs(fixture_graph), _factors(), _point(fixture_graph, 0), _point(fixture_graph, 8)
    )
    drive = next(r for r in routes if r.kind == "drive")
    assert drive.sources["crash_risk"] == CRASH_SOURCE


def test_drive_time_multiplied_by_departure_factor(fixture_graph):
    from datetime import datetime

    from commute_agent.models import TrafficFactor
    from commute_agent.traffic import TrafficBucket, TrafficFactors

    G = fixture_graph
    origin, destination = _point(G, 0), _point(G, 8)
    base, _ = street_routes(_graphs(G), _factors(), origin, destination)
    free_flow = next(r for r in base if r.kind == "drive").legs[0].duration_min

    factor = TrafficFactor(value=1.5, source="test source")
    buckets = tuple(TrafficBucket(day, "0-24", 0, 24, factor) for day in ("weekday", "weekend"))
    traffic = TrafficFactors(buckets=buckets)
    routes, _ = street_routes(
        _graphs(G),
        _factors(),
        origin,
        destination,
        depart_at=datetime(2026, 10, 27, 17, 0),
        traffic=traffic,
    )
    drive = next(r for r in routes if r.kind == "drive")
    assert drive.legs[0].duration_min == pytest.approx(free_flow * 1.5)
    assert "traffic factor 1.5 for weekday 0-24 (test source)" in drive.sources["time_min"]
    walk = next(r for r in routes if r.kind == "walk")
    assert "traffic" not in walk.sources["time_min"]


def test_drive_without_departure_stays_free_flow(fixture_graph):
    routes, _ = street_routes(
        _graphs(fixture_graph), _factors(), _point(fixture_graph, 0), _point(fixture_graph, 8)
    )
    drive = next(r for r in routes if r.kind == "drive")
    assert "no departure time" in drive.sources["time_min"]
