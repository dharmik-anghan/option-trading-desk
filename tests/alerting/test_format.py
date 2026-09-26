"""The wording of money, pinned to the same table the frontend pins.

`frontend/src/format.test.ts` asserts this exact table. Both suites exist because
the same alert can be worded by either side and one situation must not read as
two: a change here that is not made there fails one of them rather than quietly
producing two spellings of the same number.

The rounding rows at the end record a real difference that a dump from the
TypeScript caught. JavaScript's `Math.round` rounds half toward +infinity, so
-2.5 is -2; Python's `round` is banker's rounding and "away from zero" gives -3.
Both are wrong for this purpose.
"""

from __future__ import annotations

import math

import pytest

from alerting.format import compact, integer, rupees, rupees_compact

#: value, rupees, rupees_compact, integer, compact
TABLE: list[tuple[float, str, str, str, str]] = [
    (0, "₹0", "₹0", "0", "0"),
    (1234, "₹1,234", "₹1.2k", "1,234", "1.2k"),
    (123456, "₹1,23,456", "₹1.23 L", "1,23,456", "1.23 L"),
    (12345678, "₹1,23,45,678", "₹1.23 Cr", "1,23,45,678", "1.23 Cr"),
    (-2805.4, "−₹2,805", "−₹2.8k", "-2,805", "−2.8k"),
    (15000, "₹15,000", "₹15.0k", "15,000", "15.0k"),
    (-83.15, "−₹83", "−₹83", "-83", "−83"),
    (40000, "₹40,000", "₹40.0k", "40,000", "40.0k"),
    (25000, "₹25,000", "₹25.0k", "25,000", "25.0k"),
    (999.5, "₹1,000", "₹1000", "1,000", "1000"),
    (2.5, "₹3", "₹3", "3", "3"),
    (-2.5, "−₹3", "−₹3", "-2", "−3"),
]


@pytest.mark.parametrize(("value", "as_rupees", "as_compact_rupees", "as_int", "as_compact"), TABLE)
def test_matches_the_frontend(
    value: float, as_rupees: str, as_compact_rupees: str, as_int: str, as_compact: str
) -> None:
    assert rupees(value) == as_rupees
    assert rupees_compact(value) == as_compact_rupees
    assert integer(value) == as_int
    assert compact(value) == as_compact


def test_uses_a_true_minus_not_a_hyphen() -> None:
    assert "−" in rupees(-100)
    assert "-" not in rupees(-100)


def test_groups_digits_the_indian_way() -> None:
    assert integer(123456) == "1,23,456"


def test_an_absent_number_is_unlimited_not_zero() -> None:
    # a structure with no worst case is unbounded, not break-even
    assert rupees(None) == "Unlimited"
    assert rupees_compact(None) == "Unlimited"


def test_an_infinity_does_not_become_a_figure() -> None:
    assert rupees(math.inf) == "Unlimited"
    assert integer(math.nan) == "—"
