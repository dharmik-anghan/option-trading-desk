"""The figures, and the two that exist to stop self-deception."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backtest.market import Costs, Side
from backtest.metrics import annualised, max_drawdown, measure, sharpe
from backtest.models import Exit, Trade
from marketdata.models import Bar, Interval

START = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(gross: float, *, fees: float = 0.0, funding: float = 0.0, minutes: int = 5) -> Trade:
    return Trade(
        side=Side.LONG,
        quantity=1.0,
        opened_at=START,
        closed_at=START + timedelta(minutes=minutes),
        entry=100.0,
        exit_price=100.0 + gross,
        why=Exit.RULE,
        entry_reason="a condition",
        exit_reason="another condition",
        costs=Costs(fees=fees, funding=funding),
        gross=gross,
    )


def _bars(prices: list[float]) -> list[Bar]:
    return [
        Bar(ts=START + timedelta(minutes=5 * i), open=p, high=p, low=p, close=p, volume=1.0)
        for i, p in enumerate(prices)
    ]


def test_a_win_is_judged_after_costs() -> None:
    """A trade that made two and cost three did not win."""
    assert _trade(2.0, fees=3.0).won is False
    assert _trade(2.0, fees=1.0).won is True


def test_drawdown_is_measured_from_the_peak() -> None:
    assert max_drawdown([100, 120, 60, 90]) == pytest.approx(0.5)


def test_drawdown_of_a_curve_that_only_rises_is_nothing() -> None:
    assert max_drawdown([100, 110, 120]) == 0.0


def test_cost_share_says_where_the_money_went() -> None:
    """The figure that separates a sizing problem from a bad idea."""
    trades = [_trade(10.0, fees=4.0), _trade(10.0, fees=4.0)]
    equity = [1000.0, 1012.0]

    metrics = measure(trades, equity, _bars([100.0, 100.0]), 1000.0, Interval.M5)

    assert metrics.gross == pytest.approx(20.0)
    assert metrics.cost_share == pytest.approx(0.4)


def test_cost_share_is_undefined_when_gross_was_negative() -> None:
    """A ratio against a negative number would read as though costs helped."""
    metrics = measure(
        [_trade(-10.0, fees=4.0)], [1000.0, 986.0], _bars([100.0, 100.0]), 1000.0, Interval.M5
    )

    assert metrics.cost_share is None


def test_every_result_carries_what_holding_would_have_done() -> None:
    """Over a window where the instrument doubled, 40% is a loss."""
    bars = _bars([100.0, 150.0, 200.0])
    metrics = measure([_trade(400.0)], [1000.0, 1200.0, 1400.0], bars, 1000.0, Interval.M5)

    assert metrics.buy_and_hold == pytest.approx(1.0)
    assert metrics.total_return == pytest.approx(0.4)
    assert metrics.beat_holding is False


def test_endings_are_counted_by_kind() -> None:
    """The most diagnostic column: a rule that is mostly liquidated is sized wrong."""
    trades = [
        _trade(1.0),
        Trade(
            side=Side.LONG, quantity=1.0, opened_at=START, closed_at=START,
            entry=100.0, exit_price=80.0, why=Exit.LIQUIDATION,
            entry_reason="a condition", exit_reason="the venue closed it",
            costs=Costs(), gross=-20.0,
        ),
    ]

    metrics = measure(trades, [1000.0, 981.0], _bars([100.0, 100.0]), 1000.0, Interval.M5)

    assert metrics.endings == {"rule": 1, "liquidation": 1}


def test_exposure_is_the_share_of_the_run_spent_holding() -> None:
    bars = _bars([100.0] * 10)  # ten five-minute bars, fifty minutes
    metrics = measure([_trade(1.0, minutes=25)], [1000.0] * 10, bars, 1000.0, Interval.M5)

    assert metrics.exposure == pytest.approx(0.5)


def test_sharpe_of_a_flat_curve_is_zero_rather_than_a_division_by_zero() -> None:
    assert sharpe([1000.0] * 10, Interval.M5) == 0.0


def test_measuring_a_run_with_no_trades() -> None:
    metrics = measure([], [1000.0, 1000.0], _bars([100.0, 100.0]), 1000.0, Interval.M5)

    assert metrics.trades == 0
    assert metrics.win_rate == 0.0
    assert metrics.cost_share is None


def test_annualising_compounds_rather_than_averaging() -> None:
    """A run that doubled over three years returned 26% a year, not 33%."""
    three_years = [
        Bar(ts=START + timedelta(days=365 * 3 * i), open=100.0, high=100.0, low=100.0,
            close=100.0, volume=1.0)
        for i in range(2)
    ]

    yearly = annualised(1000.0, 2000.0, three_years, Interval.D1)

    assert yearly == pytest.approx(0.26, abs=0.01)


def test_a_run_that_lost_everything_is_minus_one_hundred_a_year() -> None:
    """Whatever the formula would otherwise produce for a zero or negative end."""
    two_years = [
        Bar(ts=START + timedelta(days=365 * 2 * i), open=100.0, high=100.0, low=100.0,
            close=100.0, volume=1.0)
        for i in range(2)
    ]

    assert annualised(1000.0, 0.0, two_years, Interval.D1) == -1.0


def test_too_short_a_window_is_not_annualised_at_all() -> None:
    """A run that made 1% in an afternoon annualises to several million percent,
    and a Calmar built on it would be the most impressive number on the page."""
    an_hour = _bars([100.0] * 12)

    assert annualised(1000.0, 1010.0, an_hour, Interval.M5) is None


def test_calmar_is_the_year_over_the_worst_fall() -> None:
    """The ratio that matches how a rule is actually abandoned."""
    bars = [
        Bar(ts=START + timedelta(days=365 * i), open=100.0, high=100.0, low=100.0,
            close=100.0, volume=1.0)
        for i in range(2)
    ]
    # up 50% over a year, through a 25% fall
    metrics = measure([_trade(500.0)], [1000.0, 750.0, 1500.0], bars, 1000.0, Interval.D1)

    assert metrics.annualised is not None
    assert metrics.annualised == pytest.approx(0.5, abs=0.02)
    assert metrics.max_drawdown == pytest.approx(0.25)
    assert metrics.calmar == pytest.approx(metrics.annualised / 0.25, rel=1e-6)


def test_calmar_is_undefined_when_nothing_ever_fell() -> None:
    """A ratio over a zero drawdown is infinite rather than excellent, and
    printing a large number there would be exactly the flattery these figures
    exist to prevent."""
    a_year = [
        Bar(ts=START + timedelta(days=365 * i), open=100.0, high=100.0, low=100.0,
            close=100.0, volume=1.0)
        for i in range(2)
    ]

    metrics = measure([_trade(100.0)], [1000.0, 1100.0], a_year, 1000.0, Interval.D1)

    assert metrics.max_drawdown == 0.0
    assert metrics.annualised is not None
    assert metrics.calmar is None
