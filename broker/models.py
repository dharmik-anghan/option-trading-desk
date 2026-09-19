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
    volume: int
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
    greeks: Greeks | None = None


class OptionChain(BaseModel):
    underlying_symbol: str
    underlying_ltp: float
    fetched_at: datetime
    rows: list[OptionChainRow]


class Funds(BaseModel):
    total_balance: float
    utilized_margin: float
    available_balance: float


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class OrderRequest(BaseModel):
    symbol: str
    quantity: int
    side: Side
    order_type: OrderType = "MARKET"
    limit_price: float = 0.0
    product_type: str = "MARGIN"


class OrderResult(BaseModel):
    order_id: str
    message: str


class Position(BaseModel):
    symbol: str
    net_quantity: int
    average_price: float
    ltp: float
    unrealized_pnl: float
    product_type: str
