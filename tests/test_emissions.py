from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from commute_agent.emissions import (
    EmissionFactorsError,
    co2_source,
    leg_co2_g,
    load_emission_factors,
)
from commute_agent.models import EmissionFactor

ALL_MODES = [
    "walk",
    "bike",
    "bluebikes",
    "drive",
    "subway",
    "light_rail",
    "bus",
    "commuter_rail",
    "ferry",
]


def _valid_mapping() -> dict:
    return {
        m: {"value": 50.0, "unit": "g_per_passenger_km", "source": f"src-{m}"} for m in ALL_MODES
    }


def _write_yaml(path: Path, mapping: dict) -> Path:
    path.write_text(yaml.safe_dump(mapping))
    return path


def test_load_valid(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path / "ef.yaml", _valid_mapping())
    factors = load_emission_factors(path)
    assert set(factors) == set(ALL_MODES)
    assert factors["bike"] == EmissionFactor(
        value=50.0, unit="g_per_passenger_km", source="src-bike"
    )


# Property 8: CO2 is linear in distance. Validates Requirements 3.4.
@given(
    distance=st.floats(min_value=0.0, max_value=1_000_000, allow_nan=False, allow_infinity=False),
    scale=st.floats(min_value=0.1, max_value=1000, allow_nan=False),
    value=st.floats(min_value=0.0, max_value=1000, allow_nan=False),
)
def test_co2_linear_in_distance(distance: float, scale: float, value: float) -> None:
    factors = {"bike": EmissionFactor(value=value, unit="g_per_passenger_km", source="s")}
    base = leg_co2_g(factors, "bike", distance)
    scaled = leg_co2_g(factors, "bike", distance * scale)
    assert scaled == pytest.approx(base * scale, rel=1e-9, abs=1e-9)


def test_co2_unit_conversion() -> None:
    km = {"bike": EmissionFactor(value=100.0, unit="g_per_passenger_km", source="s")}
    mile = {"bike": EmissionFactor(value=100.0, unit="g_per_passenger_mile", source="s")}
    assert leg_co2_g(km, "bike", 1000) == pytest.approx(100.0)
    assert leg_co2_g(mile, "bike", 1609.344) == pytest.approx(100.0)


def test_missing_file_names_path_and_fields(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(EmissionFactorsError) as exc:
        load_emission_factors(missing)
    msg = str(exc.value)
    assert str(missing) in msg
    assert "value" in msg and "unit" in msg and "source" in msg


def test_missing_mode_named(tmp_path: Path) -> None:
    mapping = _valid_mapping()
    del mapping["ferry"]
    path = _write_yaml(tmp_path / "ef.yaml", mapping)
    with pytest.raises(EmissionFactorsError) as exc:
        load_emission_factors(path)
    assert "ferry" in str(exc.value)


def test_null_value_named(tmp_path: Path) -> None:
    mapping = _valid_mapping()
    mapping["subway"]["value"] = None
    path = _write_yaml(tmp_path / "ef.yaml", mapping)
    with pytest.raises(EmissionFactorsError) as exc:
        load_emission_factors(path)
    assert "subway" in str(exc.value) and "value" in str(exc.value)


def test_unsupported_unit_named(tmp_path: Path) -> None:
    mapping = _valid_mapping()
    mapping["drive"]["unit"] = "g_per_mile"
    path = _write_yaml(tmp_path / "ef.yaml", mapping)
    with pytest.raises(EmissionFactorsError) as exc:
        load_emission_factors(path)
    assert "drive" in str(exc.value) and "unit" in str(exc.value)


def test_empty_source_named(tmp_path: Path) -> None:
    mapping = _valid_mapping()
    mapping["bus"]["source"] = ""
    path = _write_yaml(tmp_path / "ef.yaml", mapping)
    with pytest.raises(EmissionFactorsError) as exc:
        load_emission_factors(path)
    assert "bus" in str(exc.value) and "source" in str(exc.value)


def test_co2_source_first_seen_order() -> None:
    factors = {
        "walk": EmissionFactor(value=0.0, unit="g_per_passenger_km", source="w"),
        "bike": EmissionFactor(value=5.0, unit="g_per_passenger_km", source="b"),
    }
    label = co2_source(factors, ["bike", "walk", "bike"])
    assert label == ("emission_factors.yaml: bike (b); emission_factors.yaml: walk (w)")
