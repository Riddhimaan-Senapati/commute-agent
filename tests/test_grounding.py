"""Tests for commute_agent.grounding."""

from hypothesis import given
from hypothesis import strategies as st

from commute_agent.grounding import is_supported, numbers_in, unsupported_numbers

# Evidence shared by the two grounding unit tests, as the agent would pass it:
# the text of a tool observation carrying delta 4.2 and pct -69.6.
EVIDENCE = ['{"delta": 4.2, "pct": -69.6}']


def test_supported_reply_returns_empty() -> None:
    # "4" rounds from 4.2, "70" rounds from 69.6; the subscript in "CO₂" is not a digit.
    reply = "4 min slower, ~70% less CO\u2082"
    assert unsupported_numbers(reply, EVIDENCE) == []


def test_fabricated_number_is_reported() -> None:
    assert unsupported_numbers("about 25 minutes", EVIDENCE) == ["25"]


def test_numbers_in_strips_list_markers_and_thousands() -> None:
    text = "1. first\n2) second with 1,234 and 12,345,678"
    # List markers "1." and "2)" are removed; thousands separators are stripped.
    assert numbers_in(text) == ["1234", "12345678"]


def test_unsupported_numbers_dedupes_in_order() -> None:
    reply = "9 and 9 and 7 and 8"
    assert unsupported_numbers(reply, ["only 8 here"]) == ["9", "7"]


# Property 12: Rounded evidence is supported.
@given(
    v=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    d=st.integers(min_value=0, max_value=2),
)
def test_property_rounded_evidence_is_supported(v: float, d: int) -> None:
    rendered = f"{v:.{d}f}"
    # The text f"{v:.{d}f}" is supported by evidence containing v.
    assert is_supported(rendered, {v}) is True


@given(
    v=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    d=st.integers(min_value=0, max_value=2),
)
def test_property_number_absent_after_rounding_is_unsupported(v: float, d: int) -> None:
    # A value whose rounded form differs from every rounded evidence value is unsupported.
    rendered = f"{v:.{d}f}"
    far = float(rendered) + 1000.0  # round(far, d) can never equal round(v, d) here
    assert is_supported(rendered, {far}) is False


def test_digits_inside_names_are_not_numbers() -> None:
    assert numbers_in("estimated CO2 411 g on the SL4, about 22.7 min") == ["411", "22.7"]
    assert unsupported_numbers("CO2 and SL4 both fine", ["{}"]) == []
