"""WS5 Pareto engine property and unit tests."""

from hypothesis import given, settings
from hypothesis import strategies as st

from commute_agent.models import (
    OBJECTIVES,
    AlertRef,
    Objectives,
    RouteKind,
    RouteOption,
)
from commute_agent.pareto import (
    CRASH_LABEL_KINDS,
    LABEL_FOR,
    assign_labels,
    dominates,
    filtered_out,
    near_duplicate,
    pareto_front,
    tradeoff,
)
from tests.conftest import make_route, objectives_strategy

ROUTE_KINDS: tuple[RouteKind, ...] = ("walk", "bike", "bluebikes", "drive", "transit")


def _route_from_objectives(id: str, kind: RouteKind, obj: Objectives, n_legs: int) -> RouteOption:
    return make_route(
        id=id,
        kind=kind,
        time_min=obj.time_min,
        co2_g=obj.co2_g,
        crash_risk=obj.crash_risk,
        disruption_risk=obj.disruption_risk,
        n_legs=n_legs,
    )


@st.composite
def candidate_lists(draw: st.DrawFn, min_size: int = 1, max_size: int = 8) -> list[RouteOption]:
    """A list of RouteOptions with unique ids drawn from objectives_strategy."""
    n = draw(st.integers(min_value=min_size, max_value=max_size))
    routes: list[RouteOption] = []
    for i in range(n):
        obj = draw(objectives_strategy())
        kind = draw(st.sampled_from(ROUTE_KINDS))
        n_legs = draw(st.integers(min_value=1, max_value=4))
        routes.append(_route_from_objectives(f"r{i}", kind, obj, n_legs))
    return routes


# --- Property 1: Pareto soundness ---------------------------------------------


@settings(max_examples=200)
@given(candidates=candidate_lists())
def test_property_1_pareto_soundness(candidates: list[RouteOption]) -> None:
    """Feature: pareto-commute-agent, Property 1: Pareto soundness.

    No route returned by pareto_front is dominated by any candidate in the input.
    """
    front = pareto_front(candidates)
    for member in front:
        for candidate in candidates:
            if candidate.id == member.id:
                continue
            assert not dominates(candidate.objectives, member.objectives)


# --- Property 2: Pareto completeness ------------------------------------------


@settings(max_examples=200)
@given(candidates=candidate_lists())
def test_property_2_pareto_completeness(candidates: list[RouteOption]) -> None:
    """Feature: pareto-commute-agent, Property 2: Pareto completeness.

    Every candidate that no other candidate dominates is in the front or is a
    near duplicate of a route in the output.
    """
    front = pareto_front(candidates)
    front_ids = {r.id for r in front}
    for candidate in candidates:
        dominated = any(
            other.id != candidate.id and dominates(other.objectives, candidate.objectives)
            for other in candidates
        )
        if dominated:
            continue
        if candidate.id in front_ids:
            continue
        assert any(near_duplicate(candidate.objectives, member.objectives) for member in front)


@settings(max_examples=200)
@given(candidates=candidate_lists())
def test_filtered_out_explains_every_candidate_outside_the_front(
    candidates: list[RouteOption],
) -> None:
    """Every candidate is in the front or in filtered_out, never both, and each filtered
    route's reason holds against the route named for it."""
    front = pareto_front(candidates)
    filtered = filtered_out(candidates, front)
    front_ids = {r.id for r in front}
    filtered_ids = [f.route.id for f in filtered]
    assert sorted([*front_ids, *filtered_ids]) == sorted(r.id for r in candidates)
    by_id = {r.id: r for r in candidates}
    for f in filtered:
        by = by_id[f.by_route_id]
        assert f.by_objectives == by.objectives
        if f.reason == "dominated":
            assert dominates(by.objectives, f.route.objectives)
        else:
            assert by.id in front_ids
            assert near_duplicate(by.objectives, f.route.objectives)


def test_filtered_out_names_the_dominating_front_route() -> None:
    fast = make_route("fast", "transit", 10.0, 100.0, 1.0, 0.0)
    slow = make_route("slow", "transit", 20.0, 200.0, 2.0, 0.0)
    (dropped,) = filtered_out([fast, slow], pareto_front([fast, slow]))
    assert (dropped.route.id, dropped.reason, dropped.by_route_id) == ("slow", "dominated", "fast")


