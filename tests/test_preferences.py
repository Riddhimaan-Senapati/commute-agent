import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from commute_agent.models import (
    OBJECTIVES,
    ChoiceEvent,
    Objectives,
    PreferenceWeights,
    RouteKind,
    RouteOption,
)
from commute_agent.preferences import (
    EQUAL_WEIGHTS,
    clear_choices,
    current_weights,
    fit_weights,
    load_choices,
    normalize,
    order_routes,
    record_choice,
)
from tests.conftest import make_route, objectives_strategy

ROUTE_KINDS: tuple[RouteKind, ...] = ("walk", "bike", "bluebikes", "drive", "transit")


def _route(i: int, obj: Objectives) -> RouteOption:
    return make_route(
        id=f"r-{i}",
        kind=ROUTE_KINDS[i % len(ROUTE_KINDS)],
        time_min=obj.time_min,
        co2_g=obj.co2_g,
        crash_risk=obj.crash_risk,
        disruption_risk=obj.disruption_risk,
    )


def _weights(values: dict[str, float]) -> PreferenceWeights:
    return PreferenceWeights(weights=values, n_choices=5, insufficient_data=False)


@settings(max_examples=200)
@given(
    objs=st.lists(objectives_strategy(), min_size=1, max_size=8),
    raw=st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=4, max_size=4),
)
def test_property_5_ordering_keeps_membership(objs: list[Objectives], raw: list[float]) -> None:
    """Feature: pareto-commute-agent, Property 5: Preference ordering keeps membership."""
    routes = [_route(i, o) for i, o in enumerate(objs)]
    total = sum(raw)
    values = (
        dict(EQUAL_WEIGHTS) if total == 0 else {o: raw[i] / total for i, o in enumerate(OBJECTIVES)}
    )
    ordered = order_routes(routes, _weights(values))
    assert sorted(r.id for r in ordered) == sorted(r.id for r in routes)
    assert len(ordered) == len(routes)


@settings(max_examples=100)
@given(objs=st.lists(objectives_strategy(), min_size=1, max_size=8))
def test_property_13_normalization_bounded(objs: list[Objectives]) -> None:
    """Feature: pareto-commute-agent, Property 13: Preference fallback and normalization.

    Each normalized column is divided by its max, so entries lie in [0, 1] and a
    column whose max is 0 stays all zeros.
    """
    norm = normalize(objs)
    assert norm.shape == (len(objs), len(OBJECTIVES))
    assert np.all(norm >= 0.0)
    assert np.all(norm <= 1.0 + 1e-9)
    matrix = np.array([[getattr(o, name) for name in OBJECTIVES] for o in objs], dtype=float)
    col_max = matrix.max(axis=0)
    for j in range(len(OBJECTIVES)):
        if col_max[j] == 0:
            assert np.all(norm[:, j] == 0.0)
        else:
            assert np.isclose(norm[:, j].max(), 1.0)


@settings(max_examples=100)
@given(events=st.lists(st.integers(min_value=0, max_value=4), min_size=0, max_size=2))
def test_property_13_fallback_equal_weights(events: list[int]) -> None:
    """Feature: pareto-commute-agent, Property 13: fewer than MIN_CHOICES events falls
    back to equal weights with insufficient_data true."""
    now = datetime(2026, 1, 1, 8, 0, 0)
    choice_events = [
        ChoiceEvent(
            query_id=f"q{i}",
            chosen_at=now + timedelta(minutes=i),
            chosen=Objectives(time_min=10, co2_g=100, crash_risk=1, disruption_risk=0.1),
            rejected=[Objectives(time_min=8, co2_g=200, crash_risk=1, disruption_risk=0.1)],
        )
        for i in events
    ]
    result = fit_weights(choice_events)
    assert result.insufficient_data is True
    assert result.n_choices == len(choice_events)
    assert result.weights == EQUAL_WEIGHTS


