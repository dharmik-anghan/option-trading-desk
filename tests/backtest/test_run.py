"""The engine, tested mostly for the ways it could lie.

Each test here pins one assumption that, if it slipped, would make results better
than reality rather than worse. That asymmetry is the point: a backtest that
undercounts profit gets thrown away, and one that overcounts it gets traded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backtest.engine import Execution, run
from backtest.market import GST, FundingSchedule, PerpetualMarket, Side
from backtest.models import Exit, Intent, Position
from backtest.view import View
from marketdata.models import Bar, Interval

START = datetime(2026, 1, 1, tzinfo=UTC)
M5 = Interval.M5


def _bars(rows: list[tuple[float, float, float, float]], interval: Interval = M5) -> list[Bar]:
    return [
        Bar(
            ts=START + timedelta(seconds=interval.seconds * i),
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=1.0,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def _flat(n: int, price: float = 100.0) -> list[Bar]:
    return _bars([(price, price, price, price)] * n)


class Scripted:
    """A rule that does what a list tells it, so a test can aim the engine."""

    name = "scripted"
    context: tuple[Interval, ...] = ()

    def __init__(self, entries: dict[int, Intent], exits: dict[int, Intent] | None = None):
        self.entries = entries
        self.exits = exits or {}
        self.seen = 0

    def entry(self, view: View) -> Intent:
        i = view.base._cursor
        self.seen = max(self.seen, i)
        return self.entries.get(i, Intent.nothing())

    def exit(self, view: View, position: Position) -> Intent:
        return self.exits.get(view.base._cursor, Intent.nothing())


def _market(**kwargs: object) -> PerpetualMarket:
    defaults: dict[str, object] = {"symbol": "BTCUSDT", "maker_fee": 0.0, "taker_fee": 0.0}
    defaults.update(kwargs)
    return PerpetualMarket(**defaults)  # type: ignore[arg-type]


FREE = Execution(capital=1000.0, leverage=1.0, slippage_bps=0.0)


# --------------------------------------------------------------------------
# The one-bar delay
# --------------------------------------------------------------------------


def test_a_signal_is_filled_at_the_next_open_not_the_close_that_caused_it() -> None:
    """Filling at the signal bar's close buys at the price that made the decision.

    It is the commonest way a backtest invents money, and on a rule that enters on
    a breakout it invents exactly the breakout.
    """
    flat, up = (100.0, 100.0, 100.0, 100.0), (110.0, 110.0, 110.0, 110.0)
    bars = _bars([flat, up, up])
    rule = Scripted({0: Intent.enter(Side.LONG)}, {2: Intent.exit()})

    result = run(bars, rule, _market(), interval=M5, execution=FREE)

    assert result.trades[0].entry == 110.0  # bar 1's open, not bar 0's close of 100


def test_a_rule_cannot_act_on_the_bar_it_is_reading() -> None:
    """There is no fill at all on the bar that produced the signal."""
    bars = _bars([(100.0, 200.0, 50.0, 100.0)] + [(100.0, 100.0, 100.0, 100.0)] * 3)
    rule = Scripted({0: Intent.enter(Side.LONG)})

    result = run(bars, rule, _market(), interval=M5, execution=FREE)

    assert result.trades[0].opened_at == bars[1].ts


# --------------------------------------------------------------------------
# Ambiguous bars
# --------------------------------------------------------------------------


def test_a_bar_that_hits_both_the_stop_and_the_target_is_reported_as_the_stop() -> None:
    """A bar cannot say which came first, so this takes the worse reading.

    Assuming the target would turn every volatile bar into a win, and volatile
    bars are precisely where a result is decided.
    """
    bars = _bars(
        [
            (100.0, 100.0, 100.0, 100.0),
            (100.0, 100.0, 100.0, 100.0),
            (100.0, 120.0, 80.0, 100.0),  # reaches the target at 110 and the stop at 90
            (100.0, 100.0, 100.0, 100.0),
        ]
    )
    rule = Scripted({0: Intent.enter(Side.LONG, stop=90.0, target=110.0)})

    result = run(bars, rule, _market(), interval=M5, execution=FREE)

    assert result.trades[0].why is Exit.STOP
    assert result.trades[0].exit_price == 90.0


def test_liquidation_outranks_a_stop_on_the_same_bar() -> None:
    """The venue closes a position whatever the rule wanted.

    Reporting the stop instead would describe a loss the trader chose rather than
    one the exchange imposed, and at a smaller size than really happened.
    """
    market = _market(maintenance_margin=0.0)
    bars = _bars(
        [
            (100.0, 100.0, 100.0, 100.0),
            (100.0, 100.0, 100.0, 100.0),
            # through both the 90 stop and the 80 liquidation
            (100.0, 100.0, 50.0, 100.0),
            (100.0, 100.0, 100.0, 100.0),
        ]
    )
    rule = Scripted({0: Intent.enter(Side.LONG, stop=90.0)})
    # at 5x with no maintenance requirement, liquidation is 20% below entry
    result = run(bars, rule, market, interval=M5, execution=Execution(leverage=5.0,
                                                                     slippage_bps=0.0))

    assert result.trades[0].why is Exit.LIQUIDATION
    assert result.trades[0].exit_price == pytest.approx(80.0)


def test_a_short_is_liquidated_upwards() -> None:
    market = _market(maintenance_margin=0.0)
    flat = (100.0, 100.0, 100.0, 100.0)
    bars = _bars([flat, flat, (100.0, 150.0, 100.0, 100.0), flat])
    rule = Scripted({0: Intent.enter(Side.SHORT)})

    result = run(bars, rule, market, interval=M5, execution=Execution(leverage=5.0,
                                                                      slippage_bps=0.0))

    assert result.trades[0].why is Exit.LIQUIDATION
    assert result.trades[0].exit_price == pytest.approx(120.0)


# --------------------------------------------------------------------------
# Costs
# --------------------------------------------------------------------------


def test_slippage_is_always_against_the_trade() -> None:
    """Both ways round: worse to get in, worse to get out, long or short."""
    bars = _flat(4)
    settings = Execution(slippage_bps=10.0, leverage=1.0)

    long_side = run(
        bars, Scripted({0: Intent.enter(Side.LONG)}, {2: Intent.exit()}), _market(),
        interval=M5, execution=settings,
    ).trades[0]
    short_side = run(
        bars, Scripted({0: Intent.enter(Side.SHORT)}, {2: Intent.exit()}), _market(),
        interval=M5, execution=settings,
    ).trades[0]

    assert long_side.entry > 100.0 and long_side.exit_price < 100.0
    assert short_side.entry < 100.0 and short_side.exit_price > 100.0
    assert long_side.gross < 0 and short_side.gross < 0


def test_the_advertised_fee_is_not_the_fee_that_is_paid() -> None:
    """18% GST on top, which turns a 9.4bp round trip out of an 8bp one."""
    market = PerpetualMarket(symbol="BTCUSDT", taker_fee=0.00040)

    charged = market.fee(1.0, 100_000.0, maker=False, opening=True, side=Side.LONG)

    assert charged == pytest.approx(100_000.0 * 0.00040 * (1 + GST))
    assert charged == pytest.approx(47.2)


def test_a_round_trip_that_goes_nowhere_loses_the_costs() -> None:
    """The baseline every rule has to clear before it has done anything."""
    market = PerpetualMarket(symbol="BTCUSDT")
    bars = _flat(4)

    result = run(
        bars,
        Scripted({0: Intent.enter(Side.LONG)}, {2: Intent.exit()}),
        market,
        interval=M5,
        execution=Execution(capital=1000.0, leverage=1.0, slippage_bps=0.0),
    )

    trade = result.trades[0]
    assert trade.gross == pytest.approx(0.0)
    # 0.0472% each way on 1000 of notional
    assert trade.costs.fees == pytest.approx(1000.0 * 0.00040 * 1.18 * 2, rel=1e-6)
    assert result.final < 1000.0


def test_funding_is_charged_to_a_long_and_paid_to_a_short() -> None:
    """A positive rate is longs paying shorts, which is the usual direction."""
    settlement = START + timedelta(minutes=7)
    schedule = FundingSchedule.of("binance", [(settlement, 0.0001)])
    market = _market(funding=schedule)
    bars = _flat(5)

    went_long = run(
        bars, Scripted({0: Intent.enter(Side.LONG)}, {3: Intent.exit()}), market,
        interval=M5, execution=FREE,
    ).trades[0]
    went_short = run(
        bars, Scripted({0: Intent.enter(Side.SHORT)}, {3: Intent.exit()}), market,
        interval=M5, execution=FREE,
    ).trades[0]

    assert went_long.costs.funding > 0
    assert went_short.costs.funding == pytest.approx(-went_long.costs.funding)


def test_funding_is_not_charged_twice_for_the_same_settlement() -> None:
    settlement = START + timedelta(minutes=7)
    market = _market(funding=FundingSchedule.of("binance", [(settlement, 0.001)]))
    bars = _flat(10)

    trade = run(
        bars, Scripted({0: Intent.enter(Side.LONG)}, {8: Intent.exit()}), market,
        interval=M5, execution=FREE,
    ).trades[0]

    assert trade.costs.funding == pytest.approx(1000.0 * 0.001)


def test_a_position_closed_before_the_settlement_pays_no_funding() -> None:
    """Their fee page is explicit about it: no position, no funding."""
    market = _market(
        funding=FundingSchedule.of("binance", [(START + timedelta(hours=5), 0.001)])
    )

    trade = run(
        _flat(10), Scripted({0: Intent.enter(Side.LONG)}, {4: Intent.exit()}), market,
        interval=M5, execution=FREE,
    ).trades[0]

    assert trade.costs.funding == 0.0


# --------------------------------------------------------------------------
# Maker entries
# --------------------------------------------------------------------------


def test_a_resting_order_that_is_only_touched_does_not_fill() -> None:
    """Touching the limit says nothing about the queue in front of it."""
    bars = _bars(
        [
            (100.0, 100.0, 100.0, 100.0),  # decide here: limit rests at 100
            (101.0, 102.0, 100.0, 101.0),  # low touches 100 exactly, never goes through
            (101.0, 101.0, 101.0, 101.0),
        ]
    )
    rule = Scripted({0: Intent.enter(Side.LONG)})

    result = run(
        bars, rule, _market(), interval=M5,
        execution=Execution(maker_entry=True, slippage_bps=0.0),
    )

    assert result.trades == []


def test_a_resting_order_that_is_traded_through_fills_at_its_own_price() -> None:
    bars = _bars(
        [
            (100.0, 100.0, 100.0, 100.0),
            (101.0, 102.0, 99.0, 101.0),  # trades through 100
            (101.0, 101.0, 101.0, 101.0),
            (101.0, 101.0, 101.0, 101.0),
        ]
    )
    rule = Scripted({0: Intent.enter(Side.LONG)}, {3: Intent.exit()})

    result = run(
        bars, rule, _market(), interval=M5,
        execution=Execution(maker_entry=True, slippage_bps=50.0),
    )

    # its own price, and no slippage - a resting order is not crossed into
    assert result.trades[0].entry == 100.0


def test_a_maker_entry_pays_the_maker_fee_and_the_exit_still_pays_taker() -> None:
    """A stop is a market order however the entry was placed."""
    market = PerpetualMarket(symbol="BTCUSDT")
    flat = (100.0, 100.0, 100.0, 100.0)
    bars = _bars([flat, (100.0, 101.0, 99.0, 100.0), flat, flat])

    trade = run(
        bars, Scripted({0: Intent.enter(Side.LONG)}, {3: Intent.exit()}), market,
        interval=M5, execution=Execution(maker_entry=True, slippage_bps=0.0),
    ).trades[0]

    expected = 1000.0 * (0.00016 + 0.00040) * (1 + GST)
    assert trade.costs.fees == pytest.approx(expected, rel=1e-6)


# --------------------------------------------------------------------------
# Bookkeeping
# --------------------------------------------------------------------------


def test_the_equity_curve_shows_a_loss_before_it_is_realised() -> None:
    """A curve that only moved on a close would hide most of every drawdown."""
    flat = (100.0, 100.0, 100.0, 100.0)
    bars = _bars([flat, flat, (100.0, 100.0, 60.0, 60.0), flat])
    rule = Scripted({0: Intent.enter(Side.LONG)})

    result = run(bars, rule, _market(), interval=M5, execution=FREE)

    # ten units bought at 100, marked at the bar's close of 60: 400 of open loss,
    # on the curve at bar 2 although the trade does not close until the data ends
    assert result.equity[2] == pytest.approx(600.0)
    assert result.trades[0].closed_at == bars[-1].ts


def test_a_position_open_at_the_end_is_closed_and_said_so() -> None:
    result = run(
        _flat(5), Scripted({0: Intent.enter(Side.LONG)}), _market(), interval=M5,
        execution=FREE,
    )

    assert result.trades[0].why is Exit.END_OF_DATA
    assert any("still open" in c for c in result.caveats)


def test_only_one_position_is_held_at_a_time() -> None:
    """A second entry while in a trade is ignored rather than stacked."""
    rule = Scripted({0: Intent.enter(Side.LONG), 1: Intent.enter(Side.LONG)})

    result = run(_flat(6), rule, _market(), interval=M5, execution=FREE)

    assert len(result.trades) == 1


def test_an_empty_run_says_so_rather_than_dividing_by_zero() -> None:
    result = run([], Scripted({}), _market(), interval=M5)

    assert result.trades == []
    assert result.caveats == ["No bars, so nothing was tested"]


def test_a_rule_that_never_trades_ends_where_it_started() -> None:
    result = run(_flat(50), Scripted({}), _market(), interval=M5, execution=FREE)

    assert result.final == result.capital
    assert result.equity == [1000.0] * 50


def test_sizing_compounds_from_equity_rather_than_starting_capital() -> None:
    """A losing run trades smaller, which is what an account does."""
    high, low = (100.0, 100.0, 100.0, 100.0), (50.0, 50.0, 50.0, 50.0)
    bars = _bars([high, high, low, low, low, low])
    rule = Scripted(
        {0: Intent.enter(Side.LONG), 3: Intent.enter(Side.LONG)},
        {1: Intent.exit(), 4: Intent.exit()},
    )

    result = run(bars, rule, _market(), interval=M5, execution=FREE)

    first, second = result.trades
    assert second.quantity * second.entry < first.quantity * first.entry


# --------------------------------------------------------------------------
# Turning round
# --------------------------------------------------------------------------


class Reversing:
    """Long on an up bar, short on a down one, and out of the other on the same
    signal - the shape of every crossover strategy written both ways."""

    name = "reversing"
    context: tuple[Interval, ...] = ()

    def __init__(self, longs: set[int], shorts: set[int]):
        self.longs = longs
        self.shorts = shorts

    def entry(self, view: View) -> Intent:
        i = view.base._cursor
        if i in self.longs:
            return Intent.enter(Side.LONG, reason="up")
        if i in self.shorts:
            return Intent.enter(Side.SHORT, reason="down")
        return Intent.nothing()

    def exit(self, view: View, position: Position) -> Intent:
        i = view.base._cursor
        leaving = self.shorts if position.side is Side.LONG else self.longs
        return Intent.exit("flip") if i in leaving else Intent.nothing()


def test_a_position_can_close_and_open_the_other_way_on_one_signal() -> None:
    """The bug this was written for.

    The signal that closes a short is usually the same signal that opens a long.
    If the entry is only considered after the exit has been filled, then by the
    next bar the crossing has passed and the trade is never taken - so a
    long-and-short crossover ran for three years and took shorts only.
    """
    bars = _flat(8)
    rule = Reversing(longs={4}, shorts={1})

    result = run(bars, rule, _market(), interval=M5, execution=FREE)

    assert [t.side for t in result.trades] == [Side.SHORT, Side.LONG]
    assert result.reversals == 1


def test_a_reversal_closes_and_opens_at_the_same_price() -> None:
    """Both legs are filled at the next bar's open, because it is one decision."""
    bars = _bars([(100.0, 100.0, 100.0, 100.0)] * 3 + [(120.0, 120.0, 120.0, 120.0)] * 4)
    rule = Reversing(longs={2}, shorts={0})

    result = run(bars, rule, _market(), interval=M5, execution=FREE)

    closed, opened = result.trades[0], result.trades[1]
    assert closed.closed_at == opened.opened_at
    assert closed.exit_price == opened.entry


