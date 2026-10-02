"""Open structures: recording them, pricing them, and taking them apart.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from analytics.payoff import (
    payoff_curve_points,
)
from api.deps import BrokerDep, CodecDep, DbPathDep
from api.pricing import (
    basket_live_curve,
)
from api.schemas import (
    BasketLegResponse,
    BasketResponse,
    CloseLegRequest,
    CreateBasketRequest,
    PayoffPoint,
)
from api.store import open_db
from broker.base import FillHistory
from broker.contracts import ContractCodec
from broker.models import OptionChain, OptionChainRow
from execution.basket_history import closed_at as basket_closed_at
from execution.basket_history import history as basket_history
from execution.basket_history import realized as basket_realized
from execution.basket_status import get_basket_payoff
from execution.broker_sync import assign as assign_fills
from execution.broker_sync import ignore as ignore_fills
from execution.broker_sync import pending_with_suggestions
from execution.broker_sync import sync as sync_fills
from storage.basket_repo import (
    Basket,
    BasketLeg,
    NewBasketLeg,
    add_leg,
    create_basket,
    get_basket,
    set_basket_levels,
)
from storage.basket_repo import close_leg as repo_close_leg
from storage.basket_repo import delete_basket as repo_delete_basket
from storage.basket_repo import delete_leg as repo_delete_leg
from storage.basket_repo import list_baskets as repo_list_baskets
from venues.calendar import IST

router = APIRouter()


def _spans_expiries(basket: Basket, codec: ContractCodec) -> bool:
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


def _basket_to_response(
    basket: Basket,
    codec: ContractCodec,
    live: tuple[list[PayoffPoint], float | None, str | None] = ([], None, None),
    rows: dict[str, OptionChainRow] | None = None,
    spot: float | None = None,
) -> BasketResponse:
    payoff = get_basket_payoff(basket)
    today, days, expiry_date = live
    rows = rows or {}
    spans_expiries = _spans_expiries(basket, codec)
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

@router.post("/api/baskets", response_model=BasketResponse)
def create_basket_endpoint(
    request: CreateBasketRequest, db_path: DbPathDep, codec: CodecDep
) -> BasketResponse:
    conn = open_db(db_path)
    basket_id = create_basket(
        conn,
        name=request.name,
        strategy=request.strategy,
        underlying_symbol=request.underlying_symbol,
        legs=[
            NewBasketLeg(
                symbol=leg.symbol,
                option_type=leg.option_type,
                strike=leg.strike,
                side=leg.side,
                quantity=leg.quantity,
                entry_price=leg.entry_price,
            )
            for leg in request.legs
        ],
        created_at=datetime.now(UTC),
        stop_loss=request.stop_loss,
    )
    basket = get_basket(conn, basket_id)
    conn.close()
    assert basket is not None
    return _basket_to_response(basket, codec)

@router.get("/api/baskets", response_model=list[BasketResponse])
def list_baskets_endpoint(
    db_path: DbPathDep, codec: CodecDep, broker: BrokerDep, live: bool = False
) -> list[BasketResponse]:
    """`live=true` also prices each basket where it stands now, not just at
    expiry. That costs broker calls - one chain per distinct expiry held - so
    it is opt-in rather than the default."""
    conn = open_db(db_path)
    baskets = repo_list_baskets(conn)
    conn.close()
    if not live:
        return [_basket_to_response(b, codec) for b in baskets]
    # shared across baskets, so two structures on one expiry cost one request
    chains: dict[tuple[str, str], OptionChain] = {}
    out: list[BasketResponse] = []
    for basket in baskets:
        payoff = get_basket_payoff(basket)
        try:
            valued = basket_live_curve(basket, payoff, broker, codec, chains)
        except Exception:  # noqa: BLE001 - a live extra must never 500 the list
            valued = ([], None, None)
        out.append(
            _basket_to_response(basket, codec,
                valued,
                _rows_for_basket(basket, chains),
                _spot_for(basket, chains),
            )
        )
    return out

def _spot_for(
    basket: Basket, chains: dict[tuple[str, str], OptionChain]
) -> float | None:
    """The underlying's price, from whichever chain was fetched for it."""
    for (underlying, _token), chain in chains.items():
        if underlying == basket.underlying_symbol:
            return chain.underlying_ltp
    return None

