"""Tests for commute_agent.streets.build."""

import shutil

import numpy as np
import osmnx as ox
import pandas as pd

from commute_agent.streets.build import build_risk, snap_points
from commute_agent.streets.risk import SNAP_MAX_M


def _fixture_graph_path(fixture_paths):
    return fixture_paths / "graph_small.graphml"


def test_snap_points_returns_edges_and_distances(fixture_paths):
    G = ox.load_graphml(
        _fixture_graph_path(fixture_paths),
        edge_dtypes={"crashes": int, "exposure": float, "risk": float},
    )
    G_proj = ox.projection.project_graph(G, to_crs="EPSG:26986")
    # Project node 0's own coordinate; it must snap to an incident edge at ~0 m.
    import geopandas as gpd
    from shapely.geometry import Point

    pt = gpd.GeoSeries([Point(G.nodes[0]["x"], G.nodes[0]["y"])], crs="EPSG:4326").to_crs(
        "EPSG:26986"
    )
    edges, dists = snap_points(G_proj, np.array([pt.iloc[0].x]), np.array([pt.iloc[0].y]))
    assert len(edges) == 1
    assert 0 in edges[0]  # incident to node 0
    assert dists[0] < 1.0


def test_build_risk_counts_discarded_over_30m(fixture_paths):
    """Three bike crashes, one ~40 m from any edge, so one is discarded."""
    processed = fixture_paths
    shutil.copy(_fixture_graph_path(processed), processed / "bike.graphml")

    # Two crashes directly on fixture nodes (snap ~0 m), one ~40 m west of the grid.
    crashes = pd.DataFrame(
        {
            "source_dataset": ["boston_vision_zero"] * 3,
            "occurred_at": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"], utc=True),
            "mode": ["bike", "bike", "bike"],
            "lat": [42.3550, 42.3590, 42.3550],
            "lon": [-71.0650, -71.0610, -71.0650 - 0.00049],
        }
    )
    crashes.to_parquet(processed / "crashes.parquet")

    fragment = build_risk("bike", processed)

    assert fragment["crashes_discarded_over_30m"]["bike"] == 1
    assert fragment["crashes_snapped"]["bike"] == 2
    assert fragment["mean_crashes_per_km"]["bike"] >= 0.0


def test_build_risk_distance_threshold_is_30m(fixture_paths):
    """A crash whose snap distance exceeds SNAP_MAX_M is the discarded one."""
    assert SNAP_MAX_M == 30.0


def test_build_risk_writes_attributes_back(fixture_paths):
    processed = fixture_paths
    shutil.copy(_fixture_graph_path(processed), processed / "bike.graphml")
    crashes = pd.DataFrame(
        {
            "source_dataset": ["cambridge_police"],
            "occurred_at": pd.to_datetime(["2021-05-05"], utc=True),
            "mode": ["bike"],
            "lat": [42.3550],
            "lon": [-71.0650],
        }
    )
    crashes.to_parquet(processed / "crashes.parquet")

    build_risk("bike", processed)

    G = ox.load_graphml(
        processed / "bike.graphml",
        edge_dtypes={"crashes": int, "exposure": float, "risk": float},
    )
    # Every edge carries the four risk attributes.
    for _, _, data in G.edges(data=True):
        assert "crashes" in data
        assert "exposure" in data
        assert "risk" in data
        assert "risk_basis" in data
        assert data["risk"] >= 0.0