def test_an_entry_agreeing_with_the_position_being_closed_is_ignored() -> None:
    """Re-entering what was just exited is churn the strategy did not ask for."""

    class Contradictory:
        name = "contradictory"
        context: tuple[Interval, ...] = ()

        def entry(self, view: View) -> Intent:
            return Intent.enter(Side.LONG)

        def exit(self, view: View, position: Position) -> Intent:
            return Intent.exit("out") if view.base._cursor == 2 else Intent.nothing()

    result = run(_flat(8), Contradictory(), _market(), interval=M5, execution=FREE)

    assert result.reversals == 0
    # out at bar 3, and back in on the next bar's own entry rather than as a flip
    assert len(result.trades) == 2


# --------------------------------------------------------------------------
# Sizing
# --------------------------------------------------------------------------


def test_a_fixed_lot_is_traded_whatever_the_account_is_worth() -> None:
    """The lot you would actually type into the ticket."""
    bars = _bars([(100.0, 100.0, 100.0, 100.0)] * 3 + [(50.0, 50.0, 50.0, 50.0)] * 4)
    rule = Scripted(
        {0: Intent.enter(Side.LONG), 4: Intent.enter(Side.LONG)},
        {1: Intent.exit(), 5: Intent.exit()},
    )

    result = run(
        bars, rule, _market(), interval=M5,
        execution=Execution(capital=1000.0, sizing="quantity", quantity=0.5, slippage_bps=0.0),
    )

    assert [t.quantity for t in result.trades] == [0.5, 0.5]


