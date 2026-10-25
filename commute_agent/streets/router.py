"""Street router (WS3).

Walk, bike, bluebikes, and drive candidates over the processed street graphs. A
crash-risk aware lambda sweep yields a time-shortest path plus safer variants for
the active modes; drive uses OSMnx free-flow travel times. ``score_walk_coords``
scores an arbitrary walk polyline against the walk graph for OTP walk legs.

Nearest-node and nearest-edge searches run on a metric projection of each graph
(EPSG:26986) so OSMnx uses its scipy k-d tree and needs no scikit-learn ball
tree. Node ids survive projection.
"""

import json
import math
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import shapely
from shapely import STRtree
from shapely.geometry import Point as ShapelyPoint

from commute_agent import routes
from commute_agent.emissions import co2_source, leg_co2_g
from commute_agent.models import (
    DockCounts,
    EmissionFactor,
    Leg,
    LegMode,
    Point,
    RiskBasis,
    RouteKind,
    RouteOption,
)
from commute_agent.paths import PROCESSED
from commute_agent.traffic import TrafficBucket, TrafficFactors, free_flow_factors

WALK_KPH = 4.8
BIKE_KPH = 15.0
BLUEBIKES_KPH = 13.0
RISK_MULTIPLIERS = (0.5, 2.0, 8.0)
SPEED_SOURCE = {
    "walk": "Assumed walking speed 4.8 km/h",
    "bike": "Assumed cycling speed 15 km/h",
    "bluebikes": "Assumed Bluebikes speed 13 km/h plus walking 4.8 km/h to and from stations",
    "drive": "OSMnx free-flow speeds from OSM maxspeed, imputed by road class",
}
DRIVE_TIME_SOURCE = SPEED_SOURCE["drive"]

_KPH: dict[LegMode, float] = {"walk": WALK_KPH, "bike": BIKE_KPH, "bluebikes": BLUEBIKES_KPH}
_EPSG_MA = "EPSG:26986"
_DEFAULT_RISK_BASIS: RiskBasis = "road_class_prior"


@dataclass(frozen=True)
class StreetGraphs:
    walk: nx.MultiDiGraph
    bike: nx.MultiDiGraph
    drive: nx.MultiDiGraph
    stations: pd.DataFrame  # bluebikes_stations.parquet
    crash_source: str  # risk_meta.json crash_source_label


def load_street_graphs(processed: Path = PROCESSED) -> StreetGraphs:
    """Load the processed walk/bike/drive graphs, Bluebikes stations, and the crash
    source label from ``risk_meta.json``."""
    edge_dtypes = {
        "length": float,
        "risk": float,
        "exposure": float,
        "crashes": int,
        "speed_kph": float,
        "travel_time": float,
    }
    walk = ox.load_graphml(processed / "walk.graphml", edge_dtypes=edge_dtypes)
    bike = ox.load_graphml(processed / "bike.graphml", edge_dtypes=edge_dtypes)
    drive = ox.load_graphml(processed / "drive.graphml", edge_dtypes=edge_dtypes)
    stations = pd.read_parquet(processed / "bluebikes_stations.parquet")
    meta = json.loads((processed / "risk_meta.json").read_text())
    crash_source = meta["crash_source_label"]
    for G in (walk, bike, drive):
        _projected(G)  # pay the projection cost at load, not on the first plan
    _edge_index(walk)  # score_walk_coords queries it on every transit plan
    _edge_index(bike)  # score_bike_coords queries it on every bike-access transit plan
    return StreetGraphs(
        walk=walk, bike=bike, drive=drive, stations=stations, crash_source=crash_source
    )


