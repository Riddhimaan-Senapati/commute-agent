"""Crash-risk math. Pure functions over NumPy arrays, no file I/O."""

import numpy as np

SNAP_MAX_M = 30.0
SHRINKAGE_K = 1.0
RISK_SCALE = 1000.0


def mean_crashes_per_km(crashes: np.ndarray, lengths_m: np.ndarray) -> float:
    """Network mean crashes per km: crashes.sum() / (lengths_m.sum() / 1000)."""
    return float(crashes.sum() / (lengths_m.sum() / 1000))


def edge_risk(
    crashes: np.ndarray,
    lengths_m: np.ndarray,
    exposure: np.ndarray,
    covered: np.ndarray,
    p: float,
) -> np.ndarray:
    """Vectorized per-edge risk.

    risk = (crashes + SHRINKAGE_K * p * length_km) / exposure * RISK_SCALE.
    For edges where ``covered`` is False the crash term is 0 and only the
    SHRINKAGE_K * p * length_km prior term remains.
    """
    length_km = lengths_m / 1000
    crash_term = np.where(covered, crashes, 0.0)
    numerator = crash_term + SHRINKAGE_K * p * length_km
    return numerator / exposure * RISK_SCALE