def test_a_fixed_notional_buys_fewer_contracts_as_the_price_rises() -> None:
    bars = _bars([(100.0, 100.0, 100.0, 100.0)] * 3 + [(200.0, 200.0, 200.0, 200.0)] * 4)
    rule = Scripted(
        {0: Intent.enter(Side.LONG), 4: Intent.enter(Side.LONG)},
        {1: Intent.exit(), 5: Intent.exit()},
    )

    result = run(
        bars, rule, _market(), interval=M5,
        execution=Execution(capital=1000.0, sizing="notional", notional=100.0,
                            slippage_bps=0.0),
    )

    assert [t.quantity for t in result.trades] == [1.0, 0.5]


def test_a_quantity_is_rounded_down_to_what_the_venue_accepts() -> None:
    """Rounding up can put an order above the margin just checked for it."""
    market = _market(quantity_dp=2)

    assert market.round_quantity(0.12789) == pytest.approx(0.12)


def test_a_signal_below_the_venues_minimum_is_counted_rather_than_taken() -> None:
    """A curve must not describe trades nobody could have placed."""
    market = _market(min_quantity=1.0)

    result = run(
        _flat(6), Scripted({0: Intent.enter(Side.LONG)}), market, interval=M5,
        execution=Execution(capital=1000.0, sizing="quantity", quantity=0.01),
    )

    assert result.trades == []
    assert result.skipped_too_small == 1
    assert any("minimum size" in c for c in result.caveats)


