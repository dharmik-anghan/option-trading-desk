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
    # What the position is worth *now* rather than at expiry, priced off each
    # leg's live IV. Empty when the feed gave us no usable IV or no expiry to
    # measure time against - better to draw one curve than a made-up second.
    payoff_curve_today: list[PayoffPoint]
    days_to_expiry: float | None
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
    # Which expiry to trade. Empty means the nearest one, matching the chain
    # endpoint's default - if it were left implicit, the order could fill a
    # different expiry than the one previewed.
    expiry: str | None = None


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
    # Live state of this contract, filled only with ?live=true. The chain is
    # already being fetched for the pre-expiry curve, so carrying delta and the
    # day changes costs nothing extra - and it is what lets a watcher notice a
    # short strike being tested without polling the whole chain itself.
    ltp: float | None = None
    # Greeks as the broker reports them, per unit of the contract: delta per
    # point of the underlying, theta per day, vega per volatility point. Gamma
    # and vega are identical for the call and put at a strike, which is correct
    # under put-call parity rather than a quirk of the feed.
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    ltp_change: float | None = None
    oi_change: int | None = None


class BasketResponse(BaseModel):
    id: int
    name: str
    strategy: str
    underlying_symbol: str
    created_at: str
    #: This structure's own alert levels. None means no level set, which is not
    #: the same as a level of zero.
    stop_loss: float | None
    profit_target: float | None = None
    delta_limit: float | None = None
    #: Overrides for the shared defaults. None means the default is used, which is
    #: what lets a structure recorded before these existed behave as it did.
    worst_case_limit: float | None = None
    short_delta_limit: float | None = None
    expiry_warn_days: float | None = None
    #: What the open legs are worth now, and how the structure leans. Computed
    #: server-side so the screen, the alert about it, and the rule that raises
    #: that alert all read one number. None when the broker has not priced every
    #: open leg - a partial total read as a whole one looks fine and is wrong.
    mtm: float | None = None
    #: Exposure: the deltas weighted by contracts held. The only one that converts
    #: to money - 65 lots of 0.32 is 20.80 index points per unit move.
    net_delta: float | None = None
    #: The directional sum of the quoted deltas, unweighted. The figure on the
    #: legs table, the scale a trader speaks in, and what a delta limit is set
    #: against. Zero in both for a balanced structure, which is how one gets
    #: mistaken for the other.
    net_delta_per_contract: float | None = None
    legs: list[BasketLegResponse]
    max_profit: float | None
    max_loss: float | None
    breakevens: list[float]
    payoff_curve: list[PayoffPoint]
    # Only filled when asked for with ?live=true, and only when the legs all
    # share one listed expiry we can price against. Empty otherwise.
    payoff_curve_today: list[PayoffPoint] = []
    days_to_expiry: float | None = None
    expiry_date: str | None = None
    #: False when the open legs sit in different expiries - a calendar or a
    #: diagonal. The payoff fields above are then empty rather than wrong: they
    #: are worked out from intrinsic value at one expiry, which for a calendar
    #: reports the whole net debit as a certain loss.
    single_expiry: bool = True


class CloseLegRequest(BaseModel):
    exit_price: float


class MarketContextResponse(BaseModel):
    """What the chain says about one underlying, for the desk header.

    Every figure is optional: a missing one means the feed did not supply
    enough to compute it, which is a different thing from zero.
    """

    underlying_symbol: str
    spot: float
    change: float
    change_pct: float
    expiry_date: str | None
    futures_symbol: str | None
    futures: float | None
    futures_premium: float | None
    # Carry the futures premium implies, annualised, as a percentage.
    carry_pct: float | None
    atm_strike: float | None
    atm_straddle: float | None
    atm_iv: float | None
    historical_vol: float | None
    # Implied over historical: above 1 means options cost more than the index
    # has lately been moving.
    iv_over_hv: float | None
    put_call_ratio: float | None
    max_pain: float | None
    # Where open interest concentrates either side of spot. `*_prominence` is
    # how heavy that strike is relative to others of the same roundness: round
    # numbers carry far more open interest whatever the market is doing, so the
    # plain maximum mostly measures which strike is roundest. `*_heaviest` is
    # that plain maximum, kept so the two can be compared.
    resistance: float | None
    resistance_prominence: float | None
    resistance_heaviest: float | None
    support: float | None
    support_prominence: float | None
    support_heaviest: float | None
    skew: float | None


class EventResponse(BaseModel):
    """One scheduled release. Date only - the calendar publishes no time."""

    day: str
    name: str
    label: str
    importance: str
    coverage: str
    country: str | None


class EventsResponse(BaseModel):
    events: list[EventResponse]
    #: Seconds since the calendar was last refreshed, so the desk can say "as
    #: of" instead of implying the list is current. None means never fetched.
    age_seconds: float | None
    #: Set when the last refresh failed or the page could not be read. The
    #: events above may still be usable, just stale.
    error: str | None


class HeadlineResponse(BaseModel):
    title: str
    link: str
    source: str
    published: str | None
    #: Its publisher's beat, so a client can group or filter without a second
    #: request and without guessing from the words in the title.
    topics: list[str]


class NewsResponse(BaseModel):
    headlines: list[HeadlineResponse]
    #: Every topic the desk has a source for, so the filter offers what exists
    #: rather than a list hardcoded in the frontend.
    available_topics: list[str] = []
    #: Topics per source, for a filter that wants to name them.
    sources: dict[str, list[str]] = {}
    age_seconds: float | None
    error: str | None
