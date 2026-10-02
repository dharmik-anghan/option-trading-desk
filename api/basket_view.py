"""A basket as the API shows it: legs priced, totals summed, payoff drawn.

One place, because two readers need the same view - the baskets endpoints, and
the alert pass judging the structures with the browser closed. When each built
its own, the desk and the alert that warned about it could disagree.
"""

from __future__ import annotations

import math
from dataclasses import replace

from analytics.payoff import payoff_curve_points
from api.schemas import BasketLegResponse, BasketResponse, PayoffPoint
from broker.contracts import ContractCodec
from broker.models import OptionChain, OptionChainRow
from execution.basket_history import closed_at as basket_closed_at
from execution.basket_history import realized as basket_realized
from execution.basket_status import get_basket_payoff
from storage.basket_repo import Basket, BasketLeg


def spans_several_expiries(basket: Basket, codec: ContractCodec) -> bool:
    """Whether the open legs sit in more than one expiry.

    Read from the symbols rather than asked of the broker, so it holds even
    without a live chain. A leg whose symbol cannot be read is ignored: better
    to treat an unrecognised shape as single-expiry, which is the common case,
    than to blank a payoff over a parsing miss.
    """
    prefixes = {
        prefix
        for leg in basket.legs
        if leg.is_open
        for prefix in [codec.series_prefix(leg.symbol, leg.strike)]
        if prefix is not None
    }
    return len(prefixes) > 1


def _leg_response(leg: BasketLeg, row: OptionChainRow | None) -> BasketLegResponse:
    """One basket leg, with its live figures when a chain row was available."""
    greeks = row.greeks if row is not None else None
    return BasketLegResponse(
        id=leg.id,
        symbol=leg.symbol,
        option_type=leg.option_type,
        strike=leg.strike,
        side=leg.side,
        quantity=leg.quantity,
        entry_price=leg.entry_price,
        entry_at=leg.entry_at.isoformat(),
        exit_price=leg.exit_price,
        exit_at=leg.exit_at.isoformat() if leg.exit_at else None,
        is_open=leg.is_open,
        ltp=row.ltp if row is not None else None,
        delta=greeks.delta if greeks is not None else None,
        gamma=greeks.gamma if greeks is not None else None,
        theta=greeks.theta if greeks is not None else None,
        vega=greeks.vega if greeks is not None else None,
        iv=greeks.iv if greeks is not None else None,
        ltp_change=row.ltp_change if row is not None else None,
        oi_change=row.oi_change if row is not None else None,
    )


def _structure_totals(
    legs: list[BasketLegResponse],
) -> tuple[float | None, float | None, float | None]:
    """What the open legs are worth now, and how the structure leans.

    Computed here rather than in the browser, which is where both used to be
    worked out. Two places deriving the same figure is how the desk and the alert
    that warns about it come to disagree - and now the rules need them too, so
    there would have been three.

    A short leg subtracts: selling premium shows positive theta and negative
    delta on a call, which is the shape of the trade. Any is None when the broker
    has not priced every open leg, because a partial total read as a whole one is
    a number that looks fine and is wrong.

    Delta comes back twice, because the two answer different questions and only
    differ by a constant - which is exactly why they get confused:

    - per contract, the directional sum of the quoted deltas. On the condor:
      +0.32 -0.19 -0.23 +0.10. This is the figure on the legs table, the scale a
      trader speaks in, and what a delta limit is set against.
    - weighted by contracts, which is the position's actual exposure and the only
      one that converts to money: 65 lots of 0.32 is 20.80 index points per unit
      move, not 0.32.

    A balanced structure is zero in both, which is how one can be mistaken for
    the other until something drifts.
    """
    open_legs = [leg for leg in legs if leg.is_open]
    if not open_legs:
        return None, None, None

    def direction(leg: BasketLegResponse) -> int:
        return 1 if leg.side == "BUY" else -1

    marks = [leg.ltp for leg in open_legs]
    deltas = [leg.delta for leg in open_legs]

    mtm: float | None = None
    if all(mark is not None for mark in marks):
        mtm = sum(
            direction(leg) * (mark - leg.entry_price) * leg.quantity
            for leg, mark in zip(open_legs, marks, strict=True)
            if mark is not None
        )
    net_delta: float | None = None
    per_contract: float | None = None
    if all(delta is not None for delta in deltas):
        net_delta = sum(
            direction(leg) * leg.quantity * delta
            for leg, delta in zip(open_legs, deltas, strict=True)
            if delta is not None
        )
        per_contract = sum(
            direction(leg) * delta
            for leg, delta in zip(open_legs, deltas, strict=True)
            if delta is not None
        )
    return mtm, net_delta, per_contract