def test_a_signal_the_account_cannot_post_margin_for_is_counted_not_taken() -> None:
    result = run(
        _flat(6), Scripted({0: Intent.enter(Side.LONG)}), _market(), interval=M5,
        execution=Execution(capital=10.0, sizing="quantity", quantity=5.0, leverage=1.0),
    )

    assert result.trades == []
    assert result.skipped_unaffordable == 1
    assert any("more margin than the account had" in c for c in result.caveats)


def test_a_minimum_notional_can_bind_where_a_minimum_quantity_does_not() -> None:
    """A venue can allow 0.001 BTC and still demand 115 USDT of it."""
    market = _market(min_quantity=0.001, min_notional=115.0)

    result = run(
        _flat(6), Scripted({0: Intent.enter(Side.LONG)}), market, interval=M5,
        execution=Execution(capital=1000.0, sizing="quantity", quantity=0.01),
    )

    assert result.skipped_too_small == 1


def test_a_trade_records_why_it_opened_and_why_it_closed_separately() -> None:
    """One field was silently both, and no log column could be labelled honestly.

    A stop closing a long used to file the *entry's* words as the reason, so the
    log showed a long "taken because" of the condition that had closed it.
    """
    bars = _bars([(100.0, 100.0, 100.0, 100.0)] * 2 + [(100.0, 100.0, 80.0, 90.0),
                  (100.0, 100.0, 100.0, 100.0)])
    rule = Scripted({0: Intent.enter(Side.LONG, stop=90.0, reason="the entry fired")})

    stopped = run(bars, rule, _market(), interval=M5, execution=FREE).trades[0]

    assert stopped.why is Exit.STOP
    assert stopped.entry_reason == "the entry fired"
    assert stopped.exit_reason == "the stop was reached"


def test_a_rule_exit_records_the_condition_that_closed_it() -> None:
    rule = Scripted(
        {0: Intent.enter(Side.LONG, reason="the entry fired")},
        {2: Intent.exit("the exit fired")},
    )

    closed = run(_flat(6), rule, _market(), interval=M5, execution=FREE).trades[0]

    assert closed.entry_reason == "the entry fired"
    assert closed.exit_reason == "the exit fired"
