"""Judging a run.

The figures are chosen to answer the questions that decide whether a rule is
worth trading, and one of them is not a performance statistic at all.

`cost_share` is the ratio of costs to gross profit. On a short-horizon rule it is
usually the whole story, and it separates the two failures that a single net
figure cannot: a rule whose gross is real and whose net is negative needs bigger
moves or fewer trades, while one whose gross is negative needs a different idea.

`buy_and_hold` is there so a result can be read at all. Over a window where the
instrument tripled, a rule that returned 40% lost money in the only sense that
matters. Every result carries it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from backtest.models import Exit, Trade
from marketdata.models import Bar, Interval


@dataclass(frozen=True)
class Metrics:
    """What a run came to."""

    trades: int
    wins: int
    #: Net of every cost, as a fraction of starting capital.
    total_return: float
    #: What holding the instrument over the same window returned.
    buy_and_hold: float
    #: Deepest peak-to-trough fall in equity, as a fraction of the peak. Marked to
    #: the bar, so it includes losses taken inside a position rather than only
    #: those realised on a close.
    max_drawdown: float
    #: Return divided by volatility, annualised. A ratio, not a promise: it
    #: assumes returns that are independent and roughly symmetric, and a rule with
    #: a stop has neither.
    sharpe: float
    gross: float
    fees: float
    funding: float
    slippage: float
    #: Costs over gross profit, when there was any. None when gross was negative,
    #: because a ratio against a negative number reads as though costs helped.
    cost_share: float | None
    #: Fraction of the run spent holding something.
    exposure: float
    best: float
    worst: float
    average_bars_held: float
    #: How each trade ended. The most diagnostic thing in here.
    endings: dict[str, int]

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def beat_holding(self) -> bool:
        return self.total_return > self.buy_and_hold


def measure(
    trades: Sequence[Trade],
    equity: Sequence[float],
    bars: Sequence[Bar],
    capital: float,
    interval: Interval,
) -> Metrics:
    """Every figure, from the curve and the trade list."""
    gross = sum(t.gross for t in trades)
    fees = sum(t.costs.fees for t in trades)
    funding = sum(t.costs.funding for t in trades)
    slippage = sum(t.costs.slippage for t in trades)
    costs = fees + funding + slippage

    final = equity[-1] if equity else capital
    held = sum(t.bars_held for t in trades)
    span = (
        (bars[-1].ts - bars[0].ts).total_seconds() + interval.seconds if bars else 0.0
    )

    endings: dict[str, int] = {}
    for reason in Exit:
        count = sum(1 for t in trades if t.why is reason)
        if count:
            endings[str(reason)] = count

    return Metrics(
        trades=len(trades),
        wins=sum(1 for t in trades if t.won),
        total_return=(final - capital) / capital if capital else 0.0,
        buy_and_hold=(bars[-1].close - bars[0].open) / bars[0].open if bars else 0.0,
        max_drawdown=max_drawdown(equity),
        sharpe=sharpe(equity, interval),
        gross=gross,
        fees=fees,
        funding=funding,
        slippage=slippage,
        cost_share=costs / gross if gross > 0 else None,
        exposure=held / span if span else 0.0,
        best=max((t.net for t in trades), default=0.0),
        worst=min((t.net for t in trades), default=0.0),
        average_bars_held=(held / len(trades) / interval.seconds) if trades else 0.0,
        endings=endings,
    )


def max_drawdown(equity: Sequence[float]) -> float:
    """Deepest fall from a peak, as a fraction of that peak.

    The number that decides whether a rule is holdable. A 60% return through a 45%
    drawdown is a rule almost nobody keeps trading through, and the average of the
    two says nothing about that.
    """
    worst = 0.0
    peak = -math.inf
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def sharpe(equity: Sequence[float], interval: Interval) -> float:
    """Annualised return over annualised volatility, at the risk-free rate of zero.

    Zero because the alternative is a rate that has to be sourced, dated and
    argued about, and at these horizons it moves the figure less than the choice
    of window does. Stated rather than hidden.
    """
    if len(equity) < 3:
        return 0.0
    returns: list[float] = []
    for before, after in zip(equity, equity[1:], strict=False):
        if before > 0:
            returns.append(after / before - 1.0)
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    if variance <= 0:
        return 0.0
    per_year = timedelta(days=365).total_seconds() / interval.seconds
    return mean / math.sqrt(variance) * math.sqrt(per_year)
