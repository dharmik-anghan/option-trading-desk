"""Placing a real order.

The one router that can spend money. Checks are enforced here, server-side,
not merely shown by the review endpoint - a client-side gate is bypassable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from api.deps import BrokerDep, DbPathDep
from api.schemas import (
    OrderResultResponse,
    PlaceOrderRequest,
    PlaceOrderResponse,
)
from api.store import open_db
from execution.manager import ExecutionManager
from execution.strategy_review import UnknownStrategy, review
from storage.basket_repo import NewBasketLeg, create_basket

router = APIRouter()


@router.post("/api/orders/place", response_model=PlaceOrderResponse)
def place_order(
    request: PlaceOrderRequest, broker: BrokerDep, db_path: DbPathDep
) -> PlaceOrderResponse:
    try:
        reviewed = review(
            request.strategy, request.symbol, request.quantity, broker, request.expiry or ""
        )
    except UnknownStrategy:
        raise HTTPException(
            status_code=404, detail=f"Unknown strategy '{request.strategy}'"
        ) from None
    legs, pre_trade = reviewed.legs, reviewed.pre_trade

    if not pre_trade.passed:
        failed_reasons = [c.reason for c in pre_trade.checks if not c.passed]
        raise HTTPException(
            status_code=400,
            detail={"message": "Pre-trade checks failed", "reasons": failed_reasons},
        )

    manager = ExecutionManager(broker)
    orders = manager.build_orders(legs)
    results = manager.place_all(orders)

    now = datetime.now(UTC)
    conn = open_db(db_path)
    basket_id = create_basket(
        conn,
        name=request.basket_name or f"{request.strategy} {request.symbol} {now.date()}",
        strategy=request.strategy,
        underlying_symbol=request.symbol,
        legs=[
            NewBasketLeg(
                symbol=leg.symbol or "",
                option_type=leg.option_type,
                strike=leg.strike,
                side=leg.side,
                quantity=leg.quantity,
                entry_price=leg.premium,
            )
            for leg in legs
        ],
        created_at=now,
    )
    conn.close()

    return PlaceOrderResponse(
        orders=[OrderResultResponse(order_id=r.order_id, message=r.message) for r in results],
        basket_id=basket_id,
    )
