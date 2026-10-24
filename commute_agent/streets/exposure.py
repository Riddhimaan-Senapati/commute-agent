"""Exposure estimation for street edges.

Bluebikes trip routing proxy, least-squares calibration to counters, and a
road-class prior fallback. Pure functions plus one exception type.
"""

import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

PRIOR_MIN_EDGES = 20
MIN_MATCHED_COUNTERS = 10


class TooFewCounters(Exception):
    """Raised by the risk build when fewer than MIN_MATCHED_COUNTERS counters match."""


def bluebikes_proxy(
    G_bike: nx.MultiDiGraph,
    trips: pd.DataFrame,
    stations: pd.DataFrame,
) -> dict[tuple[int, int, int], int]:
    """Per-edge Bluebikes trip traversal count.

    For each start station run one single-source Dijkstra over ``length``, then
    for each end station walk the returned path and add the trip count to each
    traversed edge, choosing the min-length key between consecutive nodes.
    Station nodes come from ``ox.distance.nearest_nodes``. Trips whose end node
    is unreachable are skipped.
    """
    station_node = _station_nodes(G_bike, stations)
    counts: dict[tuple[int, int, int], int] = {}
    trips_by_start = trips.groupby("start_station_id")
    for start_id, group in trips_by_start:
        src = station_node.get(start_id)
        if src is None:
            continue
        paths = nx.single_source_dijkstra_path(G_bike, src, weight="length")
        for _, row in group.iterrows():
            dst = station_node.get(row["end_station_id"])
            if dst is None or dst not in paths:
                continue
            count = int(row["trips"])
            path = paths[dst]
            for u, v in zip(path, path[1:], strict=False):
                key = _min_length_key(G_bike, u, v)
                edge = (u, v, key)
                counts[edge] = counts.get(edge, 0) + count
    return counts


def fit_calibration(proxy: np.ndarray, observed: np.ndarray) -> tuple[float, float]:
    """Least squares through the origin.

    scale = sum(proxy * observed) / sum(proxy ** 2).
    r_squared = 1 - SS_res / SS_tot about the observed mean.
    """
    proxy = np.asarray(proxy, dtype=float)
    observed = np.asarray(observed, dtype=float)
    scale = float((proxy * observed).sum() / (proxy**2).sum())
    ss_res = float(((observed - scale * proxy) ** 2).sum())
    ss_tot = float(((observed - observed.mean()) ** 2).sum())
    r_squared = 1.0 - ss_res / ss_tot
    return scale, r_squared


def road_class_prior(
    highway: pd.Series,
    exposure: pd.Series,
    measured: pd.Series,
) -> pd.Series:
    """Prior exposure per edge from its highway class.

    For each edge, the median measured exposure of its highway class when that
    class has at least PRIOR_MIN_EDGES measured edges, else the median over all
    measured edges.
    """
    global_median = exposure[measured].median()
    class_median = exposure[measured].groupby(highway[measured]).median()
    class_count = highway[measured].value_counts()

    def pick(hw: object) -> float:
        if class_count.get(hw, 0) >= PRIOR_MIN_EDGES:
            return float(class_median[hw])
        return float(global_median)

    return highway.map(pick)


def _station_nodes(G_bike: nx.MultiDiGraph, stations: pd.DataFrame) -> dict[object, int]:
    import geopandas as gpd
    from shapely.geometry import Point

    # Project for the nearest-node search so OSMnx uses the scipy k-d tree and
    # needs no optional scikit-learn ball tree. Node ids survive projection.
    G_proj = ox.projection.project_graph(G_bike, to_crs="EPSG:26986")
    pts = gpd.GeoSeries(
        [Point(lon, lat) for lon, lat in zip(stations["lon"], stations["lat"], strict=True)],
        crs="EPSG:4326",
    ).to_crs("EPSG:26986")
    xs = np.array([pt.x for pt in pts])
    ys = np.array([pt.y for pt in pts])
    nodes = ox.distance.nearest_nodes(G_proj, xs, ys)
    return dict(zip(stations["station_id"], np.atleast_1d(nodes), strict=True))


def _min_length_key(G: nx.MultiDiGraph, u: int, v: int) -> int:
    edges = G[u][v]
    return min(edges, key=lambda k: edges[k]["length"])
