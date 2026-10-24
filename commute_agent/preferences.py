"""WS6. Preferences: learn which trade-offs the user picks and order routes by them.

Choices are stored in a SQLite database. A non-negative logistic model is fit on
per-query normalized objective differences between each rejected and the chosen
route. Fewer than MIN_CHOICES events falls back to equal weights.
"""

import sqlite3
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from commute_agent.models import (
    OBJECTIVES,
    ChoiceEvent,
    Objective,
    Objectives,
    PreferenceWeights,
    RouteOption,
)
from commute_agent.paths import PREFERENCES_DB

MIN_CHOICES = 3
L2_PENALTY = 0.1
EQUAL_WEIGHTS: dict[Objective, float] = {o: 0.25 for o in OBJECTIVES}


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS choices "
        "(id INTEGER PRIMARY KEY, query_id TEXT, chosen_at TEXT, payload TEXT)"
    )
    return conn


def record_choice(event: ChoiceEvent, db: Path = PREFERENCES_DB) -> None:
    """Store a Choice_Event. payload is event.model_dump_json()."""
    with _connect(db) as conn:
        conn.execute(
            "INSERT INTO choices (query_id, chosen_at, payload) VALUES (?, ?, ?)",
            (event.query_id, event.chosen_at.isoformat(), event.model_dump_json()),
        )


def load_choices(db: Path = PREFERENCES_DB) -> list[ChoiceEvent]:
    """Return every stored Choice_Event in insertion order."""
    with _connect(db) as conn:
        rows = conn.execute("SELECT payload FROM choices ORDER BY id").fetchall()
    return [ChoiceEvent.model_validate_json(payload) for (payload,) in rows]


def clear_choices(db: Path = PREFERENCES_DB) -> None:
    """Delete all Choice_Events."""
    with _connect(db) as conn:
        conn.execute("DELETE FROM choices")


def _vector(obj: Objectives) -> list[float]:
    return [getattr(obj, o) for o in OBJECTIVES]


def normalize(vectors: list[Objectives]) -> np.ndarray:
    """Shape (n, 4). Divide each column by its max over the n vectors; a column whose
    max is 0 stays 0."""
    matrix = np.array([_vector(v) for v in vectors], dtype=float)
    if matrix.size == 0:
        return matrix.reshape(0, len(OBJECTIVES))
    col_max = matrix.max(axis=0)
    safe = np.where(col_max == 0, 1.0, col_max)
    out = matrix / safe
    out[:, col_max == 0] = 0.0
    return out


def fit_weights(events: list[ChoiceEvent]) -> PreferenceWeights:
    """Fewer than MIN_CHOICES events -> EQUAL_WEIGHTS, insufficient_data True.

    Else rows x = norm(rejected) - norm(chosen), normalized per event over chosen +
    rejected. Minimize sum(log1p(exp(-x @ w))) + L2_PENALTY * w @ w with
    scipy.optimize.minimize, method L-BFGS-B, bounds (0, None) for each weight,
    x0 = 0.25 each. Return w / w.sum(), or EQUAL_WEIGHTS when w.sum() is 0.
    """
    n = len(OBJECTIVES)
    if len(events) < MIN_CHOICES:
        return PreferenceWeights(
            weights=dict(EQUAL_WEIGHTS), n_choices=len(events), insufficient_data=True
        )

    rows: list[np.ndarray] = []
    for event in events:
        norm = normalize([event.chosen, *event.rejected])
        chosen = norm[0]
        for rejected in norm[1:]:
            rows.append(rejected - chosen)
    x = np.array(rows, dtype=float).reshape(-1, n)

    def objective(w: np.ndarray) -> float:
        margins = x @ w
        return float(np.sum(np.log1p(np.exp(-margins))) + L2_PENALTY * (w @ w))

    result = minimize(
        objective,
        x0=np.full(n, 0.25),
        method="L-BFGS-B",
        bounds=[(0, None)] * n,
    )
    w = np.asarray(result.x, dtype=float)
    total = w.sum()
    if total == 0:
        return PreferenceWeights(
            weights=dict(EQUAL_WEIGHTS), n_choices=len(events), insufficient_data=False
        )
    normalized = w / total
    weights = {o: float(normalized[i]) for i, o in enumerate(OBJECTIVES)}
    return PreferenceWeights(weights=weights, n_choices=len(events), insufficient_data=False)


def order_routes(routes: list[RouteOption], weights: PreferenceWeights) -> list[RouteOption]:
    """Sort by weighted sum of normalize([r.objectives ...]) rows, ties by
    (time_min, id). Same members."""
    if not routes:
        return []
    norm = normalize([r.objectives for r in routes])
    w = np.array([weights.weights[o] for o in OBJECTIVES], dtype=float)
    scores = norm @ w
    indexed = list(enumerate(routes))
    indexed.sort(key=lambda pair: (float(scores[pair[0]]), pair[1].objectives.time_min, pair[1].id))
    return [route for _, route in indexed]


def current_weights(db: Path = PREFERENCES_DB) -> PreferenceWeights:
    """fit_weights(load_choices(db))."""
    return fit_weights(load_choices(db))
