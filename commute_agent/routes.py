from commute_agent.models import (
    UNKNOWN_EXPOSURE_BASES,
    Leg,
    Objectives,
    RouteKind,
    RouteOption,
)

DISRUPTION_SOURCE = "MBTA alerts scored by the prototype's effect table (heuristic index, 0 to 1)"


def _unique(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _alert_ids(legs: list[Leg]) -> list[str]:
    return _unique([alert_id for leg in legs for alert_id in leg.alert_ids])


def _disruption(legs: list[Leg]) -> float:
    return max((leg.disruption_risk for leg in legs), default=0.0)


def _unknown_share(legs: list[Leg]) -> float:
    metres = [m for leg in legs for m in leg.risk_basis_m.items()]
    total = sum(m for _, m in metres)
    if total == 0:
        return 0.0
    unknown = sum(m for basis, m in metres if basis in UNKNOWN_EXPOSURE_BASES)
    return unknown / total


def assemble_route(
    route_id: str,
    kind: RouteKind,
    summary: str,
    legs: list[Leg],
    time_source: str,
    co2_source: str,
    crash_source: str,
) -> RouteOption:
    """Sum time, CO2, and crash risk over legs. Take the max leg disruption risk.

    Union leg alert ids in leg order. Compute unknown_exposure_share as the
    UNKNOWN_EXPOSURE_BASES metres over all risk_basis_m metres (0.0 when there are none).
    """
    objectives = Objectives(
        time_min=sum(leg.duration_min for leg in legs),
        co2_g=sum(leg.co2_g for leg in legs),
        crash_risk=sum(leg.crash_risk for leg in legs),
        disruption_risk=_disruption(legs),
    )
    return RouteOption(
        id=route_id,
        kind=kind,
        summary=summary,
        legs=legs,
        objectives=objectives,
        sources={
            "time_min": time_source,
            "co2_g": co2_source,
            "crash_risk": crash_source,
            "disruption_risk": DISRUPTION_SOURCE,
        },
        alert_ids=_alert_ids(legs),
        unknown_exposure_share=_unknown_share(legs),
    )


def with_disruption(route: RouteOption, legs: list[Leg]) -> RouteOption:
    """Return a copy with new legs and recomputed disruption_risk and alert_ids."""
    objectives = route.objectives.model_copy(update={"disruption_risk": _disruption(legs)})
    return route.model_copy(
        update={"legs": legs, "objectives": objectives, "alert_ids": _alert_ids(legs)}
    )
