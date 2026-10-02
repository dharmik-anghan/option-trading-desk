"""Open structures: recording them, pricing them, and taking them apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from api.basket_view import basket_view, rows_for_basket, spot_for
from api.deps import BrokerDep, CodecDep, DbPathDep
from api.pricing import (
    basket_live_curve,
)
from api.schemas import (
    BasketResponse,
    CloseLegRequest,
    CreateBasketRequest,
)
from api.store import open_db
from broker.base import FillHistory
from broker.models import OptionChain
from execution.basket_history import history as basket_history
from execution.basket_status import get_basket_payoff
from execution.broker_sync import assign as assign_fills
from execution.broker_sync import ignore as ignore_fills
from execution.broker_sync import pending_with_suggestions
from execution.broker_sync import sync as sync_fills
from storage.basket_repo import (
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
    return basket_view(basket, codec)

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
        return [basket_view(b, codec) for b in baskets]
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
            basket_view(basket, codec,
                valued,
                rows_for_basket(basket, chains),
                spot_for(basket, chains),
            )
        )
    return out


@router.get("/api/baskets/{basket_id}", response_model=BasketResponse)
def get_basket_endpoint(basket_id: int, db_path: DbPathDep, codec: CodecDep) -> BasketResponse:
    conn = open_db(db_path)
    basket = get_basket(conn, basket_id)
    conn.close()
    if basket is None:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")
    return basket_view(basket, codec)

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
    return basket_view(basket, codec)

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
    return basket_view(basket, codec)


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
    return basket_view(basket, codec)


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
    return basket_view(basket, codec)


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