def _rows_for_basket(
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

@router.get("/api/baskets/{basket_id}", response_model=BasketResponse)
def get_basket_endpoint(basket_id: int, db_path: DbPathDep, codec: CodecDep) -> BasketResponse:
    conn = open_db(db_path)
    basket = get_basket(conn, basket_id)
    conn.close()
    if basket is None:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")
    return _basket_to_response(basket, codec)

@router.post("/api/baskets/{basket_id}/legs/{leg_id}/close", response_model=BasketResponse)
def close_leg_endpoint(
    basket_id: int, leg_id: int, request: CloseLegRequest, db_path: DbPathDep, codec: CodecDep
) -> BasketResponse:
    conn = open_db(db_path)
    repo_close_leg(conn, leg_id, exit_price=request.exit_price, exit_at=datetime.now(UTC))
    basket = get_basket(conn, basket_id)
    conn.close()
    if basket is None:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")
    return _basket_to_response(basket, codec)

@router.delete("/api/baskets/{basket_id}", status_code=204)
def delete_basket_endpoint(basket_id: int, db_path: DbPathDep) -> None:
    """Forget a grouping. Does not touch the broker - nothing is squared off."""
    conn = open_db(db_path)
    existed = repo_delete_basket(conn, basket_id)
    conn.close()
    if not existed:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")

@router.delete("/api/baskets/{basket_id}/legs/{leg_id}", status_code=204)
def delete_leg_endpoint(basket_id: int, leg_id: int, db_path: DbPathDep) -> None:
    """Take a leg out of a basket it never belonged to.

    Closing a leg that was genuinely exited is a different thing - that is
    POST .../close, which keeps the leg and its exit price on the basket.
    """
    conn = open_db(db_path)
    existed = repo_delete_leg(conn, basket_id, leg_id)
    conn.close()
    if not existed:
        raise HTTPException(status_code=404, detail=f"Leg {leg_id} not found in basket {basket_id}")


class LevelsRequest(BaseModel):
    """Alert levels for one structure. Null clears a level.

    Deliberately not validated as positive. A stop is a loss and reads naturally
    as a negative number, a target is a gain, and a delta limit is a magnitude -
    one rule for all three would refuse a sensible value in at least one of them.
    Zero is refused, though: a level of zero is indistinguishable from no level,
    and would fire the moment a structure ticked past break-even.
    """

    stop_loss: float | None = None
    profit_target: float | None = None
    delta_limit: float | None = None
    worst_case_limit: float | None = None
    short_delta_limit: float | None = None
    expiry_warn_days: float | None = None

    @model_validator(mode="after")
    def _no_zero_levels(self) -> LevelsRequest:
        for name in (
            "stop_loss",
            "profit_target",
            "delta_limit",
            "worst_case_limit",
            "short_delta_limit",
        ):
            value = getattr(self, name)
            if value is not None and value == 0:
                raise ValueError(f"{name} of zero is not a level; send null to clear it")
        return self


@router.put("/api/baskets/{basket_id}/levels", response_model=BasketResponse)
def put_levels(
    basket_id: int, body: LevelsRequest, db_path: DbPathDep, codec: CodecDep
) -> BasketResponse:
    """Set this structure's own alert levels.

    Returns the structure rather than the levels, so the panel that just edited
    them redraws from one answer instead of stitching a response into what it
    already had.
    """
    conn = open_db(db_path)
    try:
        if not set_basket_levels(
            conn,
            basket_id,
            stop_loss=body.stop_loss,
            profit_target=body.profit_target,
            delta_limit=body.delta_limit,
            worst_case_limit=body.worst_case_limit,
            short_delta_limit=body.short_delta_limit,
            # Zero days is a real answer here - "warn me on expiry day" - so it is
            # not in the no-zero list above.
            expiry_warn_days=body.expiry_warn_days,
        ):
            raise HTTPException(status_code=404, detail="no such basket")
        basket = get_basket(conn, basket_id)
    finally:
        conn.close()
    assert basket is not None
    return _basket_to_response(basket, codec)


# --------------------------------------------------- following the broker
#
# Everything below reads from the broker and writes only to the desk's own
# database. No order is placed, changed or cancelled by any of it: the sync is
# handed the broker for `get_fills` and `get_positions` alone.

class SuggestionOut(BaseModel):
    basket_id: int
    basket_name: str
    why: str


class PendingFillOut(BaseModel):
    fill_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    at: str
    suggestion: SuggestionOut | None


class ClosedOut(BaseModel):
    basket_id: int
    basket_name: str
    symbol: str
    quantity: int
    price: float
    at: str
    realized: float


class UnexplainedOut(BaseModel):
    basket_id: int
    basket_name: str
    leg_id: int
    symbol: str
    side: str
    quantity: int
    broker_quantity: float


class SyncResponse(BaseModel):
    since: str
    fills_read: int
    already_seen: int
    closed: list[ClosedOut]
    covered: int
    outside: int
    pending: list[PendingFillOut]
    unexplained: list[UnexplainedOut]


def _pending_out(p: object) -> PendingFillOut:
    from execution.broker_sync import Pending

    assert isinstance(p, Pending)
    return PendingFillOut(
        fill_id=p.fill_id,
        symbol=p.symbol,
        side=p.side,
        quantity=p.quantity,
        price=p.price,
        at=p.at.isoformat(),
        suggestion=SuggestionOut(**vars(p.suggestion)) if p.suggestion else None,
    )


@router.post("/api/baskets/sync", response_model=SyncResponse)
def sync_with_broker(
    db_path: DbPathDep, codec: CodecDep, broker: BrokerDep, days: int = 7
) -> SyncResponse:
    """Read the broker's fills and apply them to structures.

    Closes are applied; new positions come back as pending, with a suggested
    structure, for you to confirm. Safe to call as often as you like: a fill
    already applied is never applied again.
    """
    if not isinstance(broker, FillHistory):
        raise HTTPException(status_code=400, detail="this venue does not report fills")
    today = datetime.now(IST).date()
    conn = open_db(db_path)
    try:
        report = sync_fills(
            conn,
            broker,
            codec=codec,
            since=today - timedelta(days=max(0, min(days, 60))),
            until=today,
            now=datetime.now(UTC),
        )
    finally:
        conn.close()
    return SyncResponse(
        since=report.since.isoformat(),
        fills_read=report.fills_read,
        already_seen=report.already_seen,
        closed=[
            ClosedOut(basket_id=a.basket_id, basket_name=a.basket_name, symbol=a.fill.symbol,
                      quantity=a.quantity, price=a.fill.price, at=a.fill.at.isoformat(),
                      realized=a.realized)
            for a in report.closed
        ],
        covered=report.covered,
        outside=report.outside,
        pending=[_pending_out(p) for p in report.pending],
        unexplained=[
            UnexplainedOut(basket_id=u.basket_id, basket_name=u.basket_name, leg_id=u.leg.id,
                           symbol=u.leg.symbol, side=u.leg.side, quantity=u.leg.quantity,
                           broker_quantity=u.broker_quantity)
            for u in report.unexplained
        ],
    )


@router.get("/api/baskets/fills/pending", response_model=list[PendingFillOut])
def pending_fills(db_path: DbPathDep, codec: CodecDep) -> list[PendingFillOut]:
    conn = open_db(db_path)
    try:
        pending = pending_with_suggestions(conn, repo_list_baskets(conn), codec)
    finally:
        conn.close()
    return [_pending_out(p) for p in pending]


class AssignRequest(BaseModel):
    fill_ids: list[str]
    #: An existing structure, or `name` for a new one.
    basket_id: int | None = None
    name: str | None = None
    strategy: str = "Custom"


@router.post("/api/baskets/fills/assign", response_model=BasketResponse)
def assign_pending(body: AssignRequest, db_path: DbPathDep, codec: CodecDep) -> BasketResponse:
    """Put pending fills into a structure as new legs. Desk database only."""
    conn = open_db(db_path)
    try:
        try:
            basket_id = assign_fills(
                conn, body.fill_ids, codec=codec, basket_id=body.basket_id, new_name=body.name,
                new_strategy=body.strategy, now=datetime.now(UTC),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        basket = get_basket(conn, basket_id)
    finally:
        conn.close()
    assert basket is not None
    return _basket_to_response(basket, codec)


class FillIdsRequest(BaseModel):
    fill_ids: list[str]


@router.post("/api/baskets/fills/ignore", status_code=204)
def ignore_pending(body: FillIdsRequest, db_path: DbPathDep) -> None:
    conn = open_db(db_path)
    try:
        ignore_fills(conn, body.fill_ids)
    finally:
        conn.close()


class AddLegRequest(BaseModel):
    """A leg entered by hand: a position taken somewhere the sync cannot see, or
    a correction. Strike and type are read from the symbol."""

    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    entry_price: float
    entry_at: datetime | None = None


@router.post("/api/baskets/{basket_id}/legs", response_model=BasketResponse)
def add_leg_endpoint(
    basket_id: int, body: AddLegRequest, db_path: DbPathDep, codec: CodecDep
) -> BasketResponse:
    parsed = codec.parse_contract(body.symbol)
    if parsed is None:
        raise HTTPException(status_code=422, detail=f"{body.symbol} is not an option contract")
    if body.quantity <= 0 or body.entry_price < 0:
        raise HTTPException(status_code=422, detail="quantity must be positive")
    _, strike, kind = parsed
    at = body.entry_at or datetime.now(UTC)
    if at.tzinfo is None:
        at = at.replace(tzinfo=IST)
    conn = open_db(db_path)
    try:
        if get_basket(conn, basket_id) is None:
            raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")
        add_leg(conn, basket_id, NewBasketLeg(
            symbol=body.symbol, option_type=kind, strike=strike, side=body.side,
            quantity=body.quantity, entry_price=body.entry_price, entry_at=at,
        ))
        basket = get_basket(conn, basket_id)
    finally:
        conn.close()
    assert basket is not None
    return _basket_to_response(basket, codec)


class HistoryEventOut(BaseModel):
    at: str
    action: str
    leg_id: int
    symbol: str
    side: str
    quantity: int
    price: float
    realized: float | None


class MomentOut(BaseModel):
    at: str
    kind: str
    events: list[HistoryEventOut]
    realized: float
    premium: float
    realized_to_date: float


@router.get("/api/baskets/{basket_id}/history", response_model=list[MomentOut])
def basket_history_endpoint(basket_id: int, db_path: DbPathDep) -> list[MomentOut]:
    conn = open_db(db_path)
    try:
        basket = get_basket(conn, basket_id)
    finally:
        conn.close()
    if basket is None:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")
    return [
        MomentOut(
            at=m.at.isoformat(), kind=m.kind, realized=m.realized, premium=m.premium,
            realized_to_date=m.realized_to_date,
            events=[
                HistoryEventOut(at=e.at.isoformat(), action=e.action, leg_id=e.leg_id,
                                symbol=e.symbol, side=e.side, quantity=e.quantity,
                                price=e.price, realized=e.realized)
                for e in m.events
            ],
        )
        for m in basket_history(basket)
    ]
