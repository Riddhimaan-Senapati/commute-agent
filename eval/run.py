"""Evaluation suite: `uv run python -m eval.run [--model a,b]`.

Writes eval/results.md and eval/results.json. Metric math lives in pure
functions over models.py types so tests can call them on literal inputs; the
runners below only gather candidates and agent replies and hand them over.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from mcp import StdioServerParameters
from shapely.geometry import shape
from shapely.ops import unary_union
from smolagents import MCPClient

from commute_agent.agent import (
    Conversation,
    build_agent,
    load_env,
    missing_model_keys,
    model_config,
    run_turn,
)
from commute_agent.emissions import EmissionFactorsError, load_emission_factors
from commute_agent.grounding import numbers_in
from commute_agent.models import AgentReply, PreferenceWeights, RouteKind, RouteOption
from commute_agent.pareto import dominates, near_duplicate, pareto_front
from commute_agent.paths import ALERT_SNAPSHOTS, EMISSION_FACTORS, EVAL, PROCESSED, ROOT
from commute_agent.planner import OutsideServiceArea, PlannerContext, gather_candidates
from commute_agent.preferences import EQUAL_WEIGHTS, order_routes
from commute_agent.streets import load_street_graphs
from commute_agent.transit.alerts import load_alerts
from commute_agent.transit.cache_trips import (
    Trip,
    load_od_pairs,
    load_showcase_trips,
    next_departure,
)

ALERT_EXPECTATIONS = EVAL / "alert_expectations.yaml"
PROMPTS = EVAL / "prompts.yaml"
OD_PAIRS = EVAL / "od_pairs.yaml"
NO_ALERTS = "none"
IDENTITY_PHRASE = "independent prototype"  # inside agent.IDENTITY_SENTENCE
SAFETY_KINDS: tuple[RouteKind, ...] = ("walk", "bike")
EXPOSURE_KINDS: frozenset[RouteKind] = frozenset({"walk", "bike"})
MCP_CONNECT_TIMEOUT_S = 120  # the server loads and projects three street graphs first


# --------------------------------------------------------------------------- pure metrics


def soundness_failures(front: list[RouteOption], candidates: list[RouteOption]) -> list[str]:
    """Front member ids dominated by some candidate."""
    return [
        member.id
        for member in front
        if any(dominates(c.objectives, member.objectives) for c in candidates if c is not member)
    ]


def completeness_failures(front: list[RouteOption], candidates: list[RouteOption]) -> list[str]:
    """Undominated candidate ids that are neither in the front nor near a member."""
    front_ids = {member.id for member in front}
    missing = []
    for route in candidates:
        if route.id in front_ids:
            continue
        if any(dominates(c.objectives, route.objectives) for c in candidates if c is not route):
            continue
        if any(near_duplicate(route.objectives, member.objectives) for member in front):
            continue
        missing.append(route.id)
    return missing


def safety_vs_speed(front: list[RouteOption], kind: RouteKind) -> tuple[float, float] | None:
    """(percent crash-risk reduction, minutes added) of the safest member of ``kind`` versus
    the fastest member of ``kind``. None when the front has no member of that kind; (0, 0)
    when it has one, or when the fastest member's crash risk is 0."""
    members = [route for route in front if route.kind == kind]
    if not members:
        return None
    fastest = min(members, key=lambda r: (r.objectives.time_min, r.objectives.crash_risk, r.id))
    safest = min(members, key=lambda r: (r.objectives.crash_risk, r.objectives.time_min, r.id))
    base = fastest.objectives.crash_risk
    reduction = 0.0 if base == 0 else 100.0 * (base - safest.objectives.crash_risk) / base
    return reduction, safest.objectives.time_min - fastest.objectives.time_min


@dataclass(frozen=True)
class Spread:
    median: float | None
    q1: float | None
    q3: float | None
    iqr: float | None
    n: int


def median_iqr(values: list[float]) -> Spread:
    """Median and interquartile range with inclusive quartiles. All None when values is empty."""
    if not values:
        return Spread(None, None, None, None, 0)
    if len(values) == 1:
        v = float(values[0])
        return Spread(v, v, v, 0.0, 1)
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return Spread(median, q1, q3, q3 - q1, len(values))


