"""Time-of-day traffic factors for drive times (f6).

``data/traffic_factors.yaml`` gives a multiplier (>= 1.0) on free-flow drive time for each
weekday and weekend hour bucket. ``factor_for`` picks the bucket that holds a departure in
America/New_York local time. A missing file gives 1.0 everywhere with a note; an invalid
file raises TrafficFactorsError.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from commute_agent.models import TrafficFactor
from commute_agent.paths import TRAFFIC_FACTORS

TIMEZONE = ZoneInfo("America/New_York")
DAY_TYPES = ("weekday", "weekend")
UNSOURCED = "needs a source"
ALL_DAY = "all day"
MISSING_NOTE = (
    "Traffic factors file not found; drive times use a factor of 1.0 (free-flow). "
    "Add data/traffic_factors.yaml to apply time-of-day factors."
)
_BUCKET_KEY = re.compile(r"(\d{1,2})-(\d{1,2})")


class TrafficFactorsError(Exception): ...


@dataclass(frozen=True)
class TrafficBucket:
    """One hour bucket on one day type: hours ``start`` (inclusive) to ``end`` (exclusive)."""

    day_type: str
    label: str
    start: int
    end: int
    factor: TrafficFactor


@dataclass(frozen=True)
class TrafficFactors:
    buckets: tuple[TrafficBucket, ...]
    note: str | None = None

    def factor_for(self, depart_at: datetime) -> TrafficBucket:
        """The bucket for a departure, in America/New_York local time. Naive times are local."""
        local = (
            depart_at.replace(tzinfo=TIMEZONE)
            if depart_at.tzinfo is None
            else depart_at.astimezone(TIMEZONE)
        )
        day_type = "weekend" if local.weekday() >= 5 else "weekday"
        for bucket in self.buckets:
            if bucket.day_type == day_type and bucket.start <= local.hour < bucket.end:
                return bucket
        raise TrafficFactorsError(f"No {day_type} traffic bucket covers hour {local.hour}")


def free_flow_factors(note: str | None = None) -> TrafficFactors:
    """Every day type and hour at 1.0, for callers with no traffic file."""
    unsourced = TrafficFactor(value=1.0, source=UNSOURCED)
    buckets = tuple(TrafficBucket(day_type, ALL_DAY, 0, 24, unsourced) for day_type in DAY_TYPES)
    return TrafficFactors(buckets=buckets, note=note)


def load_traffic_factors(path: Path = TRAFFIC_FACTORS) -> TrafficFactors:
    """Load and validate the traffic factors file.

    A missing file returns free_flow_factors() with a note. Raise TrafficFactorsError naming
    the path and every offending day type, hour bucket and field when the file is invalid.
    """
    if not path.exists():
        return free_flow_factors(note=MISSING_NOTE)

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise TrafficFactorsError(
            f"Traffic factors file {path} must be a mapping of day type to hour buckets."
        )

    problems: list[str] = []
    buckets: list[TrafficBucket] = []
    for day_type in DAY_TYPES:
        if day_type not in raw:
            problems.append(f"{day_type}: section missing")
            continue
        section = raw[day_type]
        if not isinstance(section, dict):
            problems.append(f"{day_type}: section is not a mapping")
            continue

        ranges: list[TrafficBucket] = []
        for key, entry in section.items():
            label = str(key)
            match = _BUCKET_KEY.fullmatch(label)
            if match is None:
                problems.append(f"{day_type} {label}: hour bucket must look like 16-19")
                continue
            start, end = int(match.group(1)), int(match.group(2))
            if not 0 <= start < end <= 24:
                problems.append(
                    f"{day_type} {label}: hour bucket must satisfy 0 <= start < end <= 24"
                )
                continue
            if not isinstance(entry, dict):
                problems.append(f"{day_type} {label}: entry is not a mapping")
                continue

            value = entry.get("value")
            source = entry.get("source")
            entry_problems: list[str] = []
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 1.0
            ):
                entry_problems.append("value must be a number >= 1.0")
            if not isinstance(source, str) or not source.strip():
                entry_problems.append("source must be a non-empty string")
            if not entry_problems and source == UNSOURCED and value != 1.0:
                entry_problems.append(f"value must be 1.0 while source is '{UNSOURCED}'")
            if entry_problems:
                problems.append(f"{day_type} {label}: {'; '.join(entry_problems)}")
                continue

            factor = TrafficFactor(value=float(value), source=source)
            ranges.append(TrafficBucket(day_type, label, start, end, factor))

        ranges.sort(key=lambda bucket: bucket.start)
        covered_to = 0
        for bucket in ranges:
            if bucket.start != covered_to:
                problems.append(
                    f"{day_type}: hour buckets must cover 0-24 with no gaps or overlaps "
                    f"(problem at {bucket.label})"
                )
                break
            covered_to = bucket.end
        else:
            if covered_to != 24:
                problems.append(f"{day_type}: hour buckets must cover 0-24 (ends at {covered_to})")
        buckets.extend(ranges)

    if problems:
        raise TrafficFactorsError(f"Invalid traffic factors in {path}: " + "; ".join(problems))

    return TrafficFactors(buckets=tuple(buckets))


__all__ = [
    "DAY_TYPES",
    "MISSING_NOTE",
    "TrafficBucket",
    "TrafficFactors",
    "TrafficFactorsError",
    "free_flow_factors",
    "load_traffic_factors",
]
