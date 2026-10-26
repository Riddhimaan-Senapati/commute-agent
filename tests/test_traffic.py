from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from commute_agent.models import TrafficFactor
from commute_agent.paths import TRAFFIC_FACTORS
from commute_agent.traffic import (
    MISSING_NOTE,
    TrafficFactorsError,
    free_flow_factors,
    load_traffic_factors,
)

BUCKETS = ["0-6", "6-7", "7-9", "9-15", "15-16", "16-19", "19-24"]


def _valid_mapping() -> dict:
    """Weekday factors step up by 0.1 per bucket and weekend ones by 0.01, so each
    bucket has a distinct value."""
    return {
        "weekday": {
            key: {"value": 1.0 + 0.1 * n, "source": f"weekday src {key}"}
            for n, key in enumerate(BUCKETS)
        },
        "weekend": {
            key: {"value": 1.0 + 0.01 * n, "source": f"weekend src {key}"}
            for n, key in enumerate(BUCKETS)
        },
    }


def _write_yaml(path: Path, mapping: dict) -> Path:
    path.write_text(yaml.safe_dump(mapping))
    return path


def _load(tmp_path: Path, mapping: dict):
    return load_traffic_factors(_write_yaml(tmp_path / "traffic.yaml", mapping))


def _ny(*args) -> datetime:
    """A naive America/New_York wall-clock time, which factor_for treats as local."""
    return datetime(*args)


def test_load_valid_keeps_every_bucket(tmp_path: Path) -> None:
    factors = _load(tmp_path, _valid_mapping())
    assert factors.note is None
    assert len(factors.buckets) == 2 * len(BUCKETS)
    weekday_1619 = next(
        b for b in factors.buckets if b.day_type == "weekday" and b.label == "16-19"
    )
    assert weekday_1619.factor == TrafficFactor(value=1.5, source="weekday src 16-19")


def test_missing_file_is_free_flow_with_note(tmp_path: Path) -> None:
    factors = load_traffic_factors(tmp_path / "nope.yaml")
    assert factors.note == MISSING_NOTE
    for when in (_ny(2026, 10, 27, 8, 15), _ny(2026, 10, 31, 17, 0)):
        bucket = factors.factor_for(when)
        assert bucket.factor.value == 1.0
        assert bucket.factor.source == "needs a source"


def test_repo_traffic_file_is_hourly_and_sourced() -> None:
    factors = load_traffic_factors(TRAFFIC_FACTORS)
    assert factors.note is None
    assert len(factors.buckets) == 2 * 24
    assert all(b.factor.source.startswith("TomTom Traffic Index 2025") for b in factors.buckets)
    rush = factors.factor_for(datetime(2026, 10, 27, 17, 15, tzinfo=ZoneInfo("America/New_York")))
    assert (rush.label, rush.factor.value) == ("17-18", 1.84)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda m: m["weekday"]["16-19"].update(value=0.9), "weekday 16-19: value must be"),
        (lambda m: m["weekday"]["16-19"].update(value=None), "weekday 16-19: value must be"),
        (lambda m: m["weekday"]["16-19"].update(value=True), "weekday 16-19: value must be"),
        (lambda m: m["weekend"]["7-9"].update(value=float("nan")), "weekend 7-9: value must be"),
        (lambda m: m["weekend"]["7-9"].update(source=""), "weekend 7-9: source must be"),
        (lambda m: m["weekend"]["7-9"].pop("source"), "weekend 7-9: source must be"),
        (
            lambda m: m["weekend"]["7-9"].update(value=1.2, source="needs a source"),
            "weekend 7-9: value must be 1.0 while source is 'needs a source'",
        ),
        (lambda m: m["weekend"].pop("0-6"), "weekend: hour buckets must cover 0-24"),
        (lambda m: m["weekday"].pop("19-24"), "weekday: hour buckets must cover 0-24"),
        (lambda m: m["weekday"].pop("9-15"), "weekday: hour buckets must cover 0-24 with no gaps"),
        (
            lambda m: m["weekday"].update({"8-10": {"value": 1.0, "source": "x"}}),
            "weekday: hour buckets must cover 0-24 with no gaps",
        ),
        (
            lambda m: m["weekday"].update({"16:19": {"value": 1.0, "source": "x"}}),
            "weekday 16:19: hour bucket must look like",
        ),
        (
            lambda m: m["weekday"].update({"19-25": {"value": 1.0, "source": "x"}}),
            "weekday 19-25: hour bucket must satisfy",
        ),
        (lambda m: m.pop("weekend"), "weekend: section missing"),
        (lambda m: m.update(weekday="flat"), "weekday: section is not a mapping"),
    ],
)
def test_invalid_file_names_the_problem(tmp_path: Path, mutate, expected: str) -> None:
    mapping = _valid_mapping()
    mutate(mapping)
    path = _write_yaml(tmp_path / "traffic.yaml", mapping)
    with pytest.raises(TrafficFactorsError) as exc:
        load_traffic_factors(path)
    message = str(exc.value)
    assert str(path) in message
    assert expected in message


