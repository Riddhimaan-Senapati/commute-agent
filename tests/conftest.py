import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import strategies as st
from shapely.geometry import Polygon

from commute_agent import planner
from commute_agent.emissions import load_emission_factors
from commute_agent.mcp_server import SessionState, configure, load_trips
from commute_agent.models import Leg, Objectives, Place, Point, RouteKind, RouteOption
from commute_agent.planner import PlannerContext
from commute_agent.streets import load_street_graphs

FIXTURES = Path(__file__).resolve().parent / "fixtures"

FIXTURE_FILES = (
    "graph_small.graphml",
    "otp_fields_corner.json",
    "mbta_alerts.json",
    "alert_snapshot.json",
    "plan_result.json",
)


def make_route(
    id: str,
    kind: RouteKind,
    time_min: float,
    co2_g: float,
    crash_risk: float,
    disruption_risk: float,
    n_legs: int = 1,
) -> RouteOption:
    leg = Leg(mode="walk", distance_m=1.0, duration_min=time_min, co2_g=co2_g)
    return RouteOption(
        id=id,
        kind=kind,
        summary=id,
        legs=[leg.model_copy() for _ in range(n_legs)],
        objectives=Objectives(
            time_min=time_min,
            co2_g=co2_g,
            crash_risk=crash_risk,
            disruption_risk=disruption_risk,
        ),
        sources={
            "time_min": "test",
            "co2_g": "test",
            "crash_risk": "test",
            "disruption_risk": "test",
        },
    )


def objectives_strategy() -> st.SearchStrategy[Objectives]:
    unit = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
    return st.builds(
        Objectives,
        time_min=unit,
        co2_g=st.floats(min_value=0.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
        crash_risk=unit,
        disruption_risk=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )


@pytest.fixture
def fixture_paths(tmp_path: Path) -> Path:
    """Copy the committed fixtures into tmp_path and return that directory."""
    for name in FIXTURE_FILES:
        shutil.copy(FIXTURES / name, tmp_path / name)
    return tmp_path


@pytest.fixture
def fixture_graph(fixture_paths: Path):
    import osmnx as ox

    return ox.load_graphml(fixture_paths / "graph_small.graphml")


FIXTURE_FACTORS = """\
walk: {value: 0.0, unit: g_per_passenger_km, source: fixture}
bike: {value: 0.0, unit: g_per_passenger_km, source: fixture}
bluebikes: {value: 0.0, unit: g_per_passenger_km, source: fixture}
drive: {value: 200.0, unit: g_per_passenger_km, source: fixture}
subway: {value: 50.0, unit: g_per_passenger_km, source: fixture}
light_rail: {value: 50.0, unit: g_per_passenger_km, source: fixture}
bus: {value: 80.0, unit: g_per_passenger_km, source: fixture}
commuter_rail: {value: 50.0, unit: g_per_passenger_km, source: fixture}
ferry: {value: 50.0, unit: g_per_passenger_km, source: fixture}
"""


@pytest.fixture
def planner_ctx(fixture_paths: Path, tmp_path: Path) -> PlannerContext:
    """Offline planner context on the fixture graph; processed files are built in tmp_path."""
    processed = tmp_path / "processed"
    processed.mkdir()
    graph = fixture_paths / "graph_small.graphml"
    for mode in ("walk", "bike", "drive"):
        shutil.copy(graph, processed / f"{mode}.graphml")
    pd.DataFrame(
        {
            "station_id": ["S1", "S2"],
            "name": ["Station one", "Station two"],
            "lat": [42.3550, 42.3580],
            "lon": [-71.0650, -71.0620],
        }
    ).to_parquet(processed / "bluebikes_stations.parquet")
    (processed / "risk_meta.json").write_text(
        json.dumps({"crash_source_label": "fixture crash index"})
    )
    box = Polygon([(-71.07, 42.35), (-71.05, 42.35), (-71.05, 42.37), (-71.07, 42.37)])
    factors_path = tmp_path / "emission_factors.yaml"
    factors_path.write_text(FIXTURE_FACTORS)
    return PlannerContext(
        graphs=load_street_graphs(processed),
        factors=load_emission_factors(factors_path),
        boundary=box,
        live=False,
        preferences_db=tmp_path / "preferences.db",
    )


@pytest.fixture
def otp_cache(fixture_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the fixture OTP response for every offline transit lookup."""
    raw = json.loads((fixture_paths / "otp_fields_corner.json").read_text())

    def cached(
        origin,
        destination,
        depart_at,
        *,
        live,
        cache_dir=None,
        exclude_routes=(),
        access="walk",
        step_free=False,
    ):
        if exclude_routes or access == "bike":
            return (None, "transit not cached for this trip")
        return (raw, None)

    monkeypatch.setattr(planner, "fetch_itineraries", cached)


@pytest.fixture
def server(planner_ctx: PlannerContext, otp_cache: None) -> SessionState:
    """Configure the MCP server module with fixture state for one test."""
    gazetteer = [
        Place(name="Park Street", point=Point(lat=42.3564, lon=-71.0624), source="mbta_station")
    ]
    state = SessionState(ctx=planner_ctx, trips=load_trips(), gazetteer=gazetteer)
    configure(state)
    return state
