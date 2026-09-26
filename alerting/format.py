"""Money and numbers, worded the way the desk words them.

A port of the frontend's `format.ts`, kept byte-compatible on purpose: the same
alert can now be raised in the browser or by the backend, and the two must not
word it differently or the log looks like two alerts instead of one.

Indian digit grouping (1,23,456 rather than 1,234,56) is done here rather than
with `locale`, which would depend on a locale being installed in the container.
"""

from __future__ import annotations

import math

#: A true minus, not a hyphen, so figures align in tabular columns.
MINUS = "−"
DASH = "—"


def _js_round(value: float) -> float:
    """JavaScript's `Math.round`: half rounds toward +infinity.

    Not Python's `round`, which is banker's rounding (`round(2.5)` is 2 where
    JavaScript gives 3), and not "away from zero" either - JavaScript rounds
    -2.5 to -2, not -3. Both differences are a digit of drift between an alert
    raised in the browser and the same alert raised here, which is exactly the
    kind of thing that makes one line look like two.
    """
    return math.floor(value + 0.5)


def _round2(value: float) -> float:
    return _js_round(value * 100) / 100


def _round_half_up(value: float) -> int:
    return int(_js_round(value))


def group_indian(value: int) -> str:
    """12345678 -> '1,23,45,678'. Last three digits, then pairs."""
    digits = str(abs(value))
    if len(digits) <= 3:
        head, rest = digits, ""
    else:
        head, rest = digits[:-3], digits[-3:]
        pairs: list[str] = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        head = ",".join(pairs)
    out = f"{head},{rest}" if rest else head
    return ("-" if value < 0 else "") + out


def integer(value: float) -> str:
    if not math.isfinite(value):
        return DASH
    return group_indian(_round_half_up(value))


def compact(value: float) -> str:
    """Lakh/crore compaction - how these numbers are actually spoken here."""
    if not math.isfinite(value):
        return DASH
    a = abs(value)
    sign = MINUS if value < 0 else ""
    if a >= 1e7:
        return f"{sign}{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{sign}{a / 1e5:.2f} L"
    if a >= 1e3:
        return f"{sign}{a / 1e3:.1f}k"
    return sign + str(_round_half_up(a))


def rupees(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "Unlimited"
    r = _round2(value)
    return (MINUS if r < 0 else "") + "₹" + group_indian(_round_half_up(abs(r)))


def rupees_compact(value: float | None) -> str:
    """Compact rupees, for where the column is narrow."""
    if value is None or not math.isfinite(value):
        return "Unlimited"
    r = _round2(value)
    return (MINUS if r < 0 else "") + "₹" + compact(abs(r))