def test_filtered_out_reports_near_duplicates() -> None:
    few = make_route(
        "few", "transit", time_min=10.0, co2_g=100.0, crash_risk=0.0, disruption_risk=0.0, n_legs=1
    )
    many = make_route(
        "many", "transit", time_min=10.3, co2_g=100.0, crash_risk=0.0, disruption_risk=0.0, n_legs=3
    )
    (dropped,) = filtered_out([few, many], pareto_front([few, many]))
    assert (dropped.route.id, dropped.reason) == ("many", "near_duplicate")
    assert dropped.by_route_id == "few"


# --- Property 3: Dominance is irreflexive and asymmetric ----------------------


@settings(max_examples=300)
@given(a=objectives_strategy(), b=objectives_strategy())
def test_property_3_dominance_irreflexive_and_asymmetric(a: Objectives, b: Objectives) -> None:
    """Feature: pareto-commute-agent, Property 3: Dominance is irreflexive and asymmetric."""
    assert not dominates(a, a)
    assert not (dominates(a, b) and dominates(b, a))


# --- Property 4: Input order does not change the front ------------------------


@settings(max_examples=200)
@given(candidates=candidate_lists(min_size=2), data=st.data())
def test_property_4_input_order_does_not_change_front(
    candidates: list[RouteOption], data: st.DataObject
) -> None:
    """Feature: pareto-commute-agent, Property 4: Input order does not change the front.

    Tolerance-based dominance is not transitive, so compare the sorted id lists of
    the front across a permutation rather than relying on order within the front.
    """
    permutation = data.draw(st.permutations(candidates))
    base = sorted(r.id for r in pareto_front(candidates))
    permuted = sorted(r.id for r in pareto_front(list(permutation)))
    assert base == permuted


# --- Property 6: Labels are unique and minimal --------------------------------


@settings(max_examples=200)
@given(candidates=candidate_lists())
def test_property_6_labels_unique_and_minimal(candidates: list[RouteOption]) -> None:
    """Feature: pareto-commute-agent, Property 6: Labels are unique and minimal."""
    front = assign_labels(pareto_front(candidates))
    if not front:
        return

    all_labels = [label for r in front for label in r.labels]
    assert len(all_labels) == len(set(all_labels))  # each label at most once

    for objective, label in LABEL_FOR.items():
        if objective == "crash_risk":
            eligible = [r for r in front if r.kind in CRASH_LABEL_KINDS]
            if not eligible:
                assert all(label not in r.labels for r in front)
                continue
            holders = [r for r in front if label in r.labels]
            assert len(holders) == 1
            best = min(getattr(r.objectives, objective) for r in eligible)
            assert getattr(holders[0].objectives, objective) == best
        else:
            holders = [r for r in front if label in r.labels]
            assert len(holders) == 1  # always exactly one for a non-empty front
            best = min(getattr(r.objectives, objective) for r in front)
            assert getattr(holders[0].objectives, objective) == best


# --- Property 7: Trade-off antisymmetry ---------------------------------------


@settings(max_examples=200)
@given(a=objectives_strategy(), b=objectives_strategy())
def test_property_7_tradeoff_antisymmetry(a: Objectives, b: Objectives) -> None:
    """Feature: pareto-commute-agent, Property 7: Trade-off antisymmetry.

    tradeoff(a, b).delta == -tradeoff(b, a).delta for every objective.
    """
    ra = _route_from_objectives("a", "bike", a, 1)
    rb = _route_from_objectives("b", "drive", b, 1)
    ab = tradeoff(ra, rb, {})
    ba = tradeoff(rb, ra, {})
    for objective in OBJECTIVES:
        assert getattr(ab.delta, objective) == -getattr(ba.delta, objective)


# --- Unit edge cases ----------------------------------------------------------


def test_near_duplicates_collapse_to_fewer_legs() -> None:
    """Two routes 0.3 min apart with otherwise equal objectives collapse to the
    one with fewer legs (0.3 < time ABS_TOL of 0.5)."""
    few = make_route(
        "few", "transit", time_min=10.0, co2_g=100.0, crash_risk=0.0, disruption_risk=0.0, n_legs=1
    )
    many = make_route(
        "many", "transit", time_min=10.3, co2_g=100.0, crash_risk=0.0, disruption_risk=0.0, n_legs=3
    )
    front = pareto_front([few, many])
    assert [r.id for r in front] == ["few"]


def test_near_duplicates_collapse_order_independent() -> None:
    """The same collapse holds regardless of input order."""
    few = make_route(
        "few", "transit", time_min=10.0, co2_g=100.0, crash_risk=0.0, disruption_risk=0.0, n_legs=1
    )
    many = make_route(
        "many", "transit", time_min=10.3, co2_g=100.0, crash_risk=0.0, disruption_risk=0.0, n_legs=3
    )
    assert [r.id for r in pareto_front([many, few])] == ["few"]


