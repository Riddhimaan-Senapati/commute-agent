"""Street-graph risk build: I/O and orchestration for WS2.

Reads the walk and bike GraphML plus processed crash, count, and Bluebikes data,
snaps crashes and counters onto the projected graph, estimates exposure, computes
crash risk, writes the attributes back onto the unprojected graph, and emits
``risk_meta.json`` and ``exposure_calibration.json``.
"""

import json
import sys
from pathlib import Path
from typing import Literal

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely.geometry import Point

from commute_agent.paths import PROCESSED

from .exposure import (
    MIN_MATCHED_COUNTERS,
    STATION_CATCHMENT_M,
    TooFewCounters,
    adjusted_r_squared,
    bluebikes_proxy,
    fit_calibration,
    fit_linear,
    road_class_prior,
    station_entry_term,
)
from .risk import RISK_SCALE, SHRINKAGE_K, SNAP_MAX_M, edge_risk, mean_crashes_per_km

CRASH_WINDOW = {
    "boston_vision_zero": ["2015-01-01", "2026-09-01"],
    "cambridge_police": ["2015-01-01", "2026-09-30"],
    "somerville_police": ["2018-05-01", "2026-09-30"],
}
CRASH_SOURCE_LABEL = (
    "Estimated crash risk index for walk and bike segments. Crashes from "
    "Boston Vision Zero (2015-01-01 to 2026-09-01), Cambridge police "
    "(2015-01-01 to 2026-09-30) and Somerville police (2018-05-01 to 2026-09-30) "
    "per estimated peak-hour volume. Brookline has no crash source, so its segments "
    "use the road-class prior and are marked outside_crash_coverage. Drive and "
    "transit vehicle segments are not modeled."
)
EPSG_MA = "EPSG:26986"
_CRASH_MODE = {"bike": "bike", "walk": "ped"}
_COUNTER_MODE = {"bike": "bike", "walk": "ped"}
_CALIBRATION_SOURCES = ("cambridge_citywide", "somerville", "numina")
# Walk exposure uses the station-entries term only when this is True. Set from the
# calibration decision in exposure_calibration.json ("walk"); False keeps the prior alone.
USE_STATION_ENTRIES = False
Edge = tuple[int, int, int]


def snap_points(
    G_projected: nx.MultiDiGraph, xs: np.ndarray, ys: np.ndarray
) -> tuple[list[tuple[int, int, int]], np.ndarray]:
    """Nearest projected edge and snap distance for each point."""
    edges, dists = ox.distance.nearest_edges(G_projected, xs, ys, return_dist=True)
    return [tuple(e) for e in edges], np.asarray(dists, dtype=float)