def street_routes(
    graphs: StreetGraphs,
    factors: dict[LegMode, EmissionFactor],
    origin: Point,
    destination: Point,
    depart_at: datetime | None = None,
    traffic: TrafficFactors | None = None,
    availability: Mapping[str, DockCounts] | None = None,
) -> tuple[list[RouteOption], dict[RouteKind, str]]:
    """Candidates for walk, bike, bluebikes, drive, and omitted kinds with reasons.

    Only drive uses ``depart_at``: its free-flow time is multiplied by the traffic factor for
    that departure. Without ``depart_at`` the drive time stays free-flow. ``availability`` is
    live Bluebikes counts by station id. None means unknown, and the Bluebikes candidate then
    uses the nearest stations regardless of their counts."""
    options: list[RouteOption] = []
    omitted: dict[RouteKind, str] = {}

    walk_paths = _sweep(graphs.walk, "walk", origin, destination)
    if walk_paths is None:
        omitted["walk"] = "no path"
    else:
        for n, (node_path, _edges) in enumerate(walk_paths, start=1):
            summary = "Walk, fastest" if n == 1 else f"Walk, safer variant {n - 1}"
            options.append(
                _build_option(
                    graphs.walk,
                    "walk",
                    "walk",
                    f"walk-{n}",
                    summary,
                    node_path,
                    factors,
                    graphs.crash_source,
                )
            )

    bike_paths = _sweep(graphs.bike, "bike", origin, destination)
    if bike_paths is None:
        omitted["bike"] = "no path"
    else:
        for n, (node_path, _edges) in enumerate(bike_paths, start=1):
            summary = "Bike, fastest" if n == 1 else f"Bike, safer variant {n - 1}"
            options.append(
                _build_option(
                    graphs.bike,
                    "bike",
                    "bike",
                    f"bike-{n}",
                    summary,
                    node_path,
                    factors,
                    graphs.crash_source,
                )
            )

    bucket = None
    if depart_at is not None:
        bucket = (traffic or free_flow_factors()).factor_for(depart_at)
    drive_option, drive_reason = _drive_route(
        graphs.drive, origin, destination, factors, graphs.crash_source, bucket
    )
    if drive_option is not None:
        options.append(drive_option)
    else:
        omitted["drive"] = drive_reason

    bluebikes_option, bb_reason = _bluebikes_route(
        graphs, origin, destination, factors, availability
    )
    if bluebikes_option is not None:
        options.append(bluebikes_option)
    else:
        omitted["bluebikes"] = bb_reason

    return options, omitted


def score_walk_coords(
    G_walk: nx.MultiDiGraph, coords: list[tuple[float, float]]
) -> tuple[float, dict[RiskBasis, float]]:
    """Crash risk and metres per basis for an arbitrary walk polyline, used for OTP walk legs."""
    return _score_coords(G_walk, coords)


def score_bike_coords(
    G_bike: nx.MultiDiGraph, coords: list[tuple[float, float]]
) -> tuple[float, dict[RiskBasis, float]]:
    """Crash risk and metres per basis for an arbitrary bike polyline, used for OTP bike legs.

    Scored on the bike graph the same way as walk legs are scored on the walk graph.
    """
    return _score_coords(G_bike, coords)


def _score_coords(
    G: nx.MultiDiGraph, coords: list[tuple[float, float]]
) -> tuple[float, dict[RiskBasis, float]]:
    """For each consecutive coordinate pair, take the midpoint, find the nearest edge in one
    vectorized nearest_edges call, and add edge risk * min(1, segment_m / edge_length)."""
    risk_basis_m: dict[RiskBasis, float] = {}
    if len(coords) < 2:
        return 0.0, risk_basis_m

    seg_lon_mid: list[float] = []
    seg_lat_mid: list[float] = []
    seg_m: list[float] = []
    for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:], strict=False):
        seg_lon_mid.append((lon1 + lon2) / 2)
        seg_lat_mid.append((lat1 + lat2) / 2)
        seg_m.append(float(ox.distance.great_circle(lat1, lon1, lat2, lon2)))

    xs, ys = _project_points(seg_lon_mid, seg_lat_mid)
    edges = _nearest_edges(G, xs, ys)

    total_risk = 0.0
    for edge, segment_m in zip(edges, seg_m, strict=True):
        key = tuple(int(c) for c in edge)
        data = G.edges[key]
        edge_length = float(data["length"])
        frac = min(1.0, segment_m / edge_length) if edge_length > 0 else 0.0
        total_risk += float(data["risk"]) * frac
        basis = data.get("risk_basis", _DEFAULT_RISK_BASIS)
        risk_basis_m[basis] = risk_basis_m.get(basis, 0.0) + segment_m
    return total_risk, risk_basis_m


