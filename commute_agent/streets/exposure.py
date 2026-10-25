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
# Half a mile, the usual U.S. walk catchment for a rail station (FTA Circular 4702.1B
# convention, cited in README "Data notes"). A planning convention, not fitted to counts.
STATION_CATCHMENT_M = 804.7
_STATION_CHUNK = 20_000


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


def fit_linear(X: np.ndarray, observed: np.ndarray) -> tuple[np.ndarray, float]:
    """Least squares through the origin with one or more predictor columns.

    With one column this is ``fit_calibration``. r_squared = 1 - SS_res / SS_tot
    about the observed mean, as in ``fit_calibration``.
    """
    X = np.asarray(X, dtype=float)
    observed = np.asarray(observed, dtype=float)
    coef, *_ = np.linalg.lstsq(X, observed, rcond=None)
    ss_res = float(((observed - X @ coef) ** 2).sum())
    ss_tot = float(((observed - observed.mean()) ** 2).sum())
    return coef, 1.0 - ss_res / ss_tot


def adjusted_r_squared(r_squared: float, n: int, n_coefficients: int) -> float:
    """R² penalised for the number of fitted coefficients: 1 - (1 - R²)(n - 1) / (n - p)."""
    return 1.0 - (1.0 - r_squared) * (n - 1) / (n - n_coefficients)


def station_entry_term(
    xs: np.ndarray,
    ys: np.ndarray,
    station_xy: np.ndarray,
    entries_per_day: np.ndarray,
    radius_m: float = STATION_CATCHMENT_M,
) -> np.ndarray:
    """Distance-decayed station entries near each point.

    For each point, sum over stations within ``radius_m`` of
    ``entries_per_day * (1 - distance / radius_m)``. Inputs are in projected metres:
    ``xs``, ``ys`` for points, ``station_xy`` as an (n, 2) array for stations.
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    station_xy = np.asarray(station_xy, dtype=float).reshape(-1, 2)
    entries = np.asarray(entries_per_day, dtype=float)
    term = np.zeros(len(xs))
    if len(station_xy) == 0:
        return term
    for start in range(0, len(xs), _STATION_CHUNK):
        stop = start + _STATION_CHUNK
        dx = xs[start:stop, None] - station_xy[None, :, 0]
        dy = ys[start:stop, None] - station_xy[None, :, 1]
        weight = np.clip(1.0 - np.hypot(dx, dy) / radius_m, 0.0, None)
        term[start:stop] = weight @ entries
    return term


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
