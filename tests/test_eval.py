from commute_agent.models import AgentReply, Leg, ToolCall, TransitRef
from eval.run import (
    AlertExpectation,
    Metric,
    ModelCell,
    check_alert_expectation,
    clarification_pass,
    completeness_failures,
    first_draft_counts,
    grounding_counts,
    identity_pass,
    mean_unknown_exposure,
    median_iqr,
    render_markdown,
    safety_vs_speed,
    skipped_text,
    soundness_failures,
)
from tests.conftest import make_route


def transit_route(id: str, line: str, disruption: float, alert_ids: list[str]):
    route = make_route(id, "transit", 20.0, 50.0, 0.0, disruption)
    leg = Leg(
        mode="subway",
        distance_m=5000.0,
        duration_min=15.0,
        co2_g=50.0,
        transit=TransitRef(
            route_id=line, route_name=line, route_type=1, direction_id=0, stop_ids=["place-x"]
        ),
    )
    return route.model_copy(update={"legs": [leg], "alert_ids": alert_ids})


def reply(text: str, *, tools: tuple[str, ...] = (), unsupported: tuple[str, ...] = ()):
    return AgentReply(
        conversation_id="c1",
        text=text,
        tool_calls=[ToolCall(name=n, input={}, output="", is_error=False) for n in tools],
        unsupported_numbers=list(unsupported),
    )


FAST = make_route("drive-1", "drive", 10.0, 500.0, 0.0, 0.0)
SLOW_GREEN = make_route("walk-1", "walk", 40.0, 0.0, 3.0, 0.0)
DOMINATED = make_route("walk-2", "walk", 45.0, 10.0, 4.0, 0.0)
DUPLICATE = make_route("walk-3", "walk", 40.2, 0.0, 3.01, 0.0)


def test_sound_front_has_no_failures():
    candidates = [FAST, SLOW_GREEN, DOMINATED]
    assert soundness_failures([FAST, SLOW_GREEN], candidates) == []


def test_dominated_front_member_is_a_soundness_failure():
    candidates = [FAST, SLOW_GREEN, DOMINATED]
    assert soundness_failures([FAST, DOMINATED], candidates) == ["walk-2"]


def test_missing_undominated_candidate_is_a_completeness_failure():
    candidates = [FAST, SLOW_GREEN, DOMINATED]
    assert completeness_failures([FAST], candidates) == ["walk-1"]


def test_near_duplicate_of_a_member_is_not_a_completeness_failure():
    candidates = [FAST, SLOW_GREEN, DUPLICATE, DOMINATED]
    assert completeness_failures([FAST, SLOW_GREEN], candidates) == []


def test_safety_vs_speed_compares_safest_with_fastest_of_one_kind():
    fast_bike = make_route("bike-1", "bike", 20.0, 0.0, 8.0, 0.0)
    safe_bike = make_route("bike-2", "bike", 26.0, 0.0, 2.0, 0.0)
    assert safety_vs_speed([FAST, fast_bike, safe_bike], "bike") == (75.0, 6.0)


def test_safety_vs_speed_single_member_counts_zero_and_absent_kind_is_none():
    assert safety_vs_speed([FAST, SLOW_GREEN], "walk") == (0.0, 0.0)
    assert safety_vs_speed([FAST, SLOW_GREEN], "bike") is None


def test_median_iqr():
    spread = median_iqr([1.0, 2.0, 3.0, 4.0, 5.0])
    assert (spread.median, spread.q1, spread.q3, spread.iqr, spread.n) == (3.0, 2.0, 4.0, 2.0, 5)


def test_median_iqr_small_inputs():
    assert median_iqr([]).median is None
    assert median_iqr([]).n == 0
    one = median_iqr([7.0])
    assert (one.median, one.iqr, one.n) == (7.0, 0.0, 1)


SYNTHETIC = "Synthetic: Red Line suspended JFK/UMass to Park Street"


def test_alert_expectation_passes_when_red_routes_carry_the_alert_and_front_changes():
    row = AlertExpectation("fields-corner-to-downtown", SYNTHETIC, ["Red"], True)
    red = transit_route("transit-1", "Red", 1.0, ["synthetic-red-1"])
    bus = transit_route("transit-2", "15", 0.0, [])
    check = check_alert_expectation(
        row, [FAST, red, bus], ["drive-1", "transit-2"], ["drive-1", "transit-1"]
    )
    assert (check.routes_matched, check.routes_total, check.mismatched_routes) == (2, 2, [])
    assert check.front_changed is True
    assert check.front_as_expected is True


def test_alert_expectation_flags_unalerted_red_route_and_unchanged_front():
    row = AlertExpectation("fields-corner-to-downtown", SYNTHETIC, ["Red"], True)
    red = transit_route("transit-1", "Red", 0.0, [])
    stray = transit_route("transit-2", "15", 0.3, ["other"])
    check = check_alert_expectation(row, [red, stray], ["transit-1"], ["transit-1"])
    assert check.mismatched_routes == ["transit-1", "transit-2"]
    assert check.routes_matched == 0
    assert check.front_as_expected is False


