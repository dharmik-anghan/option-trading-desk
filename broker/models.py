"""Broker-agnostic data shapes.

Every broker adapter (FyersBroker today, others later) must translate its
own wire format into these models. Nothing above the `broker/` layer should
ever see a raw Fyers/Zerodha/etc. JSON payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

OptionType = Literal["CE", "PE"]
Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]


class Greeks(BaseModel):
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


class Quote(BaseModel):
    symbol: str
    ltp: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    bid: float
    ask: float
    timestamp: datetime


class OptionChainRow(BaseModel):
    symbol: str
    strike: float
    option_type: OptionType
    ltp: float
    bid: float
    ask: float
    oi: int
    prev_oi: int
    volume: int
    # Day changes as the broker reports them. Needed together to read open
    # interest: whether OI is being added or closed only means something
    # alongside which way the price moved. Defaults keep older callers and
    # stored snapshots valid.
    ltp_change: float = 0.0
    ltp_change_pct: float = 0.0
    oi_change: int = 0
    oi_change_pct: float = 0.0
    greeks: Greeks | None = None


class Expiry(BaseModel):
    """One listed expiry for an underlying.

    `token` is the broker's own selector for that expiry (Fyers calls it
    `expiry`, a unix timestamp as a string) and is passed back verbatim when
    asking for that expiry's chain - we never reconstruct it ourselves.
    """

    date: str  # as the exchange lists it, e.g. "29-10-2026"
    token: str
    weekly: bool


class OptionChain(BaseModel):
    underlying_symbol: str
    underlying_ltp: float
    fetched_at: datetime
    rows: list[OptionChainRow]
    # Every expiry the broker lists, so the caller can offer a choice rather
    # than silently always showing the nearest one.
    expiries: list[Expiry] = []
    expiry_token: str | None = None  # which of `expiries` these rows are for
    # Whole-chain totals the broker already computes; summing `rows` would
    # only match when every strike was fetched.
    call_oi: int = 0
    put_oi: int = 0
    india_vix: float | None = None


class Funds(BaseModel):
    total_balance: float
    utilized_margin: float
    available_balance: float
    realized_pnl: float


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    # Fractional on a crypto venue (1762.221 XAU), whole on an index.
    volume: float


class OrderRequest(BaseModel):
    symbol: str
    #: Float for the same reason as Position.net_quantity: perpetuals trade in
    #: fractions of a contract.
    quantity: float
    side: Side
    order_type: OrderType = "MARKET"
    limit_price: float = 0.0
    product_type: str = "MARGIN"


class OrderResult(BaseModel):
    order_id: str
    message: str


class Position(BaseModel):
    symbol: str
    # Float, not int: index options trade in whole contracts but a perpetual
    # trades in fractions of one (0.01 BTC), and an int cannot hold that. Whole
    # numbers are exact in a float, so the options path is unaffected.
    net_quantity: float
    average_price: float
    ltp: float
    unrealized_pnl: float
    product_type: str