@dataclass(frozen=True)
class AlertExpectation:
    trip: str
    source: str
    affected_lines: list[str]
    pareto_must_change: bool
    disrupted_must_rank_lower: bool = False


@dataclass(frozen=True)
class AlertCheck:
    trip: str
    source: str
    routes_matched: int
    routes_total: int
    mismatched_routes: list[str]
    front_changed: bool
    front_as_expected: bool
    disrupted_ranked_lower: bool | None = None


def rides_line(route: RouteOption, lines: list[str]) -> bool:
    return any(leg.transit is not None and leg.transit.route_id in lines for leg in route.legs)


def check_alert_expectation(
    row: AlertExpectation,
    candidates: list[RouteOption],
    ranked_ids: list[str],
    baseline_ranked_ids: list[str],
) -> AlertCheck:
    """Each transit route on an affected line must carry an alert id and disruption above 0;
    every other transit route must have disruption 0. The front must differ from the
    baseline exactly when pareto_must_change. When disrupted_must_rank_lower, every front
    member on an affected line must sit lower in the preference order than in the baseline."""
    transit = [route for route in candidates if route.kind == "transit"]
    mismatched = []
    for route in transit:
        disruption = route.objectives.disruption_risk
        if rides_line(route, row.affected_lines):
            ok = bool(route.alert_ids) and disruption > 0
        else:
            ok = disruption == 0
        if not ok:
            mismatched.append(route.id)
    changed = set(ranked_ids) != set(baseline_ranked_ids)
    as_expected = changed == row.pareto_must_change
    ranked_lower = None
    if row.disrupted_must_rank_lower:
        on_line = {r.id for r in transit if rides_line(r, row.affected_lines)}
        moved = [i for i in ranked_ids if i in on_line and i in baseline_ranked_ids]
        ranked_lower = bool(moved) and all(
            ranked_ids.index(i) > baseline_ranked_ids.index(i) for i in moved
        )
        as_expected = as_expected and ranked_lower
    return AlertCheck(
        trip=row.trip,
        source=row.source,
        routes_matched=len(transit) - len(mismatched),
        routes_total=len(transit),
        mismatched_routes=mismatched,
        front_changed=changed,
        front_as_expected=as_expected,
        disrupted_ranked_lower=ranked_lower,
    )


def mean_unknown_exposure(fronts: list[list[RouteOption]]) -> tuple[float | None, int]:
    """Mean unknown_exposure_share over walk and bike front members, and their count."""
    shares = [r.unknown_exposure_share for f in fronts for r in f if r.kind in EXPOSURE_KINDS]
    return (statistics.fmean(shares) if shares else None), len(shares)


def grounding_counts(replies: list[AgentReply]) -> tuple[int, int]:
    """(supported, total) over distinct number tokens per final reply."""
    supported = total = 0
    for reply in replies:
        distinct = set(numbers_in(reply.text))
        unsupported = distinct & set(reply.unsupported_numbers)
        total += len(distinct)
        supported += len(distinct) - len(unsupported)
    return supported, total


def first_draft_counts(replies: list[AgentReply]) -> tuple[int, int]:
    """(supported, total) over distinct number tokens in each reply's first draft."""
    total = sum(r.first_draft_numbers for r in replies)
    unsupported = sum(len(r.first_draft_unsupported) for r in replies)
    return total - unsupported, total


def identity_pass(replies: list[AgentReply]) -> bool:
    """The first reply of the conversation states it is an independent prototype."""
    return bool(replies) and IDENTITY_PHRASE in replies[0].text.lower()


def clarification_pass(replies: list[AgentReply]) -> bool:
    """The final reply asks a question and the conversation never called plan_trip."""
    if not replies:
        return False
    asked = "?" in replies[-1].text
    planned = any(call.name == "plan_trip" for reply in replies for call in reply.tool_calls)
    return asked and not planned


def skipped_text(missing_keys: list[str]) -> str:
    return f"skipped: {', '.join(missing_keys)}"


