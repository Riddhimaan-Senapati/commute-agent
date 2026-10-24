from hypothesis import given, settings
from hypothesis import strategies as st

from commute_agent.models import Leg, RouteOption
from commute_agent.routes import assemble_route, with_disruption

leg_strategy = st.builds(
    Leg,
    mode=st.sampled_from(["walk", "bike", "subway"]),
    distance_m=st.floats(min_value=0, max_value=10_000, allow_nan=False),
    duration_min=st.floats(min_value=0, max_value=120, allow_nan=False),
    co2_g=st.floats(min_value=0, max_value=5_000, allow_nan=False),
    crash_risk=st.floats(min_value=0, max_value=50, allow_nan=False),
    disruption_risk=st.floats(min_value=0, max_value=1, allow_nan=False),
    alert_ids=st.lists(st.sampled_from(["a1", "a2", "a3"]), max_size=3),
    risk_basis_m=st.dictionaries(
        st.sampled_from(["bluebikes_proxy", "road_class_prior", "outside_crash_coverage"]),
        st.floats(min_value=0, max_value=10_000, allow_nan=False),
        max_size=3,
    ),
)


@settings(max_examples=200)
@given(legs=st.lists(leg_strategy, min_size=1, max_size=6))
def test_route_assembly_sums_legs(legs: list[Leg]) -> None:
    """Feature: pareto-commute-agent, Property 15: Route assembly sums legs."""
    route = assemble_route("r", "transit", "s", legs, "t", "c", "k")
    assert abs(route.objectives.time_min - sum(leg.duration_min for leg in legs)) < 1e-9
    assert abs(route.objectives.co2_g - sum(leg.co2_g for leg in legs)) < 1e-6
    assert abs(route.objectives.crash_risk - sum(leg.crash_risk for leg in legs)) < 1e-9
    assert route.objectives.disruption_risk == max(leg.disruption_risk for leg in legs)
    assert 0.0 <= route.unknown_exposure_share <= 1.0


def test_with_disruption_recomputes_only_disruption_and_alerts() -> None:
    legs = [Leg(mode="subway", distance_m=1000, duration_min=10, co2_g=50, alert_ids=["a1"])]
    route: RouteOption = assemble_route("r", "transit", "s", legs, "t", "c", "k")
    calm = [Leg(mode="subway", distance_m=1000, duration_min=10, co2_g=50, disruption_risk=0.6)]
    updated = with_disruption(route, calm)
    assert updated.objectives.disruption_risk == 0.6
    assert updated.objectives.time_min == 10
    assert updated.alert_ids == []
