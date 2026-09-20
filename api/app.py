"""Dashboard API.

Order placement lives here now (`POST /api/orders/place`) - this used to be
CLI-only via scripts/place_strategy_order.py, which is removed. The CLI's
`CONFIRM`-typed safety gate doesn't translate directly to a browser; the
replacement is: the review step (`GET /api/strategies/{name}`) always
returns the same pre-trade check results the placement endpoint will
enforce, and placement itself is refused server-side (400) if those checks
fail - never just hidden behind a disabled button, since a client-side-only
gate is trivially bypassable. The deliberate-click part of the safety
model now lives in the frontend's review screen, not in this API.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from analytics.payoff import Leg, PayoffResult, analyze
from api.dependencies import get_broker, get_db_path
from api.schemas import (
    BasketLegResponse,
    BasketResponse,
    CloseLegRequest,
    CreateBasketRequest,
    LegResponse,
    OrderResultResponse,
    PlaceOrderRequest,
    PlaceOrderResponse,
    PortfolioHistoryPoint,
    PortfolioResponse,
    RiskCheckResponse,
    StrategySignalResponse,
)
from broker.base import Broker
from broker.models import OptionChain
from execution.basket_status import get_basket_payoff
from execution.manager import ExecutionManager
from execution.portfolio_status import get_portfolio_status
from risk.pre_trade_check import (
    DEFAULT_MAX_LOSS_LIMIT,
    DEFAULT_MAX_RISK_PCT,
    DEFAULT_REQUIRED_MARGIN_PLACEHOLDER,
    PreTradeCheckResult,
    run_pre_trade_checks,
)
from storage.basket_repo import Basket, NewBasketLeg, create_basket, get_basket
from storage.basket_repo import close_leg as repo_close_leg
from storage.basket_repo import list_baskets as repo_list_baskets
from storage.db import connect, init_schema
from storage.portfolio_repo import snapshots_since
from strategies.base import Strategy
from strategies.credit_spread import CreditSpread
from strategies.iron_condor import IronCondor
from strategies.short_strangle import ShortStrangle

app = FastAPI(title="Option Strategy Dashboard API")

# Local dev only: the Vite dev server runs on a different port than uvicorn.
# Tighten this (or drop it behind a reverse proxy) before exposing this
# beyond localhost - see docs/SETUP.md.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

BrokerDep = Annotated[Broker, Depends(get_broker)]
DbPathDep = Annotated[Path, Depends(get_db_path)]


def _strategies() -> dict[str, Strategy]:
    # Built fresh per request rather than module-level, so each request
    # gets its own strategy instance (they're mutable dataclasses).
    return {
        "short_strangle": ShortStrangle(),
        "iron_condor": IronCondor(),
        "credit_spread_bullish": CreditSpread(direction="bullish"),
        "credit_spread_bearish": CreditSpread(direction="bearish"),
    }


def _evaluate(
    name: str, symbol: str, quantity: int, broker: Broker
) -> tuple[OptionChain, list[Leg], PayoffResult, PreTradeCheckResult]:
    strategy = _strategies().get(name)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Unknown strategy '{name}'")
    strategy.quantity = quantity  # type: ignore[attr-defined]

    chain = broker.get_option_chain(symbol, strike_count=15)
    legs = strategy.build_legs(chain)
    payoff = analyze(legs)

    funds = broker.get_funds()
    pre_trade = run_pre_trade_checks(
        payoff=payoff,
        available_funds=funds.available_balance,
        required_margin=DEFAULT_REQUIRED_MARGIN_PLACEHOLDER,
        capital=funds.total_balance,
        max_risk_pct=DEFAULT_MAX_RISK_PCT,
        max_loss_limit=DEFAULT_MAX_LOSS_LIMIT,
    )
    return chain, legs, payoff, pre_trade


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    init_schema(conn)
    return conn


def _basket_to_response(basket: Basket) -> BasketResponse:
    payoff = get_basket_payoff(basket)
    return BasketResponse(
        id=basket.id,
        name=basket.name,
        strategy=basket.strategy,
        underlying_symbol=basket.underlying_symbol,
        created_at=basket.created_at.isoformat(),
        stop_loss=basket.stop_loss,
        legs=[
            BasketLegResponse(
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
            )
            for leg in basket.legs
        ],
        max_profit=None if math.isinf(payoff.max_profit) else payoff.max_profit,
        max_loss=None if math.isinf(payoff.max_loss) else payoff.max_loss,
        breakevens=payoff.breakevens,
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/portfolio", response_model=PortfolioResponse)
def portfolio(broker: BrokerDep) -> PortfolioResponse:
    status = get_portfolio_status(broker)
    return PortfolioResponse(
        positions=status.positions,
        realized_pnl=status.realized_pnl,
        unrealized_pnl=status.unrealized_pnl,
        total_pnl=status.total_pnl,
    )


@app.get("/api/option-chain/{symbol:path}", response_model=OptionChain)
def option_chain(symbol: str, broker: BrokerDep, strike_count: int = 15) -> OptionChain:
    return broker.get_option_chain(symbol, strike_count=strike_count)


@app.get("/api/strategies/{name}", response_model=StrategySignalResponse)
def strategy_signal(
    name: str,
    symbol: str,
    broker: BrokerDep,
    quantity: int = 1,
) -> StrategySignalResponse:
    chain, legs, result, pre_trade = _evaluate(name, symbol, quantity, broker)

    return StrategySignalResponse(
        strategy=name,
        symbol=symbol,
        underlying_ltp=chain.underlying_ltp,
        legs=[LegResponse(**vars(leg)) for leg in legs],
        max_profit=None if math.isinf(result.max_profit) else result.max_profit,
        max_loss=None if math.isinf(result.max_loss) else result.max_loss,
        breakevens=result.breakevens,
        pre_trade_checks=[
            RiskCheckResponse(passed=c.passed, reason=c.reason) for c in pre_trade.checks
        ],
        can_place=pre_trade.passed,
    )


@app.post("/api/orders/place", response_model=PlaceOrderResponse)
def place_order(
    request: PlaceOrderRequest, broker: BrokerDep, db_path: DbPathDep
) -> PlaceOrderResponse:
    _chain, legs, _payoff, pre_trade = _evaluate(
        request.strategy, request.symbol, request.quantity, broker
    )

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
    conn = _open_db(db_path)
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


@app.post("/api/baskets", response_model=BasketResponse)
def create_basket_endpoint(request: CreateBasketRequest, db_path: DbPathDep) -> BasketResponse:
    conn = _open_db(db_path)
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
    return _basket_to_response(basket)


@app.get("/api/baskets", response_model=list[BasketResponse])
def list_baskets_endpoint(db_path: DbPathDep) -> list[BasketResponse]:
    conn = _open_db(db_path)
    baskets = repo_list_baskets(conn)
    conn.close()
    return [_basket_to_response(b) for b in baskets]


@app.get("/api/baskets/{basket_id}", response_model=BasketResponse)
def get_basket_endpoint(basket_id: int, db_path: DbPathDep) -> BasketResponse:
    conn = _open_db(db_path)
    basket = get_basket(conn, basket_id)
    conn.close()
    if basket is None:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")
    return _basket_to_response(basket)


@app.post("/api/baskets/{basket_id}/legs/{leg_id}/close", response_model=BasketResponse)
def close_leg_endpoint(
    basket_id: int, leg_id: int, request: CloseLegRequest, db_path: DbPathDep
) -> BasketResponse:
    conn = _open_db(db_path)
    repo_close_leg(conn, leg_id, exit_price=request.exit_price, exit_at=datetime.now(UTC))
    basket = get_basket(conn, basket_id)
    conn.close()
    if basket is None:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")
    return _basket_to_response(basket)


@app.get("/api/portfolio/history", response_model=list[PortfolioHistoryPoint])
def portfolio_history(db_path: DbPathDep, days: int = 7) -> list[PortfolioHistoryPoint]:
    conn = _open_db(db_path)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = snapshots_since(conn, since=since)
    conn.close()
    return [
        PortfolioHistoryPoint(
            fetched_at=row.fetched_at.isoformat(),
            realized_pnl=row.realized_pnl,
            unrealized_pnl=row.unrealized_pnl,
            total_pnl=row.realized_pnl + row.unrealized_pnl,
        )
        for row in rows
    ]
