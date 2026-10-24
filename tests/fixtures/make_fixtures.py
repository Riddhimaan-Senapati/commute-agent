"""Build the committed test fixtures. Run once: uv run python tests/fixtures/make_fixtures.py.

Writes graph_small.graphml next to this file. It does not touch
otp_fields_corner.json: that file is a real OTP 2.10.0 planConnection response
for the fields-corner-to-downtown Showcase_Trip (Tuesday 08:15), copied from
data/cache/otp/ffc582e57e619f4e.json after cache_trips warmed the cache.
"""

import math
from pathlib import Path

import networkx as nx
import osmnx as ox

HERE = Path(__file__).resolve().parent
ORIGIN_LAT, ORIGIN_LON = 42.3550, -71.0650
STEP_DEG = 0.002
GRID = 3
CRASH_CONSTANT_P = 0.5  # crashes per km used for the fixture's shrinkage term
SHRINKAGE_K = 1.0
RISK_SCALE = 1000.0
EXPOSURE = 100.0
UNCOVERED_EDGE = (3, 4)  # the one edge with basis outside_crash_coverage
CRASHED_EDGES = {(0, 1): 2, (4, 5): 1}  # directed edge -> crash count


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def node_id(i: int, j: int) -> int:
    return i * GRID + j


def build_graph() -> nx.MultiDiGraph:
    G = nx.MultiDiGraph(crs="epsg:4326")
    for i in range(GRID):
        for j in range(GRID):
            G.add_node(node_id(i, j), y=ORIGIN_LAT + STEP_DEG * i, x=ORIGIN_LON + STEP_DEG * j)

    undirected = []
    for i in range(GRID):
        for j in range(GRID):
            if j + 1 < GRID:
                undirected.append((node_id(i, j), node_id(i, j + 1)))
            if i + 1 < GRID:
                undirected.append((node_id(i, j), node_id(i + 1, j)))
    assert len(undirected) == 12

    for u, v in undirected:
        for a, b in ((u, v), (v, u)):
            length = haversine_m(G.nodes[a]["y"], G.nodes[a]["x"], G.nodes[b]["y"], G.nodes[b]["x"])
            crashes = CRASHED_EDGES.get((a, b), 0)
            basis = "outside_crash_coverage" if (a, b) == UNCOVERED_EDGE else "road_class_prior"
            length_km = length / 1000
            risk = (crashes + SHRINKAGE_K * CRASH_CONSTANT_P * length_km) / EXPOSURE * RISK_SCALE
            if basis == "outside_crash_coverage":
                risk = SHRINKAGE_K * CRASH_CONSTANT_P * length_km / EXPOSURE * RISK_SCALE
            G.add_edge(
                a,
                b,
                length=length,
                highway="residential",
                crashes=crashes,
                exposure=EXPOSURE,
                risk_basis=basis,
                risk=risk,
                speed_kph=30.0,
                travel_time=length / (30.0 / 3.6),
            )
    return G


def main() -> None:
    ox.save_graphml(build_graph(), HERE / "graph_small.graphml")


if __name__ == "__main__":
    main()