def _to_projected_xy(lons: pd.Series, lats: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    pts = gpd.GeoSeries(
        [Point(lon, lat) for lon, lat in zip(lons, lats, strict=True)], crs="EPSG:4326"
    ).to_crs(EPSG_MA)
    return np.array([p.x for p in pts]), np.array([p.y for p in pts])


def _reverse_edge(G: nx.MultiDiGraph, edge: Edge) -> Edge | None:
    """The reverse edge (v, u, k') sharing the same osmid, if it exists."""
    u, v, _ = edge
    if not G.has_edge(v, u):
        return None
    osmid = G.edges[edge].get("osmid")
    for rk, data in G[v][u].items():
        if osmid is None or data.get("osmid") == osmid:
            return (v, u, rk)
    return None


def _snap_crashes(
    G: nx.MultiDiGraph, G_proj: nx.MultiDiGraph, crashes: pd.DataFrame
) -> tuple[dict[Edge, int], int, int]:
    """Count crashes per edge, sharing each to its reverse edge; count snaps and discards."""
    counts: dict[Edge, int] = {}
    if len(crashes) == 0:
        return counts, 0, 0
    xs, ys = _to_projected_xy(crashes["lon"], crashes["lat"])
    edges, dists = snap_points(G_proj, xs, ys)
    snapped = discarded = 0
    for edge, dist in zip(edges, dists, strict=True):
        if dist > SNAP_MAX_M:
            discarded += 1
            continue
        snapped += 1
        counts[edge] = counts.get(edge, 0) + 1
        reverse = _reverse_edge(G, edge)
        if reverse is not None:
            counts[reverse] = counts.get(reverse, 0) + 1
    return counts, snapped, discarded


def _snap_counters(G_proj: nx.MultiDiGraph, counters: pd.DataFrame) -> list[Edge | None]:
    """Nearest edge for each counter location, or None when it is more than SNAP_MAX_M away."""
    if len(counters) == 0:
        return []
    xs, ys = _to_projected_xy(counters["lon"], counters["lat"])
    edges, dists = snap_points(G_proj, xs, ys)
    return [e if d <= SNAP_MAX_M else None for e, d in zip(edges, dists, strict=True)]


def _edge_covered(G: nx.MultiDiGraph, edge_ids: list[Edge], coverage: gpd.GeoDataFrame | None):
    if coverage is None:
        return np.ones(len(edge_ids), dtype=bool)
    union = coverage.union_all()
    covered = []
    for u, v, _ in edge_ids:
        mx = (G.nodes[u]["x"] + G.nodes[v]["x"]) / 2
        my = (G.nodes[u]["y"] + G.nodes[v]["y"]) / 2
        covered.append(union.contains(Point(mx, my)))
    return np.array(covered, dtype=bool)


def _load_counters(processed: Path, mode: str) -> pd.DataFrame:
    counts = pd.read_parquet(processed / "counts.parquet")
    return counts[counts["mode"] == _COUNTER_MODE[mode]].reset_index(drop=True)


def _bike_exposure(
    G: nx.MultiDiGraph, G_proj: nx.MultiDiGraph, edge_ids: list[Edge], processed: Path
) -> tuple[np.ndarray, list[str], dict]:
    """Bluebikes proxy scaled to counts, with road-class priors elsewhere."""
    trips = pd.read_parquet(processed / "bluebikes_trips.parquet")
    stations = pd.read_parquet(processed / "bluebikes_stations.parquet")
    proxy_map = bluebikes_proxy(G, trips, stations)
    proxy = np.array([proxy_map.get(e, 0) for e in edge_ids], dtype=float)
    index = {e: i for i, e in enumerate(edge_ids)}

    counters = _load_counters(processed, "bike")
    matched = _snap_counters(G_proj, counters)
    used_proxy, observed, used_sources = [], [], []
    unmatched = 0
    matched_per_source = dict.fromkeys(_CALIBRATION_SOURCES, 0)
    for edge, row in zip(matched, counters.itertuples(index=False), strict=True):
        if edge is None:
            unmatched += 1
            continue
        i = index.get(edge)
        if i is None or proxy[i] <= 0:
            continue
        used_proxy.append(proxy[i])
        observed.append(float(row.peak_hour_volume))
        used_sources.append(row.source)
        matched_per_source[row.source] = matched_per_source.get(row.source, 0) + 1

    calibration_n = len(used_proxy)
    if calibration_n < MIN_MATCHED_COUNTERS:
        raise TooFewCounters(
            f"Only {calibration_n} bike counters matched a Bluebikes-used edge "
            f"(need {MIN_MATCHED_COUNTERS}). Matched per source: {matched_per_source}."
        )
    scale, r_squared = fit_calibration(np.array(used_proxy), np.array(observed))

    scaled = proxy * scale
    measured = pd.Series(proxy > 0)
    highway = pd.Series([_first(G.edges[e].get("highway")) for e in edge_ids])
    prior = road_class_prior(highway, pd.Series(scaled), measured).to_numpy(dtype=float)
    exposure = np.where(proxy > 0, scaled, prior)
    bases = ["bluebikes_proxy" if p > 0 else "road_class_prior" for p in proxy]
    calibration = {
        "scale": scale,
        "r_squared": r_squared,
        "matched": matched_per_source,
        "unmatched_over_30m": unmatched,
    }
    return exposure, bases, calibration


def _walk_exposure(
    G: nx.MultiDiGraph,
    G_proj: nx.MultiDiGraph,
    edge_ids: list[Edge],
    processed: Path,
    use_station_entries: bool = USE_STATION_ENTRIES,
) -> tuple[np.ndarray, list[str], dict]:
    """Pedestrian counter volumes where counted, road-class priors elsewhere.

    The calibration compares two predictors of the matched counter volumes: the
    road-class prior (leave-one-out, so a counter does not predict itself) alone,
    and that prior plus distance-decayed station entries. With ``use_station_entries``
    the unmeasured edges use the two-term fit instead of the prior alone.
    """
    index = {e: i for i, e in enumerate(edge_ids)}
    counters = _load_counters(processed, "walk")
    volume = np.zeros(len(edge_ids))
    seen = np.zeros(len(edge_ids), dtype=int)
    for edge, row in zip(
        _snap_counters(G_proj, counters), counters.itertuples(index=False), strict=True
    ):
        i = index.get(edge) if edge is not None else None
        if i is None:
            continue
        volume[i] += float(row.peak_hour_volume)
        seen[i] += 1
    measured_mask = seen > 0
    volume = np.where(measured_mask, volume / np.maximum(seen, 1), 0.0)
    highway = pd.Series([_first(G.edges[e].get("highway")) for e in edge_ids])
    prior = road_class_prior(highway, pd.Series(volume), pd.Series(measured_mask)).to_numpy(
        dtype=float
    )

    station_term = _station_term(G_proj, edge_ids, processed)
    calibration, coef = _walk_calibration(highway, volume, measured_mask, station_term)
    if use_station_entries and station_term is not None and coef is not None:
        prior_with_stations = coef[0] * prior + coef[1] * station_term
        exposure = np.where(measured_mask, volume, prior_with_stations)
        calibration["station_entries_used"] = True
    else:
        exposure = np.where(measured_mask, volume, prior)
        calibration["station_entries_used"] = False
    bases = ["ped_counter" if m else "road_class_prior" for m in measured_mask]
    return exposure, bases, calibration


def _station_term(
    G_proj: nx.MultiDiGraph, edge_ids: list[Edge], processed: Path
) -> np.ndarray | None:
    """Distance-decayed station entries at each edge midpoint, or None when not built."""
    path = processed / "station_entries.parquet"
    if not path.exists():
        return None
    stations = pd.read_parquet(path)
    xs, ys = _to_projected_xy(stations["lon"], stations["lat"])
    mid_x = np.array([(G_proj.nodes[u]["x"] + G_proj.nodes[v]["x"]) / 2 for u, v, _ in edge_ids])
    mid_y = np.array([(G_proj.nodes[u]["y"] + G_proj.nodes[v]["y"]) / 2 for u, v, _ in edge_ids])
    return station_entry_term(
        mid_x, mid_y, np.column_stack([xs, ys]), stations["entries_per_day"].to_numpy()
    )


def _walk_calibration(
    highway: pd.Series,
    volume: np.ndarray,
    measured_mask: np.ndarray,
    station_term: np.ndarray | None,
) -> tuple[dict, np.ndarray | None]:
    """Fit the walk calibration on matched ped counters and report both R² values.

    Rows are the edges with a matched counter (per-edge mean volume, as in
    ``_walk_exposure``). Returns the report for ``exposure_calibration.json`` and the
    two-term coefficients for the build (None when the fit cannot run).
    """
    rows = np.flatnonzero(measured_mask)
    n = len(rows)
    report: dict = {
        "matched_edges": n,
        "station_catchment_m": STATION_CATCHMENT_M,
        "station_entries_available": station_term is not None,
    }
    if n < MIN_MATCHED_COUNTERS or station_term is None:
        report.update(
            r_squared_before=None,
            r_squared_after=None,
            adopted=False,
            decision=(
                f"Not fitted: {n} walk edges have a ped counter (need {MIN_MATCHED_COUNTERS})"
                if n < MIN_MATCHED_COUNTERS
                else "Not fitted: station_entries.parquet is not built"
            ),
        )
        return report, None

    observed = volume[rows]
    hw_rows = highway.iloc[rows].reset_index(drop=True)
    vol_rows = pd.Series(observed)
    # Leave-one-out road-class prior: each counter predicted without its own volume.
    prior_loo = np.empty(n)
    for k in range(n):
        keep = np.ones(n, dtype=bool)
        keep[k] = False
        prior_loo[k] = road_class_prior(hw_rows, vol_rows, pd.Series(keep)).iloc[k]
    station_rows = station_term[rows]

    _, r2_before = fit_linear(prior_loo[:, None], observed)
    coef, r2_after = fit_linear(np.column_stack([prior_loo, station_rows]), observed)
    # A nested fit never lowers in-sample R², so the decision uses adjusted R² (one
    # penalty per coefficient) and requires both coefficients to be non-negative.
    adj_before = adjusted_r_squared(r2_before, n, 1)
    adj_after = adjusted_r_squared(r2_after, n, 2)
    adopted = bool(adj_after > adj_before and coef[0] >= 0 and coef[1] >= 0)
    report.update(
        r_squared_before=r2_before,
        r_squared_after=r2_after,
        adjusted_r_squared_before=adj_before,
        adjusted_r_squared_after=adj_after,
        coefficients={
            "road_class_prior": float(coef[0]),
            "station_entries_per_day": float(coef[1]),
        },
        adopted=adopted,
        decision=(
            "Adopted: adjusted R² rises and both coefficients are non-negative."
            if adopted
            else "Not adopted: adjusted R² does not rise with station entries."
        ),
    )
    return report, coef


def _stored_exposure(G: nx.MultiDiGraph, edge_ids: list[Edge]) -> tuple[np.ndarray, list[str]]:
    """Exposure already stored on the graph, used when no count data has been built."""
    exposure = np.array([G.edges[e]["exposure"] for e in edge_ids], dtype=float)
    bases = [G.edges[e].get("risk_basis", "road_class_prior") for e in edge_ids]
    return exposure, bases


def build_risk(
    mode: Literal["walk", "bike"],
    processed: Path = PROCESSED,
    use_station_entries: bool = USE_STATION_ENTRIES,
) -> dict:
    """Snap crashes and counters onto ``<mode>.graphml`` and write risk attributes.

    Returns the ``risk_meta.json`` fragment for this mode. The bike and walk fragments
    carry ``calibration`` for ``exposure_calibration.json``.
    """
    crash_mode = _CRASH_MODE[mode]
    G = ox.load_graphml(
        processed / f"{mode}.graphml",
        edge_dtypes={"crashes": int, "exposure": float, "risk": float},
    )
    G_proj = ox.projection.project_graph(G, to_crs=EPSG_MA)
    coverage = _load_coverage(processed)

    crashes = _load_crashes(processed, crash_mode)
    crash_counts, snapped, discarded = _snap_crashes(G, G_proj, crashes)

    edge_ids = list(G.edges(keys=True))
    lengths = np.array([G.edges[e]["length"] for e in edge_ids], dtype=float)
    crash_arr = np.array([crash_counts.get(e, 0) for e in edge_ids], dtype=float)
    covered = _edge_covered(G, edge_ids, coverage)

    calibration = None
    if (processed / "counts.parquet").exists():
        if mode == "bike":
            exposure, bases, calibration = _bike_exposure(G, G_proj, edge_ids, processed)
        else:
            exposure, bases, calibration = _walk_exposure(
                G, G_proj, edge_ids, processed, use_station_entries
            )
    else:
        exposure, bases = _stored_exposure(G, edge_ids)
    bases = [
        "outside_crash_coverage" if not cov else basis
        for basis, cov in zip(bases, covered, strict=True)
    ]

    p = mean_crashes_per_km(crash_arr, lengths)
    risk = edge_risk(crash_arr, lengths, exposure, covered, p)

    for e, c, ex, b, r in zip(edge_ids, crash_arr, exposure, bases, risk, strict=True):
        G.edges[e]["crashes"] = int(c)
        G.edges[e]["exposure"] = float(ex)
        G.edges[e]["risk_basis"] = b
        G.edges[e]["risk"] = float(r)
    ox.save_graphml(G, processed / f"{mode}.graphml")

    fragment = {
        "crashes_snapped": {crash_mode: snapped},
        "crashes_discarded_over_30m": {crash_mode: discarded},
        "mean_crashes_per_km": {crash_mode: p},
    }
    if calibration is not None:
        fragment["calibration"] = calibration
    return fragment


def _load_coverage(processed: Path) -> gpd.GeoDataFrame | None:
    path = processed / "crash_coverage.geojson"
    if not path.exists():
        return None
    return gpd.read_file(path).to_crs("EPSG:4326")


def _load_crashes(processed: Path, crash_mode: str) -> pd.DataFrame:
    df = pd.read_parquet(processed / "crashes.parquet")
    return df[df["mode"] == crash_mode].reset_index(drop=True)


def _first(value: object) -> object:
    if isinstance(value, list):
        return value[0]
    return value


def main(processed: Path = PROCESSED) -> int:
    """Build bike then walk risk, then write risk_meta.json and exposure_calibration.json."""
    try:
        bike = build_risk("bike", processed)
        walk = build_risk("walk", processed)
    except TooFewCounters as error:
        print(f"risk build: {error}", file=sys.stderr)
        return 2

    risk_meta = {
        "crash_window": CRASH_WINDOW,
        "crashes_snapped": {**bike["crashes_snapped"], **walk["crashes_snapped"]},
        "crashes_discarded_over_30m": {
            **bike["crashes_discarded_over_30m"],
            **walk["crashes_discarded_over_30m"],
        },
        "mean_crashes_per_km": {**bike["mean_crashes_per_km"], **walk["mean_crashes_per_km"]},
        "shrinkage_k": SHRINKAGE_K,
        "risk_scale": RISK_SCALE,
        "crash_source_label": CRASH_SOURCE_LABEL,
    }
    (processed / "risk_meta.json").write_text(json.dumps(risk_meta, indent=2) + "\n")
    if "calibration" in bike:
        calibration = {**bike["calibration"]}
        if "calibration" in walk:
            calibration["walk"] = walk["calibration"]
        (processed / "exposure_calibration.json").write_text(
            json.dumps(calibration, indent=2) + "\n"
        )
    return 0


__all__ = [
    "TooFewCounters",
    "MIN_MATCHED_COUNTERS",
    "bluebikes_proxy",
    "fit_calibration",
    "snap_points",
    "build_risk",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
