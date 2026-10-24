"""Tests for commute_agent.streets.risk."""

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from commute_agent.streets.risk import (
    RISK_SCALE,
    SHRINKAGE_K,
    edge_risk,
    mean_crashes_per_km,
)


def test_mean_crashes_per_km_literal():
    crashes = np.array([2.0, 1.0, 0.0])
    lengths_m = np.array([500.0, 500.0, 1000.0])  # 2 km total
    assert mean_crashes_per_km(crashes, lengths_m) == 1.5


def test_edge_risk_covered_literal():
    crashes = np.array([2.0])
    lengths_m = np.array([1000.0])  # 1 km
    exposure = np.array([100.0])
    covered = np.array([True])
    p = 0.5
    # (2 + 1.0 * 0.5 * 1.0) / 100 * 1000 = 25.0
    risk = edge_risk(crashes, lengths_m, exposure, covered, p)
    assert risk[0] == 25.0


def test_edge_risk_uncovered_drops_crash_term():
    crashes = np.array([2.0])
    lengths_m = np.array([1000.0])
    exposure = np.array([100.0])
    covered = np.array([False])
    p = 0.5
    # crash term dropped: (0 + 0.5 * 1.0) / 100 * 1000 = 5.0
    risk = edge_risk(crashes, lengths_m, exposure, covered, p)
    assert risk[0] == 5.0


# Feature: pareto-commute-agent, Property 9: Risk is monotone
@settings(max_examples=200)
@given(
    crashes=st.floats(min_value=0, max_value=50),
    length_m=st.floats(min_value=1.0, max_value=5000.0),
    exposure=st.floats(min_value=1.0, max_value=10000.0),
    p=st.floats(min_value=0.0, max_value=10.0),
    extra_exposure=st.floats(min_value=0.0, max_value=10000.0),
)
def test_property_9_risk_monotone(crashes, length_m, exposure, p, extra_exposure):
    """Adding one crash does not lower an edge's risk, and raising its exposure
    does not raise its risk (covered edges)."""
    c = np.array([crashes])
    length = np.array([length_m])
    ex = np.array([exposure])
    covered = np.array([True])

    base = edge_risk(c, length, ex, covered, p)[0]

    # One more crash must not lower risk.
    more_crashes = edge_risk(c + 1.0, length, ex, covered, p)[0]
    assert more_crashes >= base - 1e-9

    # Higher exposure must not raise risk.
    higher_exposure = edge_risk(c, length, ex + extra_exposure, covered, p)[0]
    assert higher_exposure <= base + 1e-9


def test_property_9_holds_for_uncovered_edges():
    """Monotonicity in exposure also holds when the crash term is dropped."""
    c = np.array([3.0])
    length = np.array([800.0])
    covered = np.array([False])
    p = 0.4
    low = edge_risk(c, length, np.array([50.0]), covered, p)[0]
    high = edge_risk(c, length, np.array([200.0]), covered, p)[0]
    assert high <= low
    # Adding crashes to an uncovered edge changes nothing (crash term dropped).
    assert edge_risk(c + 5.0, length, np.array([50.0]), covered, p)[0] == low


def test_constants():
    assert SHRINKAGE_K == 1.0
    assert RISK_SCALE == 1000.0
