"""Setups that arm an order, and trades that happen only if price confirms them.

A condition says a setup exists; it does not say to buy. These cover the gap
between the two, which the engine used to paper over by always filling at the
next open.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backtest.engine import Execution, run
from backtest.market import PerpetualMarket, Side
from backtest.models import Action, Intent, Position, Trigger
from backtest.rules import SpecRule
from backtest.spec import SpecError, parse
from backtest.view import View, build, wind
from marketdata.models import Bar, Interval

START = datetime(2026, 1, 1, tzinfo=UTC)
M5 = Interval.M5


def _bars(rows: list[tuple[float, float, float, float]]) -> list[Bar]:
    return [
        Bar(
            ts=START + timedelta(minutes=5 * i),
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=1.0,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


class OnBar:
    """Arms a setup on named bars and never exits, so a test sees the entry alone."""

    name = "on bar"
    context: tuple[Interval, ...] = ()

    def __init__(self, at: set[int], side: Side = Side.LONG, trigger: Trigger | None = None):
        self.at = at
        self.side = side
        self.trigger = trigger or Trigger(field="high", within=3)

    def entry(self, view: View) -> Intent:
        if view.base._cursor in self.at:
            return Intent.enter(self.side, reason="setup", trigger=self.trigger)
        return Intent.nothing()

    def exit(self, view: View, position: Position) -> Intent:
        return Intent.nothing()


def _market(**kwargs: object) -> PerpetualMarket:
    defaults: dict[str, object] = {
        "symbol": "BTCUSDT",
        "maker_fee": 0.0,
        "taker_fee": 0.0,
        "min_quantity": 0.0,
    }
    defaults.update(kwargs)
    return PerpetualMarket(**defaults)  # type: ignore[arg-type]


FREE = Execution(capital=1000.0, leverage=1.0, slippage_bps=0.0)


# --------------------------------------------------------------------------
# Waiting for the break
# --------------------------------------------------------------------------


def test_a_setup_does_not_enter_until_price_breaks_the_level() -> None:
    """The whole point: the conditions arm an order, they do not place a trade."""
    bars = _bars(
        [
            (100.0, 110.0, 90.0, 100.0),  # setup: its high is 110
            (100.0, 105.0, 95.0, 100.0),  # never reaches 110
            (100.0, 120.0, 95.0, 115.0),  # breaks it
            (115.0, 115.0, 115.0, 115.0),
        ]
    )

    result = run(bars, OnBar({0}), _market(), interval=M5, execution=FREE)

    assert len(result.trades) == 1
    assert result.trades[0].opened_at == bars[2].ts
    assert result.trades[0].entry == pytest.approx(110.0)


def test_the_fill_is_the_level_not_the_next_open() -> None:
    """A stop order fills where it rests. Filling at the open would be a different
    price entirely on any bar that moved."""
    bars = _bars(
        [(100.0, 110.0, 90.0, 100.0), (91.0, 130.0, 91.0, 129.0), (129.0, 129.0, 129.0, 129.0)]
    )

    result = run(bars, OnBar({0}), _market(), interval=M5, execution=FREE)

    assert result.trades[0].entry == pytest.approx(110.0)


def test_a_setup_that_is_never_reached_costs_nothing() -> None:
    bars = _bars([(100.0, 110.0, 90.0, 100.0)] + [(100.0, 105.0, 95.0, 100.0)] * 6)

    result = run(bars, OnBar({0}), _market(), interval=M5, execution=FREE)

    assert result.trades == []
    assert result.armed == 1
    assert result.expired_unfilled == 1
    assert any("never reached" in c for c in result.caveats)


def test_an_order_stops_resting_after_its_window() -> None:
    """`within` is bars, and the bar after the last one does not fill."""
    bars = _bars(
        [(100.0, 110.0, 90.0, 100.0)]
        + [(100.0, 105.0, 95.0, 100.0)] * 2  # bars 1 and 2: no
        + [(100.0, 150.0, 95.0, 140.0)]  # bar 3: too late for within=2
        + [(140.0, 140.0, 140.0, 140.0)]
    )

    result = run(
        bars, OnBar({0}, trigger=Trigger(field="high", within=2)), _market(),
        interval=M5, execution=FREE,
    )

    assert result.trades == []
    assert result.expired_unfilled == 1


def test_one_bar_of_patience_is_the_shortest_window() -> None:
    bars = _bars(
        [(100.0, 110.0, 90.0, 100.0), (100.0, 120.0, 95.0, 115.0), (115.0, 115.0, 115.0, 115.0)]
    )

    result = run(
        bars, OnBar({0}, trigger=Trigger(field="high", within=1)), _market(),
        interval=M5, execution=FREE,
    )

    assert len(result.trades) == 1


# --------------------------------------------------------------------------
# Gaps, where this could invent money
# --------------------------------------------------------------------------


def test_a_bar_that_gaps_through_the_level_fills_at_the_open() -> None:
    """The detail that decides whether this is honest.

    A buy-stop fills at its level when price trades up through it. When the bar
    *opens* above the level it fills at the open - worse, sometimes far worse.
    Filling a gap at the level hands the strategy the whole gap for free, on
    exactly the bars where the move was biggest.
    """
    bars = _bars(
        [
            (100.0, 110.0, 90.0, 100.0),  # setup: high 110
            (140.0, 150.0, 139.0, 145.0),  # opens at 140, far above the level
            (145.0, 145.0, 145.0, 145.0),
        ]
    )

    result = run(bars, OnBar({0}), _market(), interval=M5, execution=FREE)

    assert result.trades[0].entry == pytest.approx(140.0)


def test_a_short_gapping_down_through_its_level_also_fills_at_the_open() -> None:
    bars = _bars(
        [
            (100.0, 110.0, 90.0, 100.0),  # setup: a short rests at the low, 90
            (60.0, 61.0, 50.0, 55.0),
            (55.0, 55.0, 55.0, 55.0),
        ]
    )

    result = run(bars, OnBar({0}, side=Side.SHORT), _market(), interval=M5, execution=FREE)

    assert result.trades[0].entry == pytest.approx(60.0)


def test_a_short_rests_at_the_low_rather_than_the_high() -> None:
    """The level mirrors, so one strategy works both ways round."""
    bars = _bars(
        [
            (100.0, 110.0, 90.0, 100.0),
            (100.0, 105.0, 85.0, 88.0),  # trades down through 90
            (88.0, 88.0, 88.0, 88.0),
        ]
    )

    result = run(bars, OnBar({0}, side=Side.SHORT), _market(), interval=M5, execution=FREE)

    assert result.trades[0].side is Side.SHORT
    assert result.trades[0].entry == pytest.approx(90.0)


def test_a_buffer_makes_the_break_harder_not_easier() -> None:
    """A cushion that helped the order fill would be a discount on the test."""
    bars = _bars(
        [
            (100.0, 110.0, 90.0, 100.0),
            (100.0, 110.5, 95.0, 105.0),  # clears 110 but not 110 + 1%
            (105.0, 105.0, 105.0, 105.0),
        ]
    )

    result = run(
        bars, OnBar({0}, trigger=Trigger(field="high", within=3, buffer_bps=100.0)),
        _market(), interval=M5, execution=FREE,
    )

    assert result.trades == []


def test_a_triggered_entry_pays_the_taker_fee_even_in_maker_mode() -> None:
    """A stop order is a market order the moment it is reached."""
    market = PerpetualMarket(symbol="BTCUSDT", min_quantity=0.0)
    bars = _bars(
        [(100.0, 110.0, 90.0, 100.0), (100.0, 120.0, 95.0, 115.0), (115.0, 115.0, 115.0, 115.0)]
    )

    trade = run(
        bars, OnBar({0}), market, interval=M5,
        execution=Execution(capital=1000.0, maker_entry=True, slippage_bps=0.0),
    ).trades[0]

    # Taker on the way in despite maker_entry, and taker on the way out. Computed
    # from each leg's own notional rather than assumed equal: the position is
    # worth more on exit than on entry whenever the trade made money.
    taker = 0.00040 * 1.18
    entry_leg = trade.quantity * trade.entry * taker
    exit_leg = trade.quantity * trade.exit_price * taker
    assert trade.costs.fees == pytest.approx(entry_leg + exit_leg, rel=1e-9)
    # and the maker rate would have been well under half of the entry leg
    assert entry_leg > trade.quantity * trade.entry * 0.00016 * 1.18 * 2


def test_a_newer_setup_replaces_an_older_one() -> None:
    """Two orders at two levels is a different strategy from the one written."""
    bars = _bars(
        [
            (100.0, 110.0, 90.0, 100.0),  # setup one: 110
            (100.0, 200.0, 95.0, 100.0),  # setup two: 200, and does not reach 110... it does
            (100.0, 105.0, 95.0, 100.0),
            (100.0, 105.0, 95.0, 100.0),
            (100.0, 105.0, 95.0, 100.0),
        ]
    )

    result = run(bars, OnBar({0, 1}), _market(), interval=M5, execution=FREE)

    # bar 1 reaches 110 and fills the first order, so only one trade exists
    assert len(result.trades) == 1
    assert result.armed == 1


# --------------------------------------------------------------------------
# Built rather than coded
# --------------------------------------------------------------------------


def _spec(**extra: object):  # type: ignore[no-untyped-def]
    return parse(
        {
            "name": "Breakout",
            "interval": "5m",
            "long_entry": {
                "left": {"kind": "price", "field": "close"}, "op": "above", "right": 0
            },
            "long_exit": {
                "left": {"kind": "price", "field": "close"}, "op": "below", "right": 0
            },
            **extra,
        }
    )


def test_a_trigger_can_be_built() -> None:
    spec = _spec(trigger={"kind": "break", "field": "high", "within": 5})

    assert spec.trigger == Trigger(field="high", ago=0, within=5, buffer_bps=0.0)


def test_no_trigger_means_the_next_open() -> None:
    assert _spec().trigger is None


def test_a_trigger_of_zero_bars_is_refused() -> None:
    with pytest.raises(SpecError, match="at least one bar"):
        _spec(trigger={"kind": "break", "within": 0})


def test_an_unknown_trigger_says_what_the_alternative_is() -> None:
    with pytest.raises(SpecError, match="leave it out to enter at the next open"):
        _spec(trigger={"kind": "telepathy"})


def test_a_percent_stop_is_measured_from_the_breakout_not_the_close() -> None:
    """On a wide setup candle those are different trades."""
    spec = _spec(
        trigger={"kind": "break", "field": "high", "within": 3},
        stop={"kind": "percent", "value": 1.0},
    )
    bars = _bars([(100.0, 110.0, 90.0, 100.0), (100.0, 100.0, 100.0, 100.0)])
    view, cursors = build(bars, M5)
    wind(view, cursors, 0)

    intent = SpecRule(spec).entry(view)

    assert intent.action is Action.ENTER
    # 1% below 110, the level the order rests at - not 1% below the close of 100
    assert intent.stop == pytest.approx(108.9)
