"""Number grounding: keep the agent from stating numbers no tool or user supplied.

Pure functions, no I/O. See the design's "WS7. Grounding" section.
"""

import re

# A digit glued to a letter is part of a name (CO2, SL4, Green-E1), not a quantity.
NUMBER = re.compile(r"(?<![A-Za-z_\d.])\d+(?:\.\d+)?")
LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")


def numbers_in(text: str) -> list[str]:
    """Remove list markers and thousands separators, then return every NUMBER match."""
    cleaned = LIST_MARKER.sub("", text)
    cleaned = THOUSANDS.sub("", cleaned)
    return NUMBER.findall(cleaned)


def is_supported(token: str, evidence: set[float]) -> bool:
    """True when some evidence value rounds to the token at the token's own precision.

    `decimals` is the number of digits after the point in the token. The token is
    supported when some v in evidence has round(abs(v), decimals) equal to float(token)
    within 1e-9.
    """
    _, _, frac = token.partition(".")
    decimals = len(frac)
    target = float(token)
    return any(abs(round(abs(v), decimals) - target) < 1e-9 for v in evidence)


def unsupported_numbers(reply: str, evidence_texts: list[str]) -> list[str]:
    """Return reply tokens not supported by the evidence, in order, deduplicated.

    Evidence is every NUMBER found in every evidence text.
    """
    evidence: set[float] = set()
    for text in evidence_texts:
        for token in numbers_in(text):
            evidence.add(float(token))

    result: list[str] = []
    seen: set[str] = set()
    for token in numbers_in(reply):
        if token in seen:
            continue
        if not is_supported(token, evidence):
            result.append(token)
            seen.add(token)
    return result