def test_fit_weights_are_a_simplex() -> None:
    """With at least MIN_CHOICES events, weights are non-negative and sum to 1."""
    now = datetime(2026, 1, 1, 8, 0, 0)
    events = [
        ChoiceEvent(
            query_id=f"q{i}",
            chosen_at=now + timedelta(minutes=i),
            chosen=Objectives(time_min=20, co2_g=50, crash_risk=1, disruption_risk=0.1),
            rejected=[Objectives(time_min=10, co2_g=500, crash_risk=2, disruption_risk=0.2)],
        )
        for i in range(4)
    ]
    result = fit_weights(events)
    assert result.insufficient_data is False
    assert all(v >= 0 for v in result.weights.values())
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_learning_direction_prefers_low_co2(tmp_path: Path) -> None:
    """Five events always picking the lowest-CO2 route at a time cost give
    weights['co2_g'] larger than weights['time_min']. After clear_choices,
    current_weights returns 0.25 each with insufficient_data true. (Req 10.2, 10.5)"""
    db = tmp_path / "preferences.db"
    now = datetime(2026, 1, 1, 8, 0, 0)
    for i in range(5):
        # chosen: slower but much lower CO2; rejected: faster but higher CO2.
        chosen = Objectives(time_min=30.0, co2_g=50.0, crash_risk=1.0, disruption_risk=0.1)
        rejected = Objectives(time_min=15.0, co2_g=800.0, crash_risk=1.0, disruption_risk=0.1)
        record_choice(
            ChoiceEvent(
                query_id=f"q{i}",
                chosen_at=now + timedelta(minutes=i),
                chosen=chosen,
                rejected=[rejected],
            ),
            db=db,
        )

    assert len(load_choices(db)) == 5
    weights = current_weights(db)
    assert weights.insufficient_data is False
    assert weights.n_choices == 5
    assert weights.weights["co2_g"] > weights.weights["time_min"]

    clear_choices(db)
    after = current_weights(db)
    assert after.insufficient_data is True
    assert after.n_choices == 0
    for o in OBJECTIVES:
        assert after.weights[o] == 0.25


def test_record_and_clear_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "preferences.db"
    assert load_choices(db) == []
    event = ChoiceEvent(
        query_id="q1",
        chosen_at=datetime(2026, 1, 1, 8, 0, 0),
        chosen=Objectives(time_min=10, co2_g=100, crash_risk=1, disruption_risk=0.1),
        rejected=[Objectives(time_min=8, co2_g=200, crash_risk=1, disruption_risk=0.2)],
    )
    record_choice(event, db=db)
    loaded = load_choices(db)
    assert len(loaded) == 1
    assert loaded[0].query_id == "q1"
    assert loaded[0].chosen.co2_g == 100
    clear_choices(db)
    assert load_choices(db) == []


def _choice(i: int, chosen: Objectives, rejected: Objectives) -> ChoiceEvent:
    return ChoiceEvent(
        query_id=f"q{i}",
        chosen_at=datetime(2026, 1, 1, 8, 0, 0) + timedelta(minutes=i),
        chosen=chosen,
        rejected=[rejected],
    )


def test_sessions_keep_separate_choices(tmp_path: Path) -> None:
    """Session a always picks low CO2, session b always picks low time. Each session's
    weights follow its own choices, and clearing one leaves the other intact."""
    db = tmp_path / "preferences.db"
    low_co2 = Objectives(time_min=30.0, co2_g=50.0, crash_risk=1.0, disruption_risk=0.1)
    low_time = Objectives(time_min=15.0, co2_g=800.0, crash_risk=1.0, disruption_risk=0.1)
    for i in range(5):
        record_choice(_choice(i, low_co2, low_time), db=db, session_id="a")
        record_choice(_choice(i, low_time, low_co2), db=db, session_id="b")

    weights_a = current_weights(db, "a")
    weights_b = current_weights(db, "b")
    assert weights_a.weights["co2_g"] > weights_a.weights["time_min"]
    assert weights_b.weights["time_min"] > weights_b.weights["co2_g"]
    assert current_weights(db).insufficient_data is True
    assert current_weights(db).n_choices == 0

    clear_choices(db, "a")
    assert current_weights(db, "a").insufficient_data is True
    assert current_weights(db, "b").n_choices == 5


def test_rows_from_before_sessions_still_load(tmp_path: Path) -> None:
    """A database with the original schema keeps its rows, which belong to the shared ""
    session, and new sessions can be written to it."""
    db = tmp_path / "preferences.db"
    old = _choice(
        0,
        Objectives(time_min=30.0, co2_g=50.0, crash_risk=1.0, disruption_risk=0.1),
        Objectives(time_min=15.0, co2_g=800.0, crash_risk=1.0, disruption_risk=0.1),
    )
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE choices (id INTEGER PRIMARY KEY, query_id TEXT, chosen_at TEXT, payload TEXT)"
    )
    conn.execute(
        "INSERT INTO choices (query_id, chosen_at, payload) VALUES (?, ?, ?)",
        (old.query_id, old.chosen_at.isoformat(), old.model_dump_json()),
    )
    conn.commit()
    conn.close()

    assert load_choices(db) == [old]
    assert load_choices(db, "a") == []
    record_choice(_choice(1, old.chosen, old.rejected[0]), db=db, session_id="a")
    assert len(load_choices(db)) == 1
    assert len(load_choices(db, "a")) == 1
    clear_choices(db)
    assert load_choices(db) == []
    assert len(load_choices(db, "a")) == 1
