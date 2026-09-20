"""API response shapes.

Kept separate from the internal domain types (`analytics.payoff.Leg` is a
dataclass, `execution.portfolio_status.PortfolioStatus` too) so the wire
format is explicit and doesn't silently change if an internal dataclass's
fields change - the same reasoning as `broker/models.py` translating
Fyers' wire format rather than exposing it directly, just at the other end
of the stack.
"""

from __future__ import annotations

from pydantic import BaseModel

from broker.models import OptionType, Position, Side


class PortfolioResponse(BaseModel):
    positions: list[Position]
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float


class LegResponse(BaseModel):
    option_type: OptionType
    strike: float
    premium: float
    quantity: int
    side: Side
    symbol: str | None


class RiskCheckResponse(BaseModel):
    passed: bool
    reason: str


class PayoffPoint(BaseModel):
    spot: float
    payoff: float


class StrategySignalResponse(BaseModel):
    strategy: str
    symbol: str
    underlying_ltp: float
    legs: list[LegResponse]
    # None represents unbounded risk/reward (Python's math.inf/-math.inf).
    # json.dumps would otherwise emit the literal token `Infinity`, which
    # is not valid JSON and a browser's JSON.parse rejects outright.
    max_profit: float | None
    max_loss: float | None
    breakevens: list[float]
    payoff_curve: list[PayoffPoint]
    pre_trade_checks: list[RiskCheckResponse]
    can_place: bool


class PortfolioHistoryPoint(BaseModel):
    fetched_at: str
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float


class PlaceOrderRequest(BaseModel):
    strategy: str
    symbol: str
    quantity: int = 1
    basket_name: str | None = None


class OrderResultResponse(BaseModel):
    order_id: str
    message: str


class PlaceOrderResponse(BaseModel):
    orders: list[OrderResultResponse]
    basket_id: int


class NewBasketLegRequest(BaseModel):
    symbol: str
    option_type: OptionType
    strike: float
    side: Side
    quantity: int
    entry_price: float


class CreateBasketRequest(BaseModel):
    name: str
    strategy: str
    underlying_symbol: str
    legs: list[NewBasketLegRequest]
    stop_loss: float | None = None


class BasketLegResponse(BaseModel):
    id: int
    symbol: str
    option_type: OptionType
    strike: float
    side: Side
    quantity: int
    entry_price: float
    entry_at: str
    exit_price: float | None
    exit_at: str | None
    is_open: bool


class BasketResponse(BaseModel):
    id: int
    name: str
    strategy: str
    underlying_symbol: str
    created_at: str
    stop_loss: float | None
    legs: list[BasketLegResponse]
    max_profit: float | None
    max_loss: float | None
    breakevens: list[float]
    payoff_curve: list[PayoffPoint]


class CloseLegRequest(BaseModel):
    exit_price: float
