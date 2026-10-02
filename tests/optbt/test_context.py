"""Daily context and day filters, against numbers worked out by hand."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import duckdb
import pytest

from optbt.context import Context, Pivots
from optbt.data.history import History
from optbt.data.store import SCHEMA
from optbt.strategies.legs import DayFilter


def test_classic_pivots_from_high_low_close() -> None:
    # H 110, L 90, C 100: P = 100, R1 = 110, S1 = 90, R2 = 120, S2 = 80,
    # R3 = 130, S3 = 70.
    p = Pivots.of(110, 90, 100)
    assert (p.p, p.r1, p.s1, p.r2, p.s2, p.r3, p.s3) == (100, 110, 90, 120, 80, 130, 70)


@pytest.mark.parametrize(
    ("price", "zone"),
    [(79, "below S2"), (85, "S2-S1"), (95, "S1-P"), (105, "P-R1"), (115, "R1-R2"),
     (121, "above R2")],
)
def test_the_open_is_placed_in_its_pivot_zone(price: float, zone: str) -> None:
    assert Pivots.of(110, 90, 100).zone(price) == zone


def _store(days: list[tuple[date, float, float, float, float]], vix: dict[date, float]) -> History:
    """Index days as (day, open, high, low, close); VIX as one close a day."""
    conn = duckdb.connect()
    conn.execute(SCHEMA)
    rows = []
    for d, o, h, lo, c in days:
        start = datetime.combine(d, time(9, 15))
        for i in range(375):
            ts = start + timedelta(minutes=i)
            px = o if i == 0 else c if i == 374 else (h if i == 100 else lo if i == 200 else o)
            rows.append(f"('NSE:NIFTY50-INDEX','1',TIMESTAMP '{ts}',{px},{px},{px},{px},0)")
        if d in vix:
            v = vix[d]
            for i in range(375):
                ts = start + timedelta(minutes=i)
                rows.append(f"('NSE:INDIAVIX-INDEX','1',TIMESTAMP '{ts}',{v},{v},{v},{v},0)")
    conn.execute("INSERT INTO index_bar VALUES " + ",".join(rows))
    return History(conn)


def test_a_day_knows_yesterdays_pivots_and_its_own_gap() -> None:
    d1, d2 = date(2026, 9, 21), date(2026, 9, 22)
    ctx = Context(_store([(d1, 100, 110, 90, 100), (d2, 102, 105, 99, 101)], {}))
    first, second = ctx.day(d1), ctx.day(d2)
    assert first is not None and first.pivots is None and first.gap_pct is None
    assert second is not None and second.pivots == Pivots.of(110, 90, 100)
    assert second.gap_pct == pytest.approx(2.0)  # opened 102 against a 100 close
    assert second.open_zone == "P-R1"


def test_vix_percentile_ranks_only_against_earlier_days() -> None:
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    vix = {d: float(10 + i) for i, d in enumerate(days)}  # 10, 11, ... 49
    ctx = Context(_store([(d, 100, 101, 99, 100) for d in days], vix))
    last = days[-1]
    # 25 is above 15 of the 39 earlier closes (10..24): 38.5th percentile.
    assert ctx.vix_percentile(last, 25.0, lookback=40) == pytest.approx(15 / 39 * 100)
    # Today's own close (49) is not among what it is ranked against.
    assert ctx.vix_percentile(last, 49.0, lookback=40) == pytest.approx(100.0)
    # Too little history to rank against is "unknown", not zero.
    assert ctx.vix_percentile(days[3], 12.0, lookback=40) is None


def test_the_day_filter_names_the_condition_a_day_failed() -> None:
    tags = {"expiry_day": False, "dte": 3, "vix": 14.0, "vix_pct": 40.0, "gap_pct": 0.3,
            "open_zone": "P-R1"}
    assert DayFilter().why_not(tags) is None
    assert DayFilter(expiry_day="only").why_not(tags) == "filter: not an expiry day"
    assert DayFilter(vix_pct_min=70).why_not(tags) == "filter: VIX percentile"
    assert DayFilter(open_zones=frozenset({"S1-P", "P-R1"})).why_not(tags) is None
    assert DayFilter(open_zones=frozenset({"S1-P"})).why_not(tags) == (
        "filter: open outside the chosen pivot zones")
    # A condition asked for on a day it cannot be known (no VIX yet) excludes the day.
    assert DayFilter(vix_min=10).why_not({**tags, "vix": None}) == "filter: VIX"
