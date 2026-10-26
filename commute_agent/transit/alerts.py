"""MBTA alert ingestion and disruption scoring (WS4).

Boundary validation of the MBTA V3 JSON:API document lives in ``parse_mbta_alerts``.
Everything downstream works on the validated ``Alert`` model.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import httpx

from commute_agent import routes
from commute_agent.models import (
    ActivePeriod,
    Alert,
    AlertEntity,
    AlertRef,
    AlertSnapshot,
    RouteOption,
    TransitRef,
)
from commute_agent.paths import ALERT_SNAPSHOTS

MBTA_ALERTS_URL = "https://api-v3.mbta.com/alerts"
ALERT_PAGE_SIZE = 200
MAX_ALERT_PAGES = 10
# Activities that concern the stop where a leg boards or alights. RIDE concerns every stop
# the vehicle passes; anything else (USING_WHEELCHAIR, PARK_CAR, ...) is station access.
RIDE_ACTIVITY = "RIDE"
BOARD_ACTIVITY = "BOARD"
EXIT_ACTIVITY = "EXIT"
# Selectable without --live: the live MBTA loader, with the server's 60 s cache and a note
# instead of an error when the fetch fails.
MBTA_LIVE_SOURCE = "MBTA live"

# Prototype heuristic effect table. DELAY is handled in effect_score, not here.
EFFECT_SCORES: dict[str, float] = {
    "SUSPENSION": 1.0,
    "NO_SERVICE": 1.0,
    "SHUTTLE": 0.8,
    "STATION_CLOSURE": 0.7,
    "STOP_CLOSURE": 0.7,
    "CANCELLATION": 0.6,
    "DETOUR": 0.5,
    "MODIFIED_SERVICE": 0.3,
    "SERVICE_CHANGE": 0.3,
    "STOP_MOVE": 0.3,
    "STOP_MOVED": 0.3,
    "TRACK_CHANGE": 0.2,
    "SCHEDULE_CHANGE": 0.2,
    "STATION_ISSUE": 0.2,
    "ELEVATOR_CLOSURE": 0.2,
    "ESCALATOR_CLOSURE": 0.1,
    "NOTICE": 0.0,
    "POLICY_CHANGE": 0.0,
    "PARKING_CLOSURE": 0.0,
    "PARKING_ISSUE": 0.0,
    "AMBER_ALERT": 0.0,
    "BIKE_ISSUE": 0.0,
    "SUMMARY": 0.0,
    "EXTRA_SERVICE": 0.0,
}


# Step-free mode (the page's "Step-free" switch) scores these effects on matching legs.
# An elevator closure is a barrier for a wheelchair or stroller user, so it scores
# the same as a suspension there. Escalator closures are left out: a step-free trip
# has no use for an escalator, and the stairs beside it are not step-free either.
STEP_FREE_EFFECTS = frozenset({"ELEVATOR_CLOSURE"})
STEP_FREE_SCORE = 1.0


def effect_score(alert: Alert, *, step_free: bool = False) -> float | None:
    """Return the heuristic disruption score for an alert, or None when unmapped.

    DELAY scores ``severity / 10`` capped at 1.0. Every other effect is looked up
    in ``EFFECT_SCORES``; an effect absent from the table returns None. With
    ``step_free`` on, a ``STEP_FREE_EFFECTS`` alert scores ``STEP_FREE_SCORE``.
    """
    if step_free and alert.effect in STEP_FREE_EFFECTS:
        return STEP_FREE_SCORE
    if alert.effect == "DELAY":
        return min(alert.severity / 10, 1.0)
    return EFFECT_SCORES.get(alert.effect)


def parse_mbta_alerts(payload: dict) -> list[Alert]:
    """Parse and validate an MBTA V3 JSON:API alerts document into Alert models.

    This is the external boundary: anything that does not match the expected
    shape raises here rather than downstream.
    """
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("MBTA alerts payload has no 'data' list")

    alerts: list[Alert] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("MBTA alert entry is not an object")
        alert_id = item.get("id")
        attributes = item.get("attributes")
        if not isinstance(alert_id, str):
            raise ValueError("MBTA alert is missing a string id")
        if not isinstance(attributes, dict):
            raise ValueError(f"MBTA alert {alert_id} has no attributes object")

        header = attributes.get("header")
        effect = attributes.get("effect")
        severity = attributes.get("severity")
        if not isinstance(header, str):
            raise ValueError(f"MBTA alert {alert_id} has no string header")
        if not isinstance(effect, str):
            raise ValueError(f"MBTA alert {alert_id} has no string effect")
        if not isinstance(severity, int):
            raise ValueError(f"MBTA alert {alert_id} has no integer severity")

        active_periods: list[ActivePeriod] = []
        for period in attributes.get("active_period") or []:
            active_periods.append(ActivePeriod(start=period["start"], end=period.get("end")))

        entities: list[AlertEntity] = []
        for entity in attributes.get("informed_entity") or []:
            entities.append(
                AlertEntity(
                    route=entity.get("route"),
                    stop=entity.get("stop"),
                    route_type=entity.get("route_type"),
                    direction_id=entity.get("direction_id"),
                    activities=entity.get("activities") or [],
                )
            )

        alerts.append(
            Alert(
                id=alert_id,
                header=header,
                effect=effect,
                severity=severity,
                active_periods=active_periods,
                entities=entities,
            )
        )
    return alerts


def _slug(label: str) -> str:
    """Lowercase, hyphenated slug for a snapshot filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return slug or "snapshot"