def test_drive_crash_zero_no_label_without_bike() -> None:
    """A drive route with crash risk 0 does not get lowest_crash_risk when no
    walk/bike/bluebikes route is present: the label is absent entirely."""
    drive = make_route(
        "drive-0", "drive", time_min=5.0, co2_g=900.0, crash_risk=0.0, disruption_risk=0.0
    )
    transit = make_route(
        "transit-0", "transit", time_min=8.0, co2_g=50.0, crash_risk=0.0, disruption_risk=0.2
    )
    labeled = assign_labels(pareto_front([drive, transit]))
    assert all("lowest_crash_risk" not in r.labels for r in labeled)


def test_drive_crash_zero_no_label_with_bike() -> None:
    """A drive route with crash risk 0 does not get lowest_crash_risk even when a
    bike route with higher crash risk is present; the label goes to the bike route."""
    drive = make_route(
        "drive-0", "drive", time_min=5.0, co2_g=900.0, crash_risk=0.0, disruption_risk=0.0
    )
    bike = make_route(
        "bike-1", "bike", time_min=12.0, co2_g=0.0, crash_risk=3.0, disruption_risk=0.0
    )
    labeled = assign_labels(pareto_front([drive, bike]))
    by_id = {r.id: r for r in labeled}
    assert "lowest_crash_risk" not in by_id["drive-0"].labels
    assert "lowest_crash_risk" in by_id["bike-1"].labels


def test_tradeoff_pct_none_when_reference_co2_zero() -> None:
    """pct is None for an objective whose reference value is 0."""
    route = make_route("r", "bike", time_min=12.0, co2_g=0.0, crash_risk=2.0, disruption_risk=0.0)
    reference = make_route(
        "ref", "bike", time_min=10.0, co2_g=0.0, crash_risk=1.0, disruption_risk=0.0
    )
    result = tradeoff(route, reference, {})
    assert result.pct["co2_g"] is None
    assert result.delta.co2_g == 0.0
    assert result.pct["time_min"] == 20.0  # (12 - 10) / 10 * 100


def test_assign_labels_returns_copies_with_labels() -> None:
    """assign_labels fills labels on returned copies without mutating the input."""
    fast = make_route(
        "fast", "drive", time_min=5.0, co2_g=900.0, crash_risk=0.0, disruption_risk=0.0
    )
    clean = make_route(
        "clean", "bike", time_min=20.0, co2_g=0.0, crash_risk=1.0, disruption_risk=0.0
    )
    front = pareto_front([fast, clean])
    labeled = assign_labels(front)
    assert all(r.labels == [] for r in front)  # input untouched
    by_id = {r.id: r for r in labeled}
    assert "fastest" in by_id["fast"].labels
    assert "lowest_co2" in by_id["clean"].labels
    assert "lowest_crash_risk" in by_id["clean"].labels


def test_tradeoff_alerts_avoided_in_reference_order() -> None:
    """alerts_avoided lists reference alerts absent from the route, in reference order."""
    route = make_route(
        "r", "transit", time_min=12.0, co2_g=50.0, crash_risk=0.0, disruption_risk=0.0
    )
    reference = make_route(
        "ref", "transit", time_min=10.0, co2_g=50.0, crash_risk=0.0, disruption_risk=0.5
    )
    reference.alert_ids = ["a2", "a1"]
    route.alert_ids = ["a1"]
    alerts = {
        "a1": AlertRef(id="a1", header="h1", effect="DETOUR", score=0.3),
        "a2": AlertRef(id="a2", header="h2", effect="SUSPENSION", score=1.0),
    }
    result = tradeoff(route, reference, alerts)
    assert [a.id for a in result.alerts_avoided] == ["a2"]


def test_tolerance_cycle_still_leaves_a_non_empty_front() -> None:
    # Found by fuzzing: under tolerance dominance every one of these is dominated by another.
    routes = [
        make_route("r0", "walk", 11.0, 0.9, 0.041, 0.01),
        make_route("r1", "walk", 10.6, 2.0, 0.018, 0.01),
        make_route("r2", "walk", 10.3, 1.2, 0.030, 0.02),
        make_route("r3", "walk", 10.9, 0.4, 0.023, 0.02),
    ]
    assert all(
        any(o is not r and dominates(o.objectives, r.objectives) for o in routes) for r in routes
    )
    assert sorted(r.id for r in pareto_front(routes)) == ["r0", "r1", "r2", "r3"]
