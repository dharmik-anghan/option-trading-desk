"""The run: bars in, trades out.

Everything here is about not flattering the result. The loop itself is twenty
lines; the rest is the difference between a backtest and a sales pitch.

**A decision is made on a close and filled on the next open.** A rule reads bar i
after it has closed, so the earliest it could have acted is bar i+1, and the first
price available then is that bar's open. Filling at the close that produced the
signal is the commonest way a backtest invents money, because it buys at the price
that caused the decision.

**A bar cannot say what happened inside it.** If a bar's low reaches the stop and
its high reaches the target, one of them came first and the bar does not record
which. This always assumes the stop. Pessimistic on purpose: the alternative is a
result that quietly assumes the best of every ambiguous bar, and ambiguous bars
are exactly the volatile ones where it matters.

**Liquidation is checked before anything else.** A leveraged position that went
past its liquidation price during a bar was closed by the venue, whatever the rule
wanted. Checking the stop first would report a loss the trader chose instead of one
the exchange imposed.

**Costs are charged, not netted off at the end.** Fees at both fills, funding at
each settlement crossed, slippage on every fill. They come out of the equity curve
as they are incurred, so a drawdown is a real one.

The engine holds one position at a time. That is a real restriction and a
deliberate one: position sizing, pyramiding and portfolio effects are a separate
problem, and mixing them in here would make it impossible to tell whether a result
came from the idea or from the sizing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from backtest.market import Costs, Market, Side
from backtest.models import Action, Exit, Intent, Position, Trade
from backtest.resample import closes_at
from backtest.view import View, build, wind
from marketdata.models import Bar, Interval

log = logging.getLogger(__name__)


class Rule(Protocol):
    """A strategy.

    Two methods and no state that outlives a run. It is handed what was knowable
    and returns what it wants; it never sees the engine, the market or the money,
    which is why the same object can later be pointed at a live feed.
    """

    name: str
    #: Timeframes above the traded one this rule reads. Declared so the engine can
    #: align them, and so asking for one that was not declared is an error rather
    #: than a silent lookahead.
    context: tuple[Interval, ...]

    def entry(self, view: View) -> Intent:
        """Called on every closed bar while flat."""
        ...

    def exit(self, view: View, position: Position) -> Intent:
        """Called on every closed bar while in a position."""
        ...


@dataclass(frozen=True)
class Execution:
    """The assumptions a fill is made under.

    Written down rather than hardcoded because they are assumptions, and a result
    should be reproducible from the numbers that produced it.
    """

    #: Money to trade with, in the market's quote currency.
    capital: float = 1000.0
    #: Fraction of capital committed as margin per trade.
    risk: float = 1.0
    leverage: float = 1.0
    #: How far the fill lands from the price it was decided at, in basis points,
    #: always against you. Not a guess that can be checked, which is why it is a
    #: dial: at 5m on a liquid pair a basis point or two is generous, and a result
    #: that only works at zero is not a result.
    slippage_bps: float = 1.0
    #: Enter with a resting limit order at the price the decision was made at,
    #: rather than taking the next open. Halves the entry fee - 0.019% against
    #: 0.047% - and is a much stronger claim, so it is modelled rather than
    #: assumed: the order fills only if the next bar trades *through* the limit,
    #: not merely touches it. Touching it means the order was at the back of a
    #: queue that may never have been reached, and a rule that only works when
    #: every touch fills is a rule that only works on paper.
    #:
    #: Exits stay taker whatever this says. A stop is a market order by nature,
    #: and a target that has to be filled cannot wait for a queue.
    maker_entry: bool = False


@dataclass
class Result:
    """What a run produced."""

    rule: str
    symbol: str
    interval: Interval
    started: datetime | None = None
    ended: datetime | None = None
    bars: int = 0
    capital: float = 0.0
    trades: list[Trade] = field(default_factory=list)
    #: Equity after every bar, for the curve. Same length as the bars traded.
    equity: list[float] = field(default_factory=list)
    #: Anything the reader has to know to judge the numbers - a funding proxy, a
    #: position still open at the end, a window shorter than the warm-up.
    caveats: list[str] = field(default_factory=list)

    @property
    def final(self) -> float:
        return self.equity[-1] if self.equity else self.capital


def run(
    bars: Sequence[Bar],
    rule: Rule,
    market: Market,
    *,
    interval: Interval,
    execution: Execution | None = None,
) -> Result:
    """Walk the bars once, giving the rule only what had happened."""
    settings = execution or Execution()
    result = Result(
        rule=rule.name,
        symbol=market.symbol,
        interval=interval,
        capital=settings.capital,
        bars=len(bars),
    )
    if not bars:
        result.caveats.append("No bars, so nothing was tested")
        return result

    result.started = bars[0].ts
    result.ended = closes_at(bars[-1].ts, interval)

    view, cursors = build(bars, interval, rule.context)
    equity = settings.capital
    position: Position | None = None
    #: What the rule asked for at the previous bar's close, waiting to be filled
    #: at this bar's open. The one-bar delay, made physical.
    pending: Intent | None = None
    #: The price a resting entry would sit at, when entering as a maker.
    pending_limit: float | None = None

    for i, bar in enumerate(bars):
        # --- act on what was decided last bar, at this bar's open -----------
        if pending is not None:
            if pending.action is Action.ENTER and position is None:
                position = _open(pending, bar, market, settings, equity, limit=pending_limit)
            elif pending.action is Action.EXIT and position is not None:
                equity, trade = _close(
                    position, bar.open, bar.ts, Exit.RULE, pending.reason, market, settings, equity
                )
                result.trades.append(trade)
                position = None
            pending = None
            pending_limit = None

        # --- what the bar did to an open position ---------------------------
        if position is not None:
            position = _carry(position, bar, market, interval)
            ending = _ended_during(position, bar, market)
            if ending is not None:
                price, why = ending
                equity, trade = _close(
                    position, price, bar.ts, why, position.reason, market, settings, equity
                )
                result.trades.append(trade)
                position = None

        # --- decide, on this bar's close ------------------------------------
        wind(view, cursors, i)
        if position is None:
            intent = rule.entry(view)
            pending = intent if intent.action is Action.ENTER else None
            pending_limit = bar.close if (pending and settings.maker_entry) else None
        else:
            intent = rule.exit(view, position)
            pending = intent if intent.action is Action.EXIT else None

        result.equity.append(_equity_now(equity, position, bar.close, market))

    if position is not None:
        last = bars[-1]
        equity, trade = _close(
            position,
            last.close,
            last.ts,
            Exit.END_OF_DATA,
            "the data ran out",
            market,
            settings,
            equity,
        )
        result.trades.append(trade)
        result.equity[-1] = equity
        result.caveats.append(
            "A position was still open when the data ended; it is closed at the last "
            "price, which is a price nobody chose"
        )

    return result


def _slipped(price: float, side: Side, settings: Execution, *, opening: bool) -> float:
    """The fill, moved against the trade.

    Against in both directions: a long pays more to get in and receives less to get
    out. Slippage that helped would not be slippage.
    """
    drift = price * settings.slippage_bps / 10_000.0
    worse = side.sign if opening else -side.sign
    return price + drift * worse


def _open(
    intent: Intent,
    bar: Bar,
    market: Market,
    settings: Execution,
    equity: float,
    *,
    limit: float | None = None,
) -> Position | None:
    side = intent.side
    if side is None:
        return None

    if limit is None:
        price = _slipped(bar.open, side, settings, opening=True)
    else:
        # A resting order fills at its own price and no worse, so no slippage -
        # but only if the bar traded through it. Strictly through: a bar that
        # merely reached the limit says nothing about whether the queue in front
        # of it cleared.
        through = bar.low < limit if side is Side.LONG else bar.high > limit
        if not through:
            return None
        price = limit
    if price <= 0:
        return None

    # Size from equity rather than from starting capital, so a run compounds and a
    # losing run trades smaller - which is what an account actually does.
    committed = max(0.0, equity) * settings.risk
    if committed <= 0:
        return None
    notional = committed * settings.leverage
    quantity = notional / (price * market.multiplier)
    if quantity <= 0:
        return None

    fee = market.fee(quantity, price, maker=limit is not None, opening=True, side=side)
    slippage = abs(price - bar.open) * quantity * market.multiplier
    return Position(
        side=side,
        quantity=quantity,
        entry=price,
        opened_at=bar.ts,
        stop=intent.stop,
        target=intent.target,
        liquidation=market.liquidation(side, price, settings.leverage),
        reason=intent.reason,
        costs=Costs(fees=fee, slippage=slippage),
        carried_to=bar.ts,
    )


def _carry(position: Position, bar: Bar, market: Market, interval: Interval) -> Position:
    """Charge whatever holding cost accrued over this bar."""
    frm = position.carried_to or position.opened_at
    to = closes_at(bar.ts, interval)
    if to <= frm:
        return position
    cost = market.carry(position.side, position.quantity, frm, to, bar.close)
    position.costs = position.costs.plus(funding=cost)
    position.carried_to = to
    return position


def _ended_during(position: Position, bar: Bar, market: Market) -> tuple[float, Exit] | None:
    """Whether this bar closed the position, and at what price.

    Order matters and is the honest one: liquidation, then stop, then target. A
    bar that reached all three is reported as the worst of them, because a bar
    cannot say which came first and assuming otherwise is assuming profit.
    """
    low, high = bar.low, bar.high

    if position.liquidation is not None:
        hit = low <= position.liquidation if position.side is Side.LONG else (
            high >= position.liquidation
        )
        if hit:
            return position.liquidation, Exit.LIQUIDATION

    if position.stop is not None:
        hit = low <= position.stop if position.side is Side.LONG else high >= position.stop
        if hit:
            return position.stop, Exit.STOP

    if position.target is not None:
        hit = high >= position.target if position.side is Side.LONG else low <= position.target
        if hit:
            return position.target, Exit.TARGET

    return None


def _close(
    position: Position,
    price: float,
    at: datetime,
    why: Exit,
    reason: str,
    market: Market,
    settings: Execution,
    equity: float,
) -> tuple[float, Trade]:
    """Close, charge the exit, and hand back the new equity."""
    # A liquidation is not a price anyone chose, so no slippage is added to it -
    # the venue closed the position at that level by definition.
    filled = price if why is Exit.LIQUIDATION else _slipped(
        price, position.side, settings, opening=False
    )
    multiplier = market.multiplier
    # Always taker on the way out: a stop is a market order and a target that
    # must fill cannot sit in a queue.
    fee = market.fee(
        position.quantity, filled, maker=False, opening=False, side=position.side
    )
    slippage = abs(filled - price) * position.quantity * multiplier
    costs = position.costs.plus(fees=fee, slippage=slippage)
    gross = position.gross_at(filled, multiplier)

    trade = Trade(
        side=position.side,
        quantity=position.quantity,
        opened_at=position.opened_at,
        closed_at=at,
        entry=position.entry,
        exit_price=filled,
        why=why,
        reason=reason,
        costs=costs,
        gross=gross,
    )
    return equity + trade.net, trade


def _equity_now(equity: float, position: Position | None, price: float, market: Market) -> float:
    """Equity marked to the current price, so the curve shows an open loss.

    A curve that only moved when a trade closed would hide every drawdown taken
    inside a position, which is most of the drawdown there ever is.
    """
    if position is None:
        return equity
    multiplier = market.multiplier
    return equity + position.gross_at(price, multiplier) - position.costs.total