def list_alert_sources(snapshot_dir: Path = ALERT_SNAPSHOTS) -> list[str]:
    """Return selectable alert sources: ['none', 'live', 'MBTA live', <snapshot labels sorted>]."""
    labels: list[str] = []
    if snapshot_dir.is_dir():
        for path in snapshot_dir.glob("*.json"):
            snapshot = AlertSnapshot.model_validate_json(path.read_text())
            labels.append(snapshot.label)
    return ["none", "live", MBTA_LIVE_SOURCE, *sorted(labels)]


def _fetch_live() -> list[Alert]:
    """Fetch current MBTA alerts for subway, light rail, and bus, following ``links.next``
    for up to MAX_ALERT_PAGES pages."""
    url: str | None = MBTA_ALERTS_URL
    params: dict[str, str] | None = {
        "filter[route_type]": "0,1,3",
        "page[limit]": str(ALERT_PAGE_SIZE),
    }
    alerts: list[Alert] = []
    for _ in range(MAX_ALERT_PAGES):
        if url is None:
            break
        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        alerts += parse_mbta_alerts(payload)
        # The next link already carries the filters and page offset.
        url = (payload.get("links") or {}).get("next")
        params = None
    return alerts


def load_alerts(
    source: str,
    *,
    live: bool,
    snapshot_dir: Path = ALERT_SNAPSHOTS,
) -> tuple[list[Alert], datetime | None]:
    """Return (alerts, alert_clock) for the chosen source.

    - ``'none'`` -> ``([], None)``.
    - ``'live'`` -> fetched alerts with clock None (meaning "use the departure time").
      Refused with an empty result when ``live`` is False.
    - ``'MBTA live'`` -> fetched alerts with clock None, whatever ``live`` is. A failed
      fetch raises ``httpx.HTTPError`` or ``ValueError``; the caller turns that into a note.
    - a snapshot label -> its alerts and its ``recorded_at`` clock.
    """
    if source == "none":
        return [], None
    if source == "live":
        if not live:
            return [], None
        return _fetch_live(), None
    if source == MBTA_LIVE_SOURCE:
        return _fetch_live(), None

    if snapshot_dir.is_dir():
        for path in snapshot_dir.glob("*.json"):
            snapshot = AlertSnapshot.model_validate_json(path.read_text())
            if snapshot.label == source:
                return snapshot.alerts, snapshot.recorded_at
    raise ValueError(f"unknown alert source: {source!r}")


def entity_matches(entity: AlertEntity, transit: TransitRef) -> bool:
    """True when every field the entity sets equals the leg's value.

    ``route == route_id``, ``route_type == route_type``,
    ``direction_id == direction_id``, and the stop among the leg's stops its activities
    concern (see ``_stops_for``). An entity with every field None matches nothing.
    """
    checks = (
        entity.route is not None,
        entity.stop is not None,
        entity.route_type is not None,
        entity.direction_id is not None,
    )
    if not any(checks):
        return False
    if entity.route is not None and entity.route != transit.route_id:
        return False
    if entity.route_type is not None and entity.route_type != transit.route_type:
        return False
    if entity.direction_id is not None and entity.direction_id != transit.direction_id:
        return False
    if entity.stop is not None and entity.stop not in _stops_for(entity.activities, transit):
        return False
    return True


