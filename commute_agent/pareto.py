"""WS5 Pareto engine: pure functions over RouteOption.

Dominance uses a per-objective tolerance combining an absolute floor with a
relative term. Because the tolerance depends on the pair of values being
compared, dominance is not transitive, so `pareto_front` compares every
candidate against every other candidate (not only against kept ones), which is
what makes the result independent of input order.
"""

from commute_agent.models import (
    OBJECTIVES,
    AlertRef,
    Label,
    Objective,
    Objectives,
    RouteKind,
    RouteOption,
    Tradeoff,
)

ABS_TOL: dict[Objective, float] = {
    "time_min": 0.5,
    "co2_g": 1.0,
    "crash_risk": 0.01,
    "disruption_risk": 0.01,
}
REL_TOL: dict[Objective, float] = {
    "time_min": 0.0,
    "co2_g": 0.01,
    "crash_risk": 0.02,
    "disruption_risk": 0.0,
}
LABEL_FOR: dict[Objective, Label] = {
    "time_min": "fastest",
    "co2_g": "lowest_co2",
    "crash_risk": "lowest_crash_risk",
    "disruption_risk": "least_disrupted",
}
CRASH_LABEL_KINDS: frozenset[RouteKind] = frozenset({"walk", "bike", "bluebikes"})


def tolerance(a: float, b: float, objective: Objective) -> float:
    """max(ABS_TOL[objective], REL_TOL[objective] * max(abs(a), abs(b)))."""
    return max(ABS_TOL[objective], REL_TOL[objective] * max(abs(a), abs(b)))


def dominates(a: Objectives, b: Objectives) -> bool:
    """For every objective a <= b + tol, and for at least one a < b - tol."""
    strictly_better = False
    for objective in OBJECTIVES:
        av = getattr(a, objective)
        bv = getattr(b, objective)
        tol = tolerance(av, bv, objective)
        if av > bv + tol:
            return False
        if av < bv - tol:
            strictly_better = True
    return strictly_better


def near_duplicate(a: Objectives, b: Objectives) -> bool:
    """For every objective abs(a - b) <= tol."""
    for objective in OBJECTIVES:
        av = getattr(a, objective)
        bv = getattr(b, objective)
        if abs(av - bv) > tolerance(av, bv, objective):
            return False
    return True


def _sort_key(route: RouteOption) -> tuple[int, float, str]:
    return (len(route.legs), route.objectives.time_min, route.id)


def pareto_front(candidates: list[RouteOption]) -> list[RouteOption]:
    """1. Sort by (len(legs), time_min, id).
    2. Keep candidates that no other candidate dominates.
    3. Walk the kept list in sorted order and skip any route that is a
       near_duplicate of one already taken.
    """
    ordered = sorted(candidates, key=_sort_key)
    non_dominated = [
        route
        for route in ordered
        if not any(
            other is not route and dominates(other.objectives, route.objectives)
            for other in candidates
        )
    ]
    front: list[RouteOption] = []
    for route in non_dominated:
        if any(near_duplicate(route.objectives, kept.objectives) for kept in front):
            continue
        front.append(route)
    return front


def assign_labels(front: list[RouteOption]) -> list[RouteOption]:
    """Return copies of the front with labels filled.

    Each label goes to the single route with the minimum objective value, ties
    broken by (time_min, id). lowest_crash_risk considers only routes whose kind
    is in CRASH_LABEL_KINDS, because drive and transit vehicle segments are not
    modeled; it is absent when no such route is in the front.
    """
    copies = [route.model_copy(update={"labels": []}) for route in front]
    label_ids: dict[Label, str] = {}
    for objective, label in LABEL_FOR.items():
        if objective == "crash_risk":
            eligible = [r for r in copies if r.kind in CRASH_LABEL_KINDS]
        else:
            eligible = list(copies)
        if not eligible:
            continue
        winner = min(
            eligible,
            key=lambda r: (
                getattr(r.objectives, objective),
                r.objectives.time_min,
                r.id,
            ),
        )
        label_ids[label] = winner.id

    by_id = {r.id: r for r in copies}
    for label, route_id in label_ids.items():
        by_id[route_id].labels.append(label)
    return copies


def tradeoff(route: RouteOption, reference: RouteOption, alerts: dict[str, AlertRef]) -> Tradeoff:
    """delta = route - reference per objective. pct = 100 * delta / reference,
    None when reference is 0. alerts_avoided = alerts on reference not on route,
    in reference order.
    """
    delta_values: dict[Objective, float] = {}
    pct: dict[Objective, float | None] = {}
    for objective in OBJECTIVES:
        rv = getattr(route.objectives, objective)
        ref_v = getattr(reference.objectives, objective)
        d = rv - ref_v
        delta_values[objective] = d
        pct[objective] = None if ref_v == 0 else 100.0 * d / ref_v

    route_alert_ids = set(route.alert_ids)
    alerts_avoided = [
        alerts[alert_id]
        for alert_id in reference.alert_ids
        if alert_id not in route_alert_ids and alert_id in alerts
    ]

    return Tradeoff(
        route_id=route.id,
        reference_id=reference.id,
        delta=Objectives(**delta_values),
        pct=pct,
        alerts_avoided=alerts_avoided,
    )
