"""Tests for MBTA alert ingestion and disruption scoring (WS4)."""

import json
from datetime import datetime
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from commute_agent.models import (
    ActivePeriod,
    Alert,
    AlertEntity,
    AlertSnapshot,
    Leg,
    RouteOption,
    TransitRef,
)
from commute_agent.paths import ALERT_SNAPSHOTS
from commute_agent.routes import assemble_route
from commute_agent.transit.alerts import (
    apply_disruptions,
    effect_score,
    entity_matches,
    is_active,
    list_alert_sources,
    load_alerts,
    parse_mbta_alerts,
    suspended_routes,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTHETIC_LABEL = "Synthetic: Red Line suspended JFK/UMass to Park Street"


def red_line_route(route_id: str = "r1") -> RouteOption:
    """A one-leg transit route on the Red Line through place-jfk."""
    transit = TransitRef(
        route_id="Red",
        route_name="Red Line",
        route_type=1,
        direction_id=0,
        stop_ids=["place-jfk", "place-andrw", "place-sstat"],
    )
    leg = Leg(
        mode="subway",
        distance_m=5000,
        duration_min=20,
        co2_g=120,
        transit=transit,
    )
    return assemble_route(route_id, "transit", "Red Line", [leg], "t", "c", "k")


def red_line_alert() -> Alert:
    """The committed synthetic Red Line suspension, open-ended from 05:00 -04:00."""
    return Alert(
        id="synthetic-red-1",
        header="Synthetic demo alert: Red Line suspended between JFK/UMass and Park Street.",
        effect="SUSPENSION",
        severity=7,
        active_periods=[ActivePeriod(start=datetime.fromisoformat("2026-10-27T05:00:00-04:00"))],
        entities=[
            AlertEntity(route="Red", stop="place-jfk", route_type=1),
            AlertEntity(route="Red", stop="place-pktrm", route_type=1),
        ],
    )


# --- Boundary parsing ------------------------------------------------------


def test_parse_mbta_alerts_on_fixture() -> None:
    payload = json.loads((FIXTURES / "mbta_alerts.json").read_text())
    alerts = parse_mbta_alerts(payload)
    assert len(alerts) == 4
    by_id = {alert.id: alert for alert in alerts}
    syn = by_id["synthetic-red-1"]
    assert syn.effect == "SUSPENSION"
    assert syn.severity == 7
    assert syn.active_periods[0].end is None
    assert any(e.stop == "place-jfk" and e.route == "Red" for e in syn.entities)
    notice = by_id["1037418"]
    assert notice.effect == "NOTICE"
    assert notice.entities[0].direction_id == 0


def test_effect_score_delay_and_unmapped() -> None:
    delay = Alert(id="d", header="h", effect="DELAY", severity=12, active_periods=[], entities=[])
    assert effect_score(delay) == 1.0  # capped at 1.0
    delay.severity = 5
    assert effect_score(delay) == 0.5
    made_up = Alert(
        id="m", header="h", effect="MADE_UP", severity=3, active_periods=[], entities=[]
    )
    assert effect_score(made_up) is None
    suspension = red_line_alert()
    assert effect_score(suspension) == 1.0


# --- Source listing and loading --------------------------------------------


def test_list_alert_sources_includes_synthetic() -> None:
    sources = list_alert_sources(ALERT_SNAPSHOTS)
    assert sources[0] == "none"
    assert sources[1] == "live"
    assert SYNTHETIC_LABEL in sources


def test_load_alerts_none_and_snapshot() -> None:
    assert load_alerts("none", live=False) == ([], None)
    alerts, clock = load_alerts(SYNTHETIC_LABEL, live=False)
    assert len(alerts) == 1
    assert alerts[0].id == "synthetic-red-1"
    assert clock == datetime.fromisoformat("2026-10-27T08:00:00-04:00")


def test_load_alerts_live_disabled_returns_empty() -> None:
    # live=False must never hit the network.
    assert load_alerts("live", live=False) == ([], None)


# --- entity_matches / is_active --------------------------------------------


def test_entity_matches_rules() -> None:
    transit = red_line_route().legs[0].transit
    assert entity_matches(AlertEntity(route="Red"), transit) is True
    assert entity_matches(AlertEntity(route="Orange"), transit) is False
    assert entity_matches(AlertEntity(stop="place-jfk"), transit) is True
    assert entity_matches(AlertEntity(stop="place-nowhere"), transit) is False
    assert entity_matches(AlertEntity(route="Red", direction_id=1), transit) is False
    assert entity_matches(AlertEntity(), transit) is False  # all-None matches nothing


def test_is_active_open_ended() -> None:
    alert = red_line_alert()
    before = datetime.fromisoformat("2026-10-27T04:59:00-04:00")
    after = datetime.fromisoformat("2026-10-27T09:00:00-04:00")
    assert is_active(alert, before) is False
    assert is_active(alert, after) is True


# --- Unit: synthetic alert scoring -----------------------------------------


def test_synthetic_alert_disrupts_red_line() -> None:
    route = red_line_route()
    alert = red_line_alert()
    active_at = datetime.fromisoformat("2026-10-27T08:00:00-04:00")
    out, unscored = apply_disruptions([route], [alert], active_at)
    assert out[0].objectives.disruption_risk == 1.0
    assert out[0].alert_ids == ["synthetic-red-1"]
    assert out[0].legs[0].disruption_risk == 1.0
    assert unscored == []


def test_synthetic_alert_before_start_is_calm() -> None:
    route = red_line_route()
    alert = red_line_alert()
    before = datetime.fromisoformat("2026-10-27T04:30:00-04:00")
    out, unscored = apply_disruptions([route], [alert], before)
    assert out[0].objectives.disruption_risk == 0.0
    assert out[0].alert_ids == []
    assert unscored == []


def test_unmapped_effect_lands_in_unscored() -> None:
    route = red_line_route()
    alert = Alert(
        id="weird-1",
        header="something new",
        effect="MADE_UP",
        severity=5,
        active_periods=[ActivePeriod(start=datetime.fromisoformat("2026-10-27T05:00:00-04:00"))],
        entities=[AlertEntity(route="Red", stop="place-jfk", route_type=1)],
    )
    at = datetime.fromisoformat("2026-10-27T08:00:00-04:00")
    out, unscored = apply_disruptions([route], [alert], at)
    assert out[0].objectives.disruption_risk == 0.0
    assert [ref.id for ref in unscored] == ["weird-1"]
    assert unscored[0].score is None
    # The matching alert id is still listed on the route even when unscored.
    assert out[0].alert_ids == ["weird-1"]


# --- Property tests --------------------------------------------------------

AT = datetime.fromisoformat("2026-10-27T08:00:00-04:00")

unrelated_alert_strategy = st.builds(
    Alert,
    id=st.text(min_size=1, max_size=8),
    header=st.text(max_size=20),
    effect=st.sampled_from(["SUSPENSION", "DELAY", "DETOUR", "NOTICE", "MADE_UP"]),
    severity=st.integers(min_value=0, max_value=10),
    active_periods=st.just(
        [ActivePeriod(start=datetime.fromisoformat("2026-10-27T05:00:00-04:00"))]
    ),
    entities=st.lists(
        st.builds(
            AlertEntity,
            route=st.sampled_from(["Orange", "Blue", "Green-D"]),
            stop=st.sampled_from(["place-ogmnl", "place-river", "place-nowhere"]),
            route_type=st.sampled_from([0, 1]),
        ),
        min_size=1,
        max_size=3,
    ),
)


@settings(max_examples=200)
@given(alerts=st.lists(unrelated_alert_strategy, max_size=5))
def test_property10_unrelated_alerts_change_nothing(alerts: list[Alert]) -> None:
    """Property 10: alerts that match no leg leave every objective untouched."""
    route = red_line_route()
    baseline_disruption = route.objectives.disruption_risk
    out, unscored = apply_disruptions([route], alerts, AT)
    assert out[0].objectives.disruption_risk == baseline_disruption
    assert out[0].alert_ids == []
    assert unscored == []
    # non-disruption objectives are untouched too
    assert out[0].objectives.time_min == route.objectives.time_min
    assert out[0].objectives.co2_g == route.objectives.co2_g


# Alerts that DO match the Red Line leg, each a known effect with a known score.
matching_alert_strategy = st.builds(
    Alert,
    id=st.text(min_size=1, max_size=8),
    header=st.just("h"),
    effect=st.sampled_from(["SUSPENSION", "DETOUR", "MODIFIED_SERVICE", "NOTICE"]),
    severity=st.integers(min_value=0, max_value=10),
    active_periods=st.just(
        [ActivePeriod(start=datetime.fromisoformat("2026-10-27T05:00:00-04:00"))]
    ),
    entities=st.just([AlertEntity(route="Red", stop="place-jfk", route_type=1)]),
)


@settings(max_examples=200)
@given(alerts=st.lists(matching_alert_strategy, min_size=1, max_size=6))
def test_property11_route_disruption_is_leg_maximum(alerts: list[Alert]) -> None:
    """Property 11: a route's disruption risk equals the max over its legs."""
    route = red_line_route()
    out, _ = apply_disruptions([route], alerts, AT)
    leg_max = max((leg.disruption_risk for leg in out[0].legs), default=0.0)
    assert out[0].objectives.disruption_risk == leg_max
    expected = max(
        (score for alert in alerts if (score := effect_score(alert)) is not None),
        default=0.0,
    )
    assert out[0].objectives.disruption_risk == expected


# --- Committed snapshot validity -------------------------------------------


def test_committed_synthetic_snapshot_is_valid() -> None:
    path = ALERT_SNAPSHOTS / "red-line-suspension-synthetic.json"
    snapshot = AlertSnapshot.model_validate_json(path.read_text())
    assert snapshot.label == SYNTHETIC_LABEL
    assert snapshot.synthetic is True
    assert snapshot.recorded_at == datetime.fromisoformat("2026-10-27T08:00:00-04:00")
    assert len(snapshot.alerts) == 1
    assert snapshot.alerts[0].id == "synthetic-red-1"
    assert snapshot.alerts[0].effect == "SUSPENSION"
    assert {e.stop for e in snapshot.alerts[0].entities} >= {"place-jfk", "place-pktrm"}


def test_synthetic_suspension_names_the_red_line_only_while_active() -> None:
    from commute_agent.transit.alerts import load_alerts, suspended_routes

    alerts, clock = load_alerts(
        "Synthetic: Red Line suspended JFK/UMass to Park Street", live=False
    )
    assert suspended_routes(alerts, clock, {"Red", "15"}) == ("Red",)
    assert suspended_routes(alerts, clock, {"15"}) == ()
    before = datetime.fromisoformat("2026-10-27T04:00:00-04:00")
    assert suspended_routes(alerts, before, {"Red"}) == ()


# --- Step-free elevator closures -------------------------------------------

ELEVATOR_AT = datetime.fromisoformat("2026-10-27T08:15:00-04:00")
DOWNTOWN_CROSSING_ALERT = "1031518"


def elevator_alerts() -> list[Alert]:
    """Recorded ELEVATOR_CLOSURE alerts from the MBTA V3 API, trimmed to a few entities."""
    return parse_mbta_alerts(json.loads((FIXTURES / "mbta_elevator_alerts.json").read_text()))


def downtown_crossing_route() -> RouteOption:
    """A one-leg Red Line route that boards at Downtown Crossing (stop 70078)."""
    transit = TransitRef(
        route_id="Red",
        route_name="Red Line",
        route_type=1,
        direction_id=1,
        stop_ids=["70078", "place-dwnxg"],
    )
    leg = Leg(mode="subway", distance_m=1000, duration_min=5, co2_g=10, transit=transit)
    return assemble_route("r-dwn", "transit", "Red Line", [leg], "t", "c", "k")


def test_elevator_fixture_is_real_elevator_closures() -> None:
    alerts = elevator_alerts()
    assert {alert.effect for alert in alerts} == {"ELEVATOR_CLOSURE"}
    assert len(alerts) == 3


def test_elevator_closure_scores_only_in_step_free_mode() -> None:
    alert = next(a for a in elevator_alerts() if a.id == DOWNTOWN_CROSSING_ALERT)
    assert effect_score(alert) == 0.2  # today's table score, unchanged when step-free is off
    assert effect_score(alert, step_free=True) == 1.0


def test_elevator_closure_flags_matching_leg_only_when_step_free() -> None:
    route = downtown_crossing_route()
    alerts = elevator_alerts()

    off, _ = apply_disruptions([route], alerts, ELEVATOR_AT)
    assert off[0].legs[0].disruption_risk == 0.2
    assert DOWNTOWN_CROSSING_ALERT in off[0].alert_ids

    on, unscored = apply_disruptions([route], alerts, ELEVATOR_AT, step_free=True)
    assert on[0].legs[0].disruption_risk == 1.0
    assert on[0].objectives.disruption_risk == 1.0
    assert DOWNTOWN_CROSSING_ALERT in on[0].alert_ids
    assert unscored == []


def test_elevator_closure_elsewhere_does_not_flag_step_free_leg() -> None:
    # The leg boards at Downtown Crossing; the Haymarket closure names other stops.
    route = downtown_crossing_route()
    haymarket = next(a for a in elevator_alerts() if a.id == "1040976")
    on, _ = apply_disruptions([route], [haymarket], ELEVATOR_AT, step_free=True)
    assert on[0].legs[0].disruption_risk == 0.0
    assert on[0].alert_ids == []


def test_elevator_closure_never_reroutes_like_a_suspension() -> None:
    alerts = elevator_alerts()
    assert suspended_routes(alerts, ELEVATOR_AT, {"Red", "Green-D", "110"}) == ()


RED_SOUTHBOUND = TransitRef(
    route_id="Red",
    route_name="Red Line",
    route_type=1,
    direction_id=0,
    stop_ids=["70061", "place-alfcl", "70063", "place-davis", "70065", "place-portr"],
    board_stop_ids=["70061", "place-alfcl"],
    alight_stop_ids=["70065", "place-portr"],
)


def test_board_and_exit_activities_match_only_the_end_stops() -> None:
    board_at_davis = AlertEntity(stop="place-davis", activities=["BOARD", "EXIT"])
    board_at_alewife = AlertEntity(stop="place-alfcl", activities=["BOARD"])
    exit_at_alewife = AlertEntity(stop="place-alfcl", activities=["EXIT"])
    assert not entity_matches(board_at_davis, RED_SOUTHBOUND)  # the leg only rides through
    assert entity_matches(board_at_alewife, RED_SOUTHBOUND)
    assert not entity_matches(exit_at_alewife, RED_SOUTHBOUND)


def test_ride_and_missing_activities_match_every_stop() -> None:
    assert entity_matches(AlertEntity(stop="place-davis", activities=["RIDE"]), RED_SOUTHBOUND)
    assert entity_matches(AlertEntity(stop="place-davis"), RED_SOUTHBOUND)


def test_station_access_activities_match_board_and_alight_stops() -> None:
    wheelchair = ["USING_WHEELCHAIR"]
    assert entity_matches(AlertEntity(stop="place-portr", activities=wheelchair), RED_SOUTHBOUND)
    assert not entity_matches(
        AlertEntity(stop="place-davis", activities=wheelchair), RED_SOUTHBOUND
    )


def test_parse_reads_entity_activities() -> None:
    payload = {
        "data": [
            {
                "id": "a1",
                "attributes": {
                    "header": "Elevator closed",
                    "effect": "ELEVATOR_CLOSURE",
                    "severity": 3,
                    "active_period": [{"start": "2026-10-25T00:00:00-04:00"}],
                    "informed_entity": [
                        {"stop": "place-davis", "activities": ["USING_WHEELCHAIR"]}
                    ],
                },
            }
        ]
    }
    (alert,) = parse_mbta_alerts(payload)
    assert alert.entities[0].activities == ["USING_WHEELCHAIR"]


def test_live_fetch_follows_next_links(monkeypatch) -> None:
    import httpx

    from commute_agent.transit import alerts as alerts_module

    def page(alert_id: str, next_url: str | None) -> dict:
        alert = {
            "id": alert_id,
            "attributes": {
                "header": alert_id,
                "effect": "DELAY",
                "severity": 3,
                "active_period": [],
                "informed_entity": [{"route": "Red"}],
            },
        }
        return {"data": [alert], "links": {"next": next_url} if next_url else {}}

    pages = {
        alerts_module.MBTA_ALERTS_URL: page("p1", "https://api-v3.mbta.com/alerts?page[offset]=1"),
        "https://api-v3.mbta.com/alerts?page[offset]=1": page("p2", None),
    }
    seen: list[str] = []

    def fake_get(url, params=None, timeout=None):
        seen.append(url)
        return httpx.Response(200, json=pages[url], request=httpx.Request("GET", url))

    monkeypatch.setattr(alerts_module.httpx, "get", fake_get)
    assert [a.id for a in alerts_module._fetch_live()] == ["p1", "p2"]
    assert len(seen) == 2