def _stops_for(activities: list[str], transit: TransitRef) -> set[str]:
    """Stops of the leg that a stop entity with these activities can affect.

    No activities, or a leg without board and alight ids, means every stop on the leg.
    RIDE adds every stop; BOARD adds the boarding stop; EXIT the alighting stop; any other
    activity is station access and adds both.
    """
    if not activities or not (transit.board_stop_ids or transit.alight_stop_ids):
        return set(transit.stop_ids)
    stops: set[str] = set()
    for activity in activities:
        if activity == RIDE_ACTIVITY:
            stops.update(transit.stop_ids)
        elif activity == BOARD_ACTIVITY:
            stops.update(transit.board_stop_ids)
        elif activity == EXIT_ACTIVITY:
            stops.update(transit.alight_stop_ids)
        else:
            stops.update(transit.board_stop_ids, transit.alight_stop_ids)
    return stops


def is_active(alert: Alert, at: datetime) -> bool:
    """True when ``at`` falls inside one active period (a missing end is open-ended)."""
    for period in alert.active_periods:
        if at < period.start:
            continue
        if period.end is None or at <= period.end:
            return True
    return False


def _affects_leg(alert: Alert, transit: TransitRef) -> bool:
    return any(entity_matches(entity, transit) for entity in alert.entities)


SUSPENDING_SCORE = 1.0


def suspended_routes(alerts: list[Alert], at: datetime, route_ids: set[str]) -> tuple[str, ...]:
    """Route ids among ``route_ids`` named by an active alert that scores SUSPENDING_SCORE.

    The planner re-plans transit without these routes so an alternative can compete.
    """
    named = {
        entity.route
        for alert in alerts
        if is_active(alert, at) and effect_score(alert) == SUSPENDING_SCORE
        for entity in alert.entities
        if entity.route in route_ids
    }
    return tuple(sorted(named))


def apply_disruptions(
    routes_in: list[RouteOption],
    alerts: list[Alert],
    at: datetime,
    *,
    step_free: bool = False,
) -> tuple[list[RouteOption], list[AlertRef]]:
    """Score transit-leg disruption from active, matching alerts.

    For each transit leg, an alert affects it when it is active at ``at`` and one
    informed entity matches the leg. A leg's ``disruption_risk`` is the max
    ``effect_score`` over scored affecting alerts (0.0 when none); its ``alert_ids``
    are every affecting alert id. Matching alerts with an unmapped effect are
    collected once into the second return value. Routes are rebuilt with
    ``routes.with_disruption``. ``step_free`` is passed to ``effect_score``.
    """
    active = [alert for alert in alerts if is_active(alert, at)]
    unscored: dict[str, AlertRef] = {}

    out_routes: list[RouteOption] = []
    for route in routes_in:
        new_legs = []
        for leg in route.legs:
            if leg.transit is None:
                new_legs.append(leg.model_copy())
                continue

            affecting = [alert for alert in active if _affects_leg(alert, leg.transit)]
            scores = [
                score
                for alert in affecting
                if (score := effect_score(alert, step_free=step_free)) is not None
            ]
            for alert in affecting:
                if effect_score(alert, step_free=step_free) is None and alert.id not in unscored:
                    unscored[alert.id] = AlertRef(
                        id=alert.id, header=alert.header, effect=alert.effect, score=None
                    )
            new_legs.append(
                leg.model_copy(
                    update={
                        "disruption_risk": max(scores, default=0.0),
                        "alert_ids": [alert.id for alert in affecting],
                    }
                )
            )
        out_routes.append(routes.with_disruption(route, new_legs))

    return out_routes, list(unscored.values())


def record_snapshot(label: str, snapshot_dir: Path = ALERT_SNAPSHOTS) -> Path:
    """Fetch live MBTA alerts and write an AlertSnapshot with ``synthetic`` false.

    CLI: ``python -m commute_agent.transit.alerts record <label>``. Writes
    ``<slug(label)>.json`` into ``snapshot_dir`` and returns the path.
    """
    alerts = _fetch_live()
    snapshot = AlertSnapshot(
        label=label,
        recorded_at=datetime.now().astimezone(),
        synthetic=False,
        alerts=alerts,
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"{_slug(label)}.json"
    path.write_text(snapshot.model_dump_json(indent=2) + "\n")
    return path


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "record":
        label = " ".join(argv[1:])
        path = record_snapshot(label)
        print(f"wrote {path}")
        return 0
    print("usage: python -m commute_agent.transit.alerts record <label>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