def basket_view(
    basket: Basket,
    codec: ContractCodec,
    live: tuple[list[PayoffPoint], float | None, str | None] = ([], None, None),
    rows: dict[str, OptionChainRow] | None = None,
    spot: float | None = None,
) -> BasketResponse:
    payoff = get_basket_payoff(basket)
    today, days, expiry_date = live
    rows = rows or {}
    spans_expiries = spans_several_expiries(basket, codec)
    if spans_expiries:
        # Everything below is derived from intrinsic value at a single expiry.
        # For a calendar that is not merely imprecise, it is wrong: the far leg
        # still has months of time value the model prices at zero, so the whole
        # net debit is reported as a certain loss. Better to show nothing.
        payoff = replace(payoff, max_profit=0.0, max_loss=0.0, breakevens=[], legs=[])
        today = []
    legs = [_leg_response(leg, rows.get(leg.symbol)) for leg in basket.legs]
    mtm, net_delta, per_contract = _structure_totals(legs)
    banked = basket_realized(basket)
    ended = basket_closed_at(basket)
    return BasketResponse(
        id=basket.id,
        name=basket.name,
        strategy=basket.strategy,
        underlying_symbol=basket.underlying_symbol,
        created_at=basket.created_at.isoformat(),
        stop_loss=basket.stop_loss,
        profit_target=basket.profit_target,
        delta_limit=basket.delta_limit,
        worst_case_limit=basket.worst_case_limit,
        short_delta_limit=basket.short_delta_limit,
        expiry_warn_days=basket.expiry_warn_days,
        mtm=mtm,
        net_delta=net_delta,
        net_delta_per_contract=per_contract,
        realized=banked,
        total_pnl=(
            banked + mtm if mtm is not None else (banked if ended is not None else None)
        ),
        closed_at=ended.isoformat() if ended else None,
        legs=legs,
        max_profit=None if math.isinf(payoff.max_profit) else payoff.max_profit,
        max_loss=None if math.isinf(payoff.max_loss) else payoff.max_loss,
        breakevens=payoff.breakevens,
        payoff_curve=[
            PayoffPoint(spot=x, payoff=value) for x, value in payoff_curve_points(payoff, spot)
        ],
        payoff_curve_today=today,
        days_to_expiry=days,
        expiry_date=expiry_date,
        single_expiry=not spans_expiries,
    )


def spot_for(
    basket: Basket, chains: dict[tuple[str, str], OptionChain]
) -> float | None:
    """The underlying's price, from whichever chain was fetched for it."""
    for (underlying, _token), chain in chains.items():
        if underlying == basket.underlying_symbol:
            return chain.underlying_ltp
    return None


def rows_for_basket(
    basket: Basket, chains: dict[tuple[str, str], OptionChain]
) -> dict[str, OptionChainRow]:
    """Chain rows keyed by contract symbol, from whichever chains were fetched."""
    rows: dict[str, OptionChainRow] = {}
    wanted = {leg.symbol for leg in basket.legs}
    for (underlying, _token), chain in chains.items():
        if underlying != basket.underlying_symbol:
            continue
        for row in chain.rows:
            if row.symbol in wanted:
                rows[row.symbol] = row
    return rows
