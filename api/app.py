"""Read-only dashboard API.

Deliberately read-only: order placement stays in
`scripts/place_strategy_order.py`'s CLI confirm flow (see
docs/PHASES.md/ARCHITECTURE.md for why) - this API only ever calls
broker methods that can't move money (`get_positions`, `get_funds`,
`get_option_chain`) or reads local history, never `place_order`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from analytics.payoff import analyze
from api.dependencies import get_broker, get_db_path
from api.schemas import (
    LegResponse,
    PortfolioHistoryPoint,
    PortfolioResponse,
    StrategySignalResponse,
)
from broker.base import Broker
from broker.models import OptionChain
from execution.portfolio_status import get_portfolio_status
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
    allow_methods=["GET"],
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
    strategy = _strategies().get(name)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Unknown strategy '{name}'")
    strategy.quantity = quantity  # type: ignore[attr-defined]

    chain = broker.get_option_chain(symbol, strike_count=15)
    legs = strategy.build_legs(chain)
    result = analyze(legs)

    return StrategySignalResponse(
        strategy=name,
        symbol=symbol,
        underlying_ltp=chain.underlying_ltp,
        legs=[LegResponse(**vars(leg)) for leg in legs],
        max_profit=None if math.isinf(result.max_profit) else result.max_profit,
        max_loss=None if math.isinf(result.max_loss) else result.max_loss,
        breakevens=result.breakevens,
    )


@app.get("/api/portfolio/history", response_model=list[PortfolioHistoryPoint])
def portfolio_history(db_path: DbPathDep, days: int = 7) -> list[PortfolioHistoryPoint]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    init_schema(conn)
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