# --- internals ---------------------------------------------------------------


def _speed_mps(mode: LegMode) -> float:
    return _KPH[mode] * 1000 / 3600


# Each graph's EPSG:26986 projection, built on first use and reused. Graphs are never
# mutated after load, so the identity of the graph object is a sound cache key.
_PROJ_CACHE: "weakref.WeakKeyDictionary[nx.MultiDiGraph, nx.MultiDiGraph]" = (
    weakref.WeakKeyDictionary()
)


def _projected(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    G_proj = _PROJ_CACHE.get(G)
    if G_proj is None:
        G_proj = ox.projection.project_graph(G, to_crs=_EPSG_MA)
        _PROJ_CACHE[G] = G_proj
    return G_proj


# Each graph's projected edge R-tree plus the (u, v, key) id of every tree position.
# ox.distance.nearest_edges rebuilds both on every call, which costs over a second on
# the walk graph; this builds them once per graph, the same way as _PROJ_CACHE.
_EDGE_INDEX_CACHE: "weakref.WeakKeyDictionary[nx.MultiDiGraph, tuple[STRtree, np.ndarray]]" = (
    weakref.WeakKeyDictionary()
)


def _edge_index(G: nx.MultiDiGraph) -> tuple[STRtree, np.ndarray]:
    index = _EDGE_INDEX_CACHE.get(G)
    if index is None:
        geoms = ox.convert.graph_to_gdfs(_projected(G), nodes=False)["geometry"]
        index = (STRtree(geoms.to_numpy()), geoms.index.to_numpy())
        _EDGE_INDEX_CACHE[G] = index
    return index


def _nearest_edges(G: nx.MultiDiGraph, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Nearest (u, v, key) per projected point; same result as ox.distance.nearest_edges."""
    tree, edge_ids = _edge_index(G)
    pos = tree.query_nearest(shapely.points(xs, ys), all_matches=False)
    return edge_ids[pos[1]]


def _project_points(lons: list[float], lats: list[float]) -> tuple[np.ndarray, np.ndarray]:
    pts = gpd.GeoSeries(
        [ShapelyPoint(lon, lat) for lon, lat in zip(lons, lats, strict=True)],
        crs="EPSG:4326",
    ).to_crs(_EPSG_MA)
    xs = np.array([pt.x for pt in pts])
    ys = np.array([pt.y for pt in pts])
    return xs, ys


def _snap_nodes(G: nx.MultiDiGraph, origin: Point, destination: Point) -> tuple[int, int]:
    xs, ys = _project_points([origin.lon, destination.lon], [origin.lat, destination.lat])
    nodes = ox.distance.nearest_nodes(_projected(G), xs, ys)
    nodes = np.atleast_1d(nodes)
    return int(nodes[0]), int(nodes[1])


def _edge_len(data: dict) -> float:
    return float(data["length"])


def _edge_risk(data: dict) -> float:
    return float(data["risk"])


def _edge_weight(data: dict, speed_mps: float, lam: float) -> float:
    return _edge_len(data) / speed_mps + lam * _edge_risk(data)


def _min_key_weight(
    G: nx.MultiDiGraph, u: int, v: int, speed_mps: float, lam: float
) -> tuple[int, float]:
    best_key = None
    best_w = math.inf
    for key, data in G[u][v].items():
        w = _edge_weight(data, speed_mps, lam)
        if w < best_w:
            best_w = w
            best_key = key
    return best_key, best_w


def _path_to_edges(
    G: nx.MultiDiGraph, node_path: list[int], speed_mps: float, lam: float
) -> list[tuple[int, int, int]]:
    edges: list[tuple[int, int, int]] = []
    for u, v in zip(node_path, node_path[1:], strict=False):
        key, _ = _min_key_weight(G, u, v, speed_mps, lam)
        edges.append((u, v, key))
    return edges


def _path_time_risk(
    G: nx.MultiDiGraph, edges: list[tuple[int, int, int]], speed_mps: float
) -> tuple[float, float]:
    time_s = sum(_edge_len(G.edges[e]) / speed_mps for e in edges)
    risk = sum(_edge_risk(G.edges[e]) for e in edges)
    return time_s, risk


def _sweep(
    G: nx.MultiDiGraph, mode: LegMode, origin: Point, destination: Point
) -> list[tuple[list[int], list[tuple[int, int, int]]]] | None:
    """Run the lambda sweep. Return a list of (node_path, edge_list) deduplicated by
    edge sequence, time-shortest first, or None when no path exists."""
    speed_mps = _speed_mps(mode)
    s, t = _snap_nodes(G, origin, destination)

    def length_speed_weight(u: int, v: int, parallel: dict) -> float:
        return min(_edge_len(e) / speed_mps for e in parallel.values())

    try:
        fastest_nodes = nx.shortest_path(G, s, t, weight=length_speed_weight)
    except nx.NetworkXNoPath:
        return None
    except nx.NodeNotFound:
        return None

    fastest_edges = _path_to_edges(G, fastest_nodes, speed_mps, 0.0)
    t0, r0 = _path_time_risk(G, fastest_edges, speed_mps)

    results: list[tuple[list[int], list[tuple[int, int, int]]]] = [(fastest_nodes, fastest_edges)]
    seen_edge_lists: list[list[tuple[int, int, int]]] = [fastest_edges]

    if r0 > 0:
        for m in RISK_MULTIPLIERS:
            lam = m * t0 / r0

            def weight(u: int, v: int, parallel: dict, lam: float = lam) -> float:
                return min(_edge_weight(e, speed_mps, lam) for e in parallel.values())

            try:
                node_path = nx.shortest_path(G, s, t, weight=weight)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            edge_list = _path_to_edges(G, node_path, speed_mps, lam)
            if edge_list in seen_edge_lists:
                continue
            seen_edge_lists.append(edge_list)
            results.append((node_path, edge_list))

    return results


def _edge_coords(
    G: nx.MultiDiGraph, edges: list[tuple[int, int, int]]
) -> list[tuple[float, float]]:
    """(lon, lat) polyline from edge geometries when present, else node x/y."""
    coords: list[tuple[float, float]] = []
    for u, v, key in edges:
        data = G.edges[(u, v, key)]
        geom = data.get("geometry")
        if geom is not None:
            pts = [(float(x), float(y)) for x, y in geom.coords]
        else:
            pts = [
                (float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
                (float(G.nodes[v]["x"]), float(G.nodes[v]["y"])),
            ]
        if coords and pts and coords[-1] == pts[0]:
            coords.extend(pts[1:])
        else:
            coords.extend(pts)
    return coords


def _build_leg(
    G: nx.MultiDiGraph,
    leg_mode: LegMode,
    edges: list[tuple[int, int, int]],
    factors: dict[LegMode, EmissionFactor],
) -> Leg:
    speed_mps = _speed_mps(leg_mode)
    distance_m = sum(_edge_len(G.edges[e]) for e in edges)
    duration_min = (distance_m / speed_mps) / 60 if speed_mps > 0 else 0.0
    crash_risk = sum(_edge_risk(G.edges[e]) for e in edges)
    risk_basis_m: dict[RiskBasis, float] = {}
    for e in edges:
        data = G.edges[e]
        basis = data.get("risk_basis", _DEFAULT_RISK_BASIS)
        risk_basis_m[basis] = risk_basis_m.get(basis, 0.0) + _edge_len(data)
    return Leg(
        mode=leg_mode,
        coords=_edge_coords(G, edges),
        distance_m=distance_m,
        duration_min=duration_min,
        co2_g=leg_co2_g(factors, leg_mode, distance_m),
        crash_risk=crash_risk,
        risk_basis_m=risk_basis_m,
    )


def _build_option(
    G: nx.MultiDiGraph,
    kind: RouteKind,
    leg_mode: LegMode,
    route_id: str,
    summary: str,
    node_path: list[int],
    factors: dict[LegMode, EmissionFactor],
    crash_source: str,
) -> RouteOption:
    speed_mps = _speed_mps(leg_mode)
    edges = _path_to_edges(G, node_path, speed_mps, 0.0)
    leg = _build_leg(G, leg_mode, edges, factors)
    return routes.assemble_route(
        route_id=route_id,
        kind=kind,
        summary=summary,
        legs=[leg],
        time_source=SPEED_SOURCE[kind],
        co2_source=co2_source(factors, [leg_mode]),
        crash_source=crash_source,
    )


def _drive_time_source(bucket: TrafficBucket | None) -> str:
    """Drive time source: free-flow, or the bucket, factor, and source of the multiplier."""
    if bucket is None:
        return f"{SPEED_SOURCE['drive']}; no departure time, so no traffic factor applied"
    return (
        f"{SPEED_SOURCE['drive']}; times multiplied by traffic factor {bucket.factor.value:g} "
        f"for {bucket.day_type} {bucket.label} ({bucket.factor.source})"
    )


def _drive_route(
    G: nx.MultiDiGraph,
    origin: Point,
    destination: Point,
    factors: dict[LegMode, EmissionFactor],
    crash_source: str,
    bucket: TrafficBucket | None = None,
) -> tuple[RouteOption | None, str]:
    s, t = _snap_nodes(G, origin, destination)
    node_path = ox.routing.shortest_path(G, s, t, weight="travel_time")
    if node_path is None or len(node_path) < 2:
        return None, "no path"
    edges = _drive_edges(G, node_path)
    distance_m = sum(_edge_len(G.edges[e]) for e in edges)
    multiplier = bucket.factor.value if bucket is not None else 1.0
    duration_min = sum(float(G.edges[e]["travel_time"]) for e in edges) / 60 * multiplier
    leg = Leg(
        mode="drive",
        coords=_edge_coords(G, edges),
        distance_m=distance_m,
        duration_min=duration_min,
        co2_g=leg_co2_g(factors, "drive", distance_m),
        crash_risk=0.0,
        risk_basis_m={},
    )
    option = routes.assemble_route(
        route_id="drive-1",
        kind="drive",
        summary="Drive",
        legs=[leg],
        time_source=_drive_time_source(bucket),
        co2_source=co2_source(factors, ["drive"]),
        crash_source=crash_source,
    )
    return option, ""


def _drive_edges(G: nx.MultiDiGraph, node_path: list[int]) -> list[tuple[int, int, int]]:
    edges: list[tuple[int, int, int]] = []
    for u, v in zip(node_path, node_path[1:], strict=False):
        key = min(G[u][v], key=lambda k: float(G[u][v][k]["travel_time"]))
        edges.append((u, v, key))
    return edges


def _nearest_station(
    stations: pd.DataFrame, point: Point, allowed: np.ndarray | None
) -> int | None:
    """Index of the station nearest to ``point`` by straight-line distance, limited to
    ``allowed`` rows when given. None when no row is allowed."""
    dlat = stations["lat"].to_numpy(dtype=float) - point.lat
    dlon = stations["lon"].to_numpy(dtype=float) - point.lon
    d2 = dlat**2 + dlon**2
    if allowed is not None:
        if not allowed.any():
            return None
        d2 = np.where(allowed, d2, np.inf)
    return int(np.argmin(d2))


def _allowed_stations(
    stations: pd.DataFrame, availability: Mapping[str, DockCounts] | None, start: bool
) -> np.ndarray | None:
    """Rows usable as a start (bikes out, renting) or end (docks free, returning).
    None when availability is unknown. A station missing from the feed is not usable."""
    if availability is None:
        return None
    allowed = []
    for station_id in stations["station_id"].astype(str):
        counts = availability.get(station_id)
        if counts is None:
            allowed.append(False)
        elif start:
            allowed.append(counts.bikes > 0 and counts.renting)
        else:
            allowed.append(counts.docks > 0 and counts.returning)
    return np.array(allowed, dtype=bool)


def _bluebikes_route(
    graphs: StreetGraphs,
    origin: Point,
    destination: Point,
    factors: dict[LegMode, EmissionFactor],
    availability: Mapping[str, DockCounts] | None = None,
) -> tuple[RouteOption | None, str]:
    stations = graphs.stations
    if stations is None or len(stations) == 0:
        return None, "no path"

    start_idx = _nearest_station(
        stations, origin, _allowed_stations(stations, availability, start=True)
    )
    if start_idx is None:
        return None, "no station with bikes available"
    end_idx = _nearest_station(
        stations, destination, _allowed_stations(stations, availability, start=False)
    )
    if end_idx is None:
        return None, "no station with docks available"
    start = stations.iloc[start_idx]
    end = stations.iloc[end_idx]
    if start_idx == end_idx:
        return None, "same station at both ends"

    start_pt = Point(lat=float(start["lat"]), lon=float(start["lon"]))
    end_pt = Point(lat=float(end["lat"]), lon=float(end["lon"]))

    # Walk to the start station using the fastest walk path.
    walk_in = _sweep(graphs.walk, "walk", origin, start_pt)
    bike_sweep = _sweep(graphs.bike, "bluebikes", start_pt, end_pt)
    walk_out = _sweep(graphs.walk, "walk", end_pt, destination)
    if walk_in is None or bike_sweep is None or walk_out is None:
        return None, "no path"

    walk_in_edges = _path_to_edges(graphs.walk, walk_in[0][0], _speed_mps("walk"), 0.0)
    bike_edges = _path_to_edges(graphs.bike, bike_sweep[0][0], _speed_mps("bluebikes"), 0.0)
    walk_out_edges = _path_to_edges(graphs.walk, walk_out[0][0], _speed_mps("walk"), 0.0)

    legs = [
        _build_leg(graphs.walk, "walk", walk_in_edges, factors),
        _build_leg(graphs.bike, "bluebikes", bike_edges, factors),
        _build_leg(graphs.walk, "walk", walk_out_edges, factors),
    ]
    summary = f"Bluebikes from {start['name']} to {end['name']}"
    time_source = SPEED_SOURCE["bluebikes"]
    if availability is not None:
        start_counts = availability[str(start["station_id"])]
        end_counts = availability[str(end["station_id"])]
        time_source += (
            f"; live GBFS at start {start_counts.bikes} bikes, at end {end_counts.docks} docks"
        )
    option = routes.assemble_route(
        route_id="bluebikes-1",
        kind="bluebikes",
        summary=summary,
        legs=legs,
        time_source=time_source,
        co2_source=co2_source(factors, ["walk", "bluebikes"]),
        crash_source=graphs.crash_source,
    )
    return option, ""


__all__ = [
    "WALK_KPH",
    "BIKE_KPH",
    "BLUEBIKES_KPH",
    "RISK_MULTIPLIERS",
    "SPEED_SOURCE",
    "StreetGraphs",
    "load_street_graphs",
    "street_routes",
    "score_walk_coords",
    "score_bike_coords",
]