def test_top_level_list_is_rejected(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path / "traffic.yaml", ["weekday", "weekend"])  # type: ignore[arg-type]
    with pytest.raises(TrafficFactorsError, match="mapping of day type"):
        load_traffic_factors(path)


@pytest.mark.parametrize(
    "when, label, day_type",
    [
        (_ny(2026, 10, 27, 0, 0), "0-6", "weekday"),  # Tuesday midnight
        (_ny(2026, 10, 27, 5, 59), "0-6", "weekday"),
        (_ny(2026, 10, 27, 6, 0), "6-7", "weekday"),
        (_ny(2026, 10, 27, 6, 59), "6-7", "weekday"),
        (_ny(2026, 10, 27, 7, 0), "7-9", "weekday"),
        (_ny(2026, 10, 27, 8, 15), "7-9", "weekday"),
        (_ny(2026, 10, 27, 8, 59), "7-9", "weekday"),
        (_ny(2026, 10, 27, 9, 0), "9-15", "weekday"),
        (_ny(2026, 10, 27, 14, 59), "9-15", "weekday"),
        (_ny(2026, 10, 27, 15, 0), "15-16", "weekday"),
        (_ny(2026, 10, 27, 15, 59), "15-16", "weekday"),
        (_ny(2026, 10, 27, 16, 0), "16-19", "weekday"),
        (_ny(2026, 10, 27, 18, 59), "16-19", "weekday"),
        (_ny(2026, 10, 27, 19, 0), "19-24", "weekday"),
        (_ny(2026, 10, 27, 23, 59), "19-24", "weekday"),
        # Weekend: Saturday 2026-10-31 and Sunday 2026-11-01 use the weekend table.
        (_ny(2026, 10, 31, 8, 0), "7-9", "weekend"),
        (_ny(2026, 10, 31, 17, 0), "16-19", "weekend"),
        (_ny(2026, 11, 1, 0, 0), "0-6", "weekend"),
        (_ny(2026, 11, 1, 23, 30), "19-24", "weekend"),
    ],
)
def test_bucket_boundaries(tmp_path: Path, when: datetime, label: str, day_type: str) -> None:
    factors = _load(tmp_path, _valid_mapping())
    bucket = factors.factor_for(when)
    assert (bucket.day_type, bucket.label) == (day_type, label)
    expected_table = _valid_mapping()[day_type][label]
    assert bucket.factor.value == pytest.approx(expected_table["value"])


def test_aware_time_converts_to_new_york(tmp_path: Path) -> None:
    factors = _load(tmp_path, _valid_mapping())
    # 21:30 UTC on 27 Oct 2026 is 17:30 EDT, so the 16-19 bucket.
    bucket = factors.factor_for(datetime(2026, 10, 27, 21, 30, tzinfo=UTC))
    assert (bucket.day_type, bucket.label) == ("weekday", "16-19")
    # 02:30 UTC on 28 Oct is 22:30 EDT on Tuesday 27 Oct, so the 19-24 bucket.
    late = factors.factor_for(datetime(2026, 10, 28, 2, 30, tzinfo=UTC))
    assert (late.day_type, late.label) == ("weekday", "19-24")


def test_free_flow_factors_cover_every_hour() -> None:
    factors = free_flow_factors()
    for hour in range(24):
        bucket = factors.factor_for(_ny(2026, 10, 27, hour, 0))
        assert bucket.factor.value == 1.0
