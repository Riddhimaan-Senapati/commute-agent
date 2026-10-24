from collections.abc import Iterable
from pathlib import Path
from typing import get_args

import yaml

from commute_agent.models import EmissionFactor, LegMode
from commute_agent.paths import EMISSION_FACTORS

REQUIRED_MODES: tuple[LegMode, ...] = get_args(LegMode)
VALID_UNITS = ("g_per_passenger_km", "g_per_passenger_mile")


class EmissionFactorsError(Exception): ...


def load_emission_factors(path: Path = EMISSION_FACTORS) -> dict[LegMode, EmissionFactor]:
    """Load and validate the emission factors file.

    Raise EmissionFactorsError naming the path and required fields when the file is
    missing, and naming every offending mode and field when entries are missing or
    invalid.
    """
    if not path.exists():
        raise EmissionFactorsError(
            f"Emission factors file not found at {path}. Copy "
            f"data/emission_factors.example.yaml to {path} and give every mode a numeric "
            f"'value', a 'unit' of {VALID_UNITS[0]} or {VALID_UNITS[1]}, and a non-empty "
            f"'source'. Required modes: {', '.join(REQUIRED_MODES)}."
        )

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise EmissionFactorsError(
            f"Emission factors file {path} must be a mapping of mode to entry."
        )

    problems: list[str] = []
    factors: dict[LegMode, EmissionFactor] = {}
    for mode in REQUIRED_MODES:
        if mode not in raw:
            problems.append(f"{mode}: entry missing")
            continue
        entry = raw[mode]
        if not isinstance(entry, dict):
            problems.append(f"{mode}: entry is not a mapping")
            continue

        value = entry.get("value")
        unit = entry.get("unit")
        source = entry.get("source")

        mode_problems: list[str] = []
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            mode_problems.append("value must be numeric")
        if unit not in VALID_UNITS:
            mode_problems.append(f"unit must be one of {VALID_UNITS}")
        if not isinstance(source, str) or not source.strip():
            mode_problems.append("source must be a non-empty string")

        if mode_problems:
            problems.append(f"{mode}: {'; '.join(mode_problems)}")
            continue

        factors[mode] = EmissionFactor(value=float(value), unit=unit, source=source)

    if problems:
        raise EmissionFactorsError(f"Invalid emission factors in {path}: " + "; ".join(problems))

    return factors


def leg_co2_g(factors: dict[LegMode, EmissionFactor], mode: LegMode, distance_m: float) -> float:
    """CO2 grams for one leg: distance in the factor's unit times its value."""
    factor = factors[mode]
    if factor.unit == "g_per_passenger_km":
        return distance_m / 1000 * factor.value
    return distance_m / 1609.344 * factor.value


def co2_source(factors: dict[LegMode, EmissionFactor], modes: Iterable[LegMode]) -> str:
    """Source label joining each distinct mode in first-seen order."""
    seen: list[LegMode] = []
    for mode in modes:
        if mode not in seen:
            seen.append(mode)
    return "; ".join(f"emission_factors.yaml: {m} ({factors[m].source})" for m in seen)
