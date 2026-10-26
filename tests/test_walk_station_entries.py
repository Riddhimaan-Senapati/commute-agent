"""Tests for the walk station-entries exposure term, on synthetic inputs."""

import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from commute_agent.ingest.ridership import station_entries
from commute_agent.streets.build import _walk_calibration
from commute_agent.streets.exposure import (
    MIN_MATCHED_COUNTERS,
    fit_calibration,
    fit_linear,
    station_entry_term,
)


def test_fit_linear_one_column_matches_fit_calibration():
    proxy = np.array([1.0, 2.0, 4.0, 5.0])
    observed = np.array([3.0, 5.0, 7.0, 9.0])
    coef, r2 = fit_linear(proxy[:, None], observed)
    scale, r2_single = fit_calibration(proxy, observed)
    assert coef[0] == pytest.approx(scale)
    assert r2 == pytest.approx(r2_single)


def test_fit_linear_recovers_two_coefficients():
    rng = np.random.default_rng(0)
    x1 = rng.uniform(1, 10, 30)
    x2 = rng.uniform(1, 10, 30)
    observed = 2.0 * x1 + 3.0 * x2
    coef, r2 = fit_linear(np.column_stack([x1, x2]), observed)
    assert coef == pytest.approx([2.0, 3.0])
    assert r2 == pytest.approx(1.0)


def test_station_entry_term_decays_linearly_to_zero_at_radius():
    station = np.array([[0.0, 0.0]])
    entries = np.array([100.0])
    radius = 800.0
    xs = np.array([0.0, 400.0, 800.0, 1600.0])
    ys = np.zeros(4)
    term = station_entry_term(xs, ys, station, entries, radius_m=radius)
    assert term == pytest.approx([100.0, 50.0, 0.0, 0.0])


def test_station_entry_term_sums_stations_in_range():
    stations = np.array([[0.0, 0.0], [0.0, 400.0]])
    entries = np.array([100.0, 60.0])
    term = station_entry_term(np.array([0.0]), np.array([0.0]), stations, entries, radius_m=800.0)
    # Distance 0 to the first station (100) and 400 m to the second (60 * 0.5 = 30).
    assert term == pytest.approx([130.0])


def test_station_entry_term_without_stations_is_zero():
    term = station_entry_term(
        np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.empty((0, 2)), np.array([])
    )
    assert term.tolist() == [0.0, 0.0]


def _synthetic_walk_inputs(n: int, with_station_signal: bool):
    """n walk edges with counters; volume follows the station term when asked."""
    rng = np.random.default_rng(1)
    highway = pd.Series(["residential"] * n)
    station_term = rng.uniform(0, 1000, n)
    noise = rng.normal(0, 1, n)
    if with_station_signal:
        volume = 0.5 * station_term + noise
    else:
        volume = rng.uniform(50, 60, n)
    measured = np.ones(n, dtype=bool)
    return highway, volume, measured, station_term


def test_walk_calibration_adopts_when_station_terms_explain_counts():
    highway, volume, measured, station_term = _synthetic_walk_inputs(
        MIN_MATCHED_COUNTERS + 5, with_station_signal=True
    )
    report, coef = _walk_calibration(highway, volume, measured, station_term)
    assert coef is not None
    assert report["matched_edges"] == MIN_MATCHED_COUNTERS + 5
    assert report["r_squared_after"] > report["r_squared_before"]
    assert report["adjusted_r_squared_after"] > report["adjusted_r_squared_before"]
    assert report["coefficients"]["station_entries_per_day"] > 0


def test_walk_calibration_not_adopted_when_station_coefficient_negative():
    highway, volume, measured, station_term = _synthetic_walk_inputs(
        MIN_MATCHED_COUNTERS + 5, with_station_signal=True
    )
    # Counts rise as the station term falls, so the station coefficient must be negative.
    report, _ = _walk_calibration(highway, volume, measured, 2000.0 - station_term)
    assert report["coefficients"]["station_entries_per_day"] < 0
    assert report["adopted"] is False


def test_walk_calibration_not_adopted_without_station_signal():
    highway, volume, measured, station_term = _synthetic_walk_inputs(
        MIN_MATCHED_COUNTERS + 5, with_station_signal=False
    )
    report, _ = _walk_calibration(highway, volume, measured, station_term)
    assert report["adopted"] is False
    assert report["r_squared_after"] is not None


def test_walk_calibration_not_fitted_below_minimum_counters():
    highway, volume, measured, station_term = _synthetic_walk_inputs(
        MIN_MATCHED_COUNTERS - 1, with_station_signal=True
    )
    report, _ = _walk_calibration(highway, volume, measured, station_term)
    assert report["adopted"] is False
    assert report["r_squared_before"] is None
    assert "need" in report["decision"]


def test_station_entries_ingest_means_per_service_day(tmp_path):
    raw = tmp_path / "raw"
    processed = tmp_path / "processed"
    (raw / "ridership").mkdir(parents=True)
    (raw / "transit").mkdir(parents=True)
    processed.mkdir()

    gse = (
        "service_date,time_period,station_name,route_or_line,gated_entries,stop_id\n"
        "2025-01-01,(08:00:00),Alpha,Red Line,10,place-alpha\n"
        "2025-01-01,(09:00:00),Alpha,Red Line,20,place-alpha\n"
        "2025-01-02,(08:00:00),Alpha,Red Line,30,place-alpha\n"
        "2025-01-01,(08:00:00),Beta,Green Line,5,place-beta\n"
        "2025-01-01,(08:00:00),Gamma,Green Line,7,place-gamma\n"
    )
    with zipfile.ZipFile(raw / "ridership" / "GSE_by_year.zip", "w") as z:
        z.writestr("GSE_by_year/GSE_2025.csv", gse)
    stops = (
        "stop_id,stop_lat,stop_lon,location_type\n"
        "place-alpha,42.35,-71.06,1\n"
        "place-beta,42.36,-71.07,1\n"
        "place-alpha-platform,42.35,-71.06,0\n"
    )
    with zipfile.ZipFile(raw / "transit" / "MBTA_GTFS.zip", "w") as z:
        z.writestr("stops.txt", stops)
    for group, name in [("ridership", "GSE_by_year.zip"), ("transit", "MBTA_GTFS.zip")]:
        entry = {
            "file": f"{group}/{name}",
            "source_url": "https://example.test/synthetic",
            "license": "synthetic",
        }
        (raw / group / "MANIFEST.json").write_text(json.dumps({"files": [entry]}))

    path = station_entries(raw, processed, year=2025)
    out = pd.read_parquet(path).set_index("stop_id")
    assert list(out.index) == ["place-alpha", "place-beta"]  # gamma has no coordinates
    assert out.loc["place-alpha", "entries_per_day"] == pytest.approx(60 / 2)
    assert out.loc["place-alpha", "service_days"] == 2
    assert out.loc["place-beta", "lat"] == pytest.approx(42.36)
    assert (processed / "station_entries.parquet.meta.json").exists()