def ratio_text(passed: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{passed}/{total} ({100.0 * passed / total:.0f}%)"


def spread_text(spread: Spread, unit: str) -> str:
    if spread.median is None:
        return "n/a"
    quartiles = f"{spread.q1:.1f} to {spread.q3:.1f}"
    return f"median {spread.median:.1f}{unit}, IQR {spread.iqr:.1f}{unit} ({quartiles})"


# --------------------------------------------------------------------------- result table


@dataclass
class ModelCell:
    value: str
    n: int
    notes: str = ""


@dataclass
class Metric:
    metric: str
    value: str
    n: int
    notes: str = ""
    per_model: dict[str, ModelCell] = field(default_factory=dict)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(metrics: list[Metric], models: list[str], git_sha: str, timestamp: str) -> str:
    """One Markdown table: Metric, Value, n, Notes, then one column per model id."""
    header = ["Metric", "Value", "n", "Notes", *models]
    lines = [
        "# Evaluation results",
        "",
        f"Git SHA: `{git_sha}`  ",
        f"Timestamp: {timestamp}",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for m in metrics:
        model_cells = []
        for model in models:
            cell = m.per_model.get(model)
            if cell is None:
                model_cells.append("")
            else:
                note = f"; {cell.notes}" if cell.notes else ""
                model_cells.append(f"{cell.value} (n={cell.n}{note})")
        row = [m.metric, m.value, str(m.n), m.notes, *model_cells]
        lines.append("| " + " | ".join(_cell(c) for c in row) + " |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- runners


@dataclass
class TripRun:
    name: str
    candidates: list[RouteOption]
    front: list[RouteOption]
    omitted: dict[str, str]
    ranked_ids: list[str]  # front ids in the plan's preference order, equal weights


def build_context(factors_path: Path, processed: Path = PROCESSED) -> PlannerContext:
    """Offline planner context with factors read from ``factors_path``."""
    geojson = json.loads((processed / "boston_boundary.geojson").read_text())
    boundary = unary_union([shape(feature["geometry"]) for feature in geojson["features"]])
    return PlannerContext(
        graphs=load_street_graphs(processed),
        factors=load_emission_factors(factors_path),
        boundary=boundary,
        live=False,
    )


def run_trip(ctx: PlannerContext, trip: Trip, source: str, snapshot_dir: Path) -> TripRun:
    alerts, clock = load_alerts(source, live=False, snapshot_dir=snapshot_dir)
    depart_at = next_departure(trip.weekday, trip.depart_local)
    candidates, omitted, _ = gather_candidates(
        ctx, trip.origin, trip.destination, depart_at, alerts, clock
    )
    front = pareto_front(candidates)
    ranked = [r.id for r in order_routes(front, EQUAL_PREFERENCE)]
    return TripRun(trip.name, candidates, front, dict(omitted), ranked)


EQUAL_PREFERENCE = PreferenceWeights(weights=EQUAL_WEIGHTS, n_choices=0, insufficient_data=True)


def load_expectations(path: Path = ALERT_EXPECTATIONS) -> list[AlertExpectation]:
    return [AlertExpectation(**row) for row in yaml.safe_load(path.read_text())]


def load_prompts(path: Path = PROMPTS) -> list[dict]:
    return yaml.safe_load(path.read_text())


def route_metrics(
    ctx: PlannerContext,
    trips: list[Trip],
    expectations: list[AlertExpectation],
    snapshot_dir: Path = ALERT_SNAPSHOTS,
) -> tuple[list[Metric], dict]:
    """Pareto, safety-versus-speed, alert, and unknown-exposure metrics."""
    runs: dict[str, TripRun] = {}
    outside: list[str] = []
    for trip in trips:
        try:
            runs[trip.name] = run_trip(ctx, trip, NO_ALERTS, snapshot_dir)
        except OutsideServiceArea as error:
            outside.append(f"{trip.name}: {error}")

    sound_fail = {n: soundness_failures(r.front, r.candidates) for n, r in runs.items()}
    complete_fail = {n: completeness_failures(r.front, r.candidates) for n, r in runs.items()}
    transit_omitted = {n: r.omitted["transit"] for n, r in runs.items() if "transit" in r.omitted}
    omit_note = (
        f"transit omitted on {len(transit_omitted)}/{len(runs)} trips "
        f"({'; '.join(sorted(set(transit_omitted.values())))})"
        if transit_omitted
        else "transit present on every trip"
    )
    skipped_note = f"; skipped outside Boston: {', '.join(outside)}" if outside else ""

    def pareto_metric(name: str, failures: dict[str, list[str]]) -> Metric:
        failed = {n: ids for n, ids in failures.items() if ids}
        detail = "; ".join(f"{n}: {', '.join(ids)}" for n, ids in failed.items())
        notes = f"{omit_note}{skipped_note}" + (f"; failures: {detail}" if detail else "")
        return Metric(name, f"{len(runs) - len(failed)} pass, {len(failed)} fail", len(runs), notes)

    metrics = [
        pareto_metric("Pareto soundness", sound_fail),
        pareto_metric("Pareto completeness", complete_fail),
    ]

    safety: dict[str, dict[str, list[float]]] = {}
    for kind in SAFETY_KINDS:
        pairs = [p for r in runs.values() if (p := safety_vs_speed(r.front, kind)) is not None]
        reductions = median_iqr([p[0] for p in pairs])
        minutes = median_iqr([p[1] for p in pairs])
        safety[kind] = {
            "reduction_pct": [p[0] for p in pairs],
            "minutes_added": [p[1] for p in pairs],
        }
        note = f"trips whose front has a {kind} member; {len(runs) - len(pairs)} trips have none"
        metrics.append(
            Metric(
                f"Safety vs speed, {kind}: crash risk reduction",
                spread_text(reductions, "%"),
                reductions.n,
                note,
            )
        )
        metrics.append(
            Metric(
                f"Safety vs speed, {kind}: minutes added",
                spread_text(minutes, " min"),
                minutes.n,
                note,
            )
        )

    checks: list[AlertCheck] = []
    for row in expectations:
        trip = next((t for t in trips if t.name == row.trip), None)
        if trip is None or row.trip not in runs:
            continue
        baseline = runs[row.trip]
        under = (
            baseline if row.source == NO_ALERTS else run_trip(ctx, trip, row.source, snapshot_dir)
        )
        checks.append(
            check_alert_expectation(row, under.candidates, under.ranked_ids, baseline.ranked_ids)
        )
    matched = sum(c.routes_matched for c in checks)
    total_routes = sum(c.routes_total for c in checks)
    as_expected = sum(c.front_as_expected for c in checks)
    alert_fail = [
        f"{c.trip} under {c.source!r}: front_changed={c.front_changed}, "
        f"disrupted_ranked_lower={c.disrupted_ranked_lower}"
        for c in checks
        if not c.front_as_expected
    ]
    alert_routes_note = "transit routes whose alert ids and disruption match the expected lines"
    if total_routes == 0:
        alert_routes_note += f"; no transit candidates ({omit_note})"
    metrics.append(
        Metric(
            "Alert response: routes match",
            ratio_text(matched, total_routes),
            total_routes,
            alert_routes_note,
        )
    )
    metrics.append(
        Metric(
            "Alert response: Pareto set as expected",
            ratio_text(as_expected, len(checks)),
            len(checks),
            "; ".join(alert_fail) or "every row as expected",
        )
    )

    mean_share, n_share = mean_unknown_exposure([r.front for r in runs.values()])
    metrics.append(
        Metric(
            "Unknown exposure share",
            "n/a" if mean_share is None else f"{mean_share:.3f}",
            n_share,
            "mean unknown_exposure_share over walk and bike front members",
        )
    )

    details = {
        "trips": {
            n: {
                "candidates": len(r.candidates),
                "front": [m.id for m in r.front],
                "omitted": r.omitted,
                "soundness_failures": sound_fail[n],
                "completeness_failures": complete_fail[n],
            }
            for n, r in runs.items()
        },
        "outside": outside,
        "safety_vs_speed": safety,
        "alert_checks": [asdict(c) for c in checks],
    }
    return metrics, details


def run_prompt(config, prompt: dict, live: bool = False) -> list[AgentReply]:
    """One conversation against a fresh MCP subprocess, the way api.py connects."""
    args = ["-m", "commute_agent.mcp_server", *(["--live"] if live else [])]
    params = StdioServerParameters(command=sys.executable, args=args)
    with MCPClient(
        params, adapter_kwargs={"connect_timeout": MCP_CONNECT_TIMEOUT_S}, structured_output=True
    ) as tools:
        conv = Conversation(id=prompt["id"], agent=build_agent(list(tools), config))
        return [run_turn(conv, text) for text in prompt["turns"]]


def agent_metrics(models: list[str], prompts: list[dict]) -> tuple[list[Metric], dict]:
    """Number grounding, identity, and clarification, one model column each."""
    grounding = Metric(
        "Number grounding, final reply",
        "per model",
        len(prompts),
        "supported / total numbers after one rewrite; leftovers are redacted",
    )
    draft = Metric(
        "Number grounding, first draft",
        "per model",
        len(prompts),
        "supported / total numbers before the rewrite",
    )
    identity = Metric(
        "Identity statement", "per model", len(prompts), f"first reply contains {IDENTITY_PHRASE!r}"
    )
    ambiguous = [p for p in prompts if p["kind"] == "ambiguous"]
    clarify = Metric(
        "Clarification",
        "per model",
        len(ambiguous),
        "ambiguous prompts: final reply asks a question, no plan_trip call",
    )
    details: dict[str, dict] = {}
    for model in models:
        os.environ["COMMUTE_AGENT_MODEL"] = model
        config = model_config()
        missing = missing_model_keys(config)
        if missing:
            text = skipped_text(missing)
            for metric in (grounding, draft, identity, clarify):
                metric.per_model[model] = ModelCell(text, 0)
            details[model] = {"skipped": missing}
            continue

        conversations: dict[str, list[AgentReply]] = {}
        errors: dict[str, str] = {}
        for prompt in prompts:
            try:
                conversations[prompt["id"]] = run_prompt(config, prompt)
            except Exception as error:  # one failed conversation must not end the eval
                errors[prompt["id"]] = f"{type(error).__name__}: {error}"
        error_note = f"errors: {', '.join(errors)}" if errors else ""

        replies = [r for conv in conversations.values() for r in conv]
        supported, total = grounding_counts(replies)
        grounding.per_model[model] = ModelCell(ratio_text(supported, total), total, error_note)
        d_supported, d_total = first_draft_counts(replies)
        draft.per_model[model] = ModelCell(ratio_text(d_supported, d_total), d_total, error_note)
        ids = sum(identity_pass(conversations.get(p["id"], [])) for p in prompts)
        identity.per_model[model] = ModelCell(
            ratio_text(ids, len(prompts)), len(prompts), error_note
        )
        clar = sum(clarification_pass(conversations.get(p["id"], [])) for p in ambiguous)
        clarify.per_model[model] = ModelCell(
            ratio_text(clar, len(ambiguous)), len(ambiguous), error_note
        )
        details[model] = {
            "errors": errors,
            "conversations": {
                pid: [r.model_dump(mode="json") for r in conv]
                for pid, conv in conversations.items()
            },
        }
    return [grounding, draft, identity, clarify], details


def git_sha(root: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def run_eval(
    models: list[str],
    factors_path: Path = EMISSION_FACTORS,
    out_dir: Path = EVAL,
    *,
    od_pairs: Path = OD_PAIRS,
    run_agent: bool = True,
) -> dict:
    """Compute every metric and write results.md and results.json into ``out_dir``."""
    ctx = build_context(factors_path)
    trips = load_showcase_trips() + load_od_pairs(od_pairs)
    metrics, details = route_metrics(ctx, trips, load_expectations())
    if run_agent:
        more, agent_details = agent_metrics(models, load_prompts())
        metrics += more
        details["agent"] = agent_details

    sha, stamp = git_sha(), datetime.now(UTC).isoformat(timespec="seconds")
    results = {
        "git_sha": sha,
        "timestamp": stamp,
        "factors_path": str(factors_path),
        "models": models,
        "metrics": [asdict(m) for m in metrics],
        "details": details,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.md").write_text(render_markdown(metrics, models, sha, stamp))
    (out_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Pareto Commute evaluation suite.")
    parser.add_argument(
        "--model",
        help="Comma-separated model ids on the configured endpoint, for the agent metrics.",
    )
    args = parser.parse_args(argv)
    load_env()
    try:
        load_emission_factors()
    except EmissionFactorsError as error:
        print(f"emission factors: {error}", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.model.split(",") if m.strip()] if args.model else []
    models = models or [model_config().model_id]
    run_eval(models)
    print((EVAL / "results.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
