"""Tests for commute_agent.streets.exposure."""

import networkx as nx
import numpy as np
import pandas as pd

from commute_agent.streets.exposure import (
    PRIOR_MIN_EDGES,
    bluebikes_proxy,
    fit_calibration,
    road_class_prior,
)


def test_fit_calibration_perfect_line():
    scale, r2 = fit_calibration(np.array([1, 2, 3]), np.array([2, 4, 6]))
    assert scale == 2.0
    assert abs(r2 - 1.0) < 1e-12


def test_fit_calibration_scale_formula():
    proxy = np.array([1.0, 2.0, 4.0])
    observed = np.array([3.0, 5.0, 7.0])
    scale, _ = fit_calibration(proxy, observed)
    expected = (proxy * observed).sum() / (proxy**2).sum()
    assert abs(scale - expected) < 1e-12


def test_bluebikes_proxy_two_trips_add_two_on_shortest_path(fixture_graph):
    """Two trips between the same corner stations add 2 to each edge of the
    length-shortest path and 0 elsewhere."""
    G = fixture_graph
    # Station coordinates at fixture node 0 (bottom-left) and node 8 (top-right).
    src_node, dst_node = 0, 8
    stations = pd.DataFrame(
        {
            "station_id": ["S0", "S8"],
            "lat": [G.nodes[src_node]["y"], G.nodes[dst_node]["y"]],
            "lon": [G.nodes[src_node]["x"], G.nodes[dst_node]["x"]],
        }
    )
    trips = pd.DataFrame({"start_station_id": ["S0"], "end_station_id": ["S8"], "trips": [2]})

    proxy = bluebikes_proxy(G, trips, stations)

    # Reconstruct the expected length-shortest path and its min-length edges.
    path = nx.shortest_path(G, src_node, dst_node, weight="length")
    expected_edges = set()
    for u, v in zip(path, path[1:], strict=False):
        edges = G[u][v]
        key = min(edges, key=lambda k: edges[k]["length"])
        expected_edges.add((u, v, key))

    assert expected_edges  # non-trivial path
    for edge in expected_edges:
        assert proxy.get(edge) == 2
    # No traversal count leaks onto edges off the path.
    for edge, count in proxy.items():
        if edge not in expected_edges:
            assert count == 0 or edge in expected_edges


def test_bluebikes_proxy_skips_unreachable(fixture_graph):
    G = fixture_graph.copy()
    # Add an isolated node far away acting as an unreachable station target.
    G.add_node(999, x=-70.0, y=43.0)
    stations = pd.DataFrame(
        {
            "station_id": ["S0", "ISO"],
            "lat": [G.nodes[0]["y"], 43.0],
            "lon": [G.nodes[0]["x"], -70.0],
        }
    )
    trips = pd.DataFrame({"start_station_id": ["S0"], "end_station_id": ["ISO"], "trips": [5]})
    proxy = bluebikes_proxy(G, trips, stations)
    assert proxy == {} or all(c == 0 for c in proxy.values())


def test_road_class_prior_small_class_gets_global_median():
    n = PRIOR_MIN_EDGES + 5
    # 'big' class has >= PRIOR_MIN_EDGES measured edges; 'small' has fewer.
    highway = pd.Series(["big"] * n + ["small"] * 3)
    exposure = pd.Series([10.0] * n + [1000.0, 1000.0, 1000.0])
    measured = pd.Series([True] * (n + 3))

    prior = road_class_prior(highway, exposure, measured)

    global_median = exposure.median()
    big_median = exposure[:n].median()
    # Big class keeps its own median.
    assert prior.iloc[0] == big_median
    # Small class (fewer than PRIOR_MIN_EDGES) falls back to the global median.
    assert prior.iloc[-1] == global_median


def test_road_class_prior_large_class_uses_own_median():
    n = PRIOR_MIN_EDGES
    highway = pd.Series(["residential"] * n)
    exposure = pd.Series([float(i) for i in range(n)])
    measured = pd.Series([True] * n)
    prior = road_class_prior(highway, exposure, measured)
    assert prior.iloc[0] == exposure.median()
