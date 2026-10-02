"""What a run came to.

In rupees, not percent. Return on capital needs a margin figure, and for short
options the margin is the capital; until a margin model exists, a percentage would
be a percentage of something made up. Rupees per run, at the lots configured, are
at least exactly what they say.

Two figures exist to stop self-deception rather than to flatter:

  `worst_share` - how much of the total profit the five worst trades gave back.
  Premium selling has a beautiful average and a tail that closes accounts; a
  strategy where a handful of days consume most of a year's profit is one the
  other figures would call excellent.

  `cost_share` - charges as a share of gross profit. On an intraday straddle it
  is routinely most of the story.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from optbt.engine import Result, Trade


@dataclass(frozen=True)
class Summary:
    trades: int
    wins: int
    gross: float
    charges: float
    net: float
    average: float
    median: float
    best: float
    worst: float
    profit_factor: float | None
    #: Deepest fall from a peak in cumulative daily equity, in rupees.
    max_drawdown: float
    worst_share: float | None
    cost_share: float | None
    exits: dict[str, int]
    by_year: dict[int, float]
    abandoned_orders: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


def summarise(result: Result, *, tail: int = 5) -> Summary:
    trades = [t for t in result.trades if t.closed is not None]
    nets = [t.net for t in trades]
    gains = sum(n for n in nets if n > 0)
    losses = -sum(n for n in nets if n < 0)
    gross = sum(t.gross for t in trades)
    charges = sum(t.charges.total for t in trades)
    net = sum(nets)

    peak = drawdown = 0.0
    for _, value in result.equity:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)

    worst = sorted(nets)[:tail]
    by_year: dict[int, float] = defaultdict(float)
    for t in trades:
        by_year[t.opened.year] += t.net

    return Summary(
        trades=len(trades),
        wins=sum(1 for n in nets if n > 0),
        gross=gross,
        charges=charges,
        net=net,
        average=net / len(trades) if trades else 0.0,
        median=statistics.median(nets) if nets else 0.0,
        best=max(nets, default=0.0),
        worst=min(nets, default=0.0),
        profit_factor=gains / losses if losses else None,
        max_drawdown=drawdown,
        worst_share=-sum(n for n in worst if n < 0) / gains if gains else None,
        cost_share=charges / gross if gross > 0 else None,
        exits=dict(Counter(_exit(t) for t in trades)),
        by_year=dict(sorted(by_year.items())),
        abandoned_orders=result.abandoned_orders,
    )


def _exit(trade: Trade) -> str:
    """How the trade ended, per leg: 'stop+time' is one leg stopped, one timed out."""
    return "+".join(sorted(leg.exit_reason or "?" for leg in trade.legs))


def report(summary: Summary) -> str:
    s = summary
    pf = f"{s.profit_factor:.2f}" if s.profit_factor is not None else "-"
    lines = [
        f"trades        {s.trades}   won {s.wins} ({s.win_rate:.0%})",
        f"gross         {s.gross:>12,.0f}",
        f"charges       {s.charges:>12,.0f}"
        + (f"   ({s.cost_share:.0%} of gross)" if s.cost_share is not None else ""),
        f"net           {s.net:>12,.0f}",
        f"per trade     avg {s.average:,.0f}   median {s.median:,.0f}   "
        f"best {s.best:,.0f}   worst {s.worst:,.0f}",
        f"profit factor {pf}",
        f"max drawdown  {s.max_drawdown:>12,.0f}",
    ]
    if s.worst_share is not None:
        lines.append(f"worst 5       gave back {s.worst_share:.0%} of all winning trades' profit")
    lines.append("exits         " + ", ".join(f"{k} {v}" for k, v in sorted(s.exits.items())))
    lines.append("by year       " + ", ".join(f"{y} {v:,.0f}" for y, v in s.by_year.items()))
    if s.abandoned_orders:
        lines.append(f"abandoned     {s.abandoned_orders} orders found no price")
    return "\n".join(lines)