def test_none_source_expects_no_disruption_and_no_change():
    row = AlertExpectation("charlestown-to-copley", "none", [], False)
    orange = transit_route("transit-1", "Orange", 0.0, [])
    check = check_alert_expectation(row, [orange], ["transit-1"], ["transit-1"])
    assert (check.routes_matched, check.routes_total, check.front_as_expected) == (1, 1, True)


def test_mean_unknown_exposure_uses_walk_and_bike_members_only():
    walk = SLOW_GREEN.model_copy(update={"unknown_exposure_share": 0.5})
    bike = make_route("bike-1", "bike", 20.0, 0.0, 2.0, 0.0).model_copy(
        update={"unknown_exposure_share": 0.1}
    )
    bluebikes = make_route("bluebikes-1", "bluebikes", 25.0, 0.0, 2.0, 0.0).model_copy(
        update={"unknown_exposure_share": 1.0}
    )
    mean, n = mean_unknown_exposure([[FAST, walk], [bike, bluebikes]])
    assert n == 2
    assert abs(mean - 0.3) < 1e-12
    assert mean_unknown_exposure([[FAST]]) == (None, 0)


def test_grounding_counts_distinct_numbers_per_reply():
    replies = [
        reply("Walk takes 40 min, 40 min total, crash index 3.0.", unsupported=("3.0",)),
        reply("Drive takes 10 min."),
    ]
    assert grounding_counts(replies) == (2, 3)


def test_identity_checks_only_the_first_reply():
    assert identity_pass([reply("I am an Independent Prototype. Walk 40 min."), reply("ok")])
    assert not identity_pass([reply("Walk 40 min."), reply("independent prototype")])
    assert not identity_pass([])


def test_clarification_needs_question_and_no_plan_trip():
    assert clarification_pass([reply("Which Brighton stop do you mean?", tools=("geocode_place",))])
    assert not clarification_pass([reply("Which one?", tools=("geocode_place", "plan_trip"))])
    assert not clarification_pass([reply("Here is your trip.")])


def test_clarification_question_may_be_followed_by_more_text():
    text = "Which Brighton do you mean?\n\n- Brighton Center\n\nTell me and I'll plan it."
    assert clarification_pass([reply(text, tools=("geocode_place",))])


def test_disrupted_route_that_ranks_lower_and_is_flagged_passes():
    row = AlertExpectation("fields-corner-to-downtown", SYNTHETIC, ["Red"], False, True)
    red = transit_route("transit-1", "Red", 1.0, ["synthetic-red-1"])
    check = check_alert_expectation(
        row, [FAST, red], ["drive-1", "transit-1"], ["transit-1", "drive-1"]
    )
    assert (check.front_changed, check.disrupted_ranked_lower, check.front_as_expected) == (
        False,
        True,
        True,
    )


def test_disrupted_route_that_keeps_its_rank_fails():
    row = AlertExpectation("fields-corner-to-downtown", SYNTHETIC, ["Red"], False, True)
    red = transit_route("transit-1", "Red", 1.0, ["synthetic-red-1"])
    check = check_alert_expectation(
        row, [FAST, red], ["transit-1", "drive-1"], ["transit-1", "drive-1"]
    )
    assert (check.disrupted_ranked_lower, check.front_as_expected) == (False, False)


def test_first_draft_counts_use_the_draft_totals():
    draft = reply("ok").model_copy(
        update={"first_draft_numbers": 5, "first_draft_unsupported": ["22", "0.4"]}
    )
    clean = reply("ok").model_copy(update={"first_draft_numbers": 3})
    assert first_draft_counts([draft, clean]) == (6, 8)


def test_skipped_agent_formatting_in_markdown():
    assert (
        skipped_text(["OPENROUTER_API_KEY", "OTHER_KEY"])
        == "skipped: OPENROUTER_API_KEY, OTHER_KEY"
    )
    metric = Metric(
        "Number grounding",
        "per model",
        10,
        "supported / total numbers",
        per_model={
            "deepseek/deepseek-v4.1-flash-1017": ModelCell(skipped_text(["OPENROUTER_API_KEY"]), 0)
        },
    )
    table = render_markdown(
        [metric], ["deepseek/deepseek-v4.1-flash-1017"], "abc123", "2026-10-24T00:00:00+00:00"
    )
    assert "Git SHA: `abc123`" in table
    assert "| Metric | Value | n | Notes | deepseek/deepseek-v4.1-flash-1017 |" in table
    assert (
        "| Number grounding | per model | 10 | supported / total numbers | "
        "skipped: OPENROUTER_API_KEY (n=0) |"
    ) in table
