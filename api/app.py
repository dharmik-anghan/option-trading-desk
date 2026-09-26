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
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from analytics import market_context as mc
from analytics.payoff import (
    PLAUSIBLE_RATE,
    Leg,
    PayoffResult,
    analyze,
    curve_domain,
    implied_rate,
    payoff_curve_points,
    theoretical_curve,
)
from api.dependencies import get_broker, get_db_path, get_feeds, get_holidays
from api.schemas import (
    BasketLegResponse,
    BasketResponse,
    CloseLegRequest,
    CreateBasketRequest,
    EventResponse,
    EventsResponse,
    HeadlineResponse,
    LegResponse,
    MarketContextResponse,
    NewsResponse,
    OrderResultResponse,
    PayoffPoint,
    PlaceOrderRequest,
    PlaceOrderResponse,
    PortfolioHistoryPoint,
    PortfolioResponse,
    RiskCheckResponse,
    StrategySignalResponse,
)
from broker.base import Broker
from broker.errors import AuthFailed, BrokerError, BrokerUnreachable, RateLimited
from broker.models import Expiry, OptionChain, OptionChainRow, Quote
from broker.session import in_session
from broker.symbols import common_expiry, series_prefix
from execution.basket_status import get_basket_payoff
from execution.manager import ExecutionManager
from execution.portfolio_status import PortfolioStatus, get_portfolio_status
from feeds.fetch import Feeds, upcoming
from feeds.holidays import Holidays
from risk.pre_trade_check import (
    DEFAULT_MAX_LOSS_LIMIT,
    DEFAULT_MAX_RISK_PCT,
    DEFAULT_REQUIRED_MARGIN_PLACEHOLDER,
    PreTradeCheckResult,
    run_pre_trade_checks,
)
from storage.basket_repo import Basket, BasketLeg, NewBasketLeg, create_basket, get_basket
from storage.basket_repo import close_leg as repo_close_leg
from storage.basket_repo import delete_basket as repo_delete_basket
from storage.basket_repo import delete_leg as repo_delete_leg
from storage.basket_repo import list_baskets as repo_list_baskets
from storage.db import connect, init_schema
from storage.portfolio_repo import save_portfolio_snapshot, snapshots_since
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
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

BrokerDep = Annotated[Broker, Depends(get_broker)]
FeedsDep = Annotated[Feeds, Depends(get_feeds)]
HolidaysDep = Annotated[Holidays, Depends(get_holidays)]
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
    name: str, symbol: str, quantity: int, broker: Broker, expiry_token: str = ""
) -> tuple[OptionChain, list[Leg], PayoffResult, PreTradeCheckResult]:
    strategy = _strategies().get(name)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Unknown strategy '{name}'")
    strategy.quantity = quantity  # type: ignore[attr-defined]

    # 40 either side, not 15: a 0.08-delta wing on a monthly expiry sits well
    # outside a +/-3% window, and clamping it to the window edge is what made
    # the short and long legs land on the same strike.
    chain = broker.get_option_chain(
        symbol, strike_count=_STRATEGY_STRIKE_COUNT, expiry_token=expiry_token
    )
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


_SECONDS_PER_YEAR = 365.0 * 24 * 3600
_STRATEGY_STRIKE_COUNT = 40
_CONTEXT_STRIKE_COUNT = 40


def _years_to_expiry(chain: OptionChain) -> float | None:
    """Time left on the chain's selected expiry, in years.

    The broker's expiry token is a unix timestamp, which is exactly what this
    needs - no parsing a date out of a contract symbol and guessing a session
    close. Returns None when there is no token to read.
    """
    token = chain.expiry_token
    if not token:
        return None
    try:
        expires_at = datetime.fromtimestamp(int(token), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None
    seconds = (expires_at - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds) / _SECONDS_PER_YEAR


def _leg_marks(legs: list[Leg], chain: OptionChain) -> list[float] | None:
    """Each leg's live traded price, or None if the chain is missing any of them.

    Used to anchor the pre-expiry curve to what the position is actually worth.
    All or nothing: calibrating some legs and not others would tilt the curve
    rather than level it.
    """
    by_symbol = {row.symbol: row for row in chain.rows}
    marks: list[float] = []
    for leg in legs:
        row = by_symbol.get(leg.symbol or "")
        if row is None or row.ltp <= 0:
            return None
        marks.append(row.ltp)
    return marks


def _leg_sigmas(legs: list[Leg], chain: OptionChain) -> list[float]:
    """Each leg's implied vol as a decimal, matched by contract symbol.

    Fyers reports IV as a percentage and sends 0 for strikes it has no quote
    for; 0 flows through to `theoretical_curve` as "price this one at
    intrinsic" rather than being mistaken for a zero-vol market.
    """
    by_symbol = {row.symbol: row for row in chain.rows}
    sigmas: list[float] = []
    for leg in legs:
        row = by_symbol.get(leg.symbol or "")
        iv = row.greeks.iv if row is not None and row.greeks is not None else 0.0
        sigmas.append(max(0.0, iv) / 100.0)
    return sigmas


def _chain_rate(chain: OptionChain, years: float) -> float:
    """The carry the chain's own call/put pairs imply, by put-call parity.

    Better than a constant: it is what these prices are actually discounting
    at, so the curve stays consistent with the quotes it was built from. A
    hardcoded 6.5% was roughly 0.5 points off what this chain implies, and the
    curve is sensitive enough to the rate that that shows up as hundreds of
    rupees out in the wings. `implied_rate` falls back on its own when the
    answer is not credible, which it is not within a few days of expiry.
    """
    pairs: dict[float, dict[str, float]] = {}
    for row in chain.rows:
        if row.ltp > 0:
            pairs.setdefault(row.strike, {})[row.option_type] = row.ltp
    both = [
        (strike, sides["CE"], sides["PE"])
        for strike, sides in pairs.items()
        if "CE" in sides and "PE" in sides
    ]
    return implied_rate(both, chain.underlying_ltp, years)


def _today_curve(result: PayoffResult, chain: OptionChain) -> list[PayoffPoint]:
    """The mark-to-market curve, or nothing if we cannot price it honestly."""
    years = _years_to_expiry(chain)
    if years is None or years <= 0:
        return []
    sigmas = _leg_sigmas(result.legs, chain)
    if not any(sigma > 0 for sigma in sigmas):
        return []
    spots = curve_domain(result, include=chain.underlying_ltp)
    marks = _leg_marks(result.legs, chain)
    values = theoretical_curve(
        result.legs,
        spots,
        sigmas=sigmas,
        time_years=years,
        rate=_chain_rate(chain, years),
        realized_offset=result.realized_offset,
        calibrate_to=marks,
        at_spot=chain.underlying_ltp if marks else None,
    )
    return [PayoffPoint(spot=s, payoff=v) for s, v in zip(spots, values, strict=True)]


def _basket_live_curve(
    basket: Basket, result: PayoffResult, broker: Broker, chains: dict[tuple[str, str], OptionChain]
) -> tuple[list[PayoffPoint], float | None, str | None]:
    """A mark-to-market curve for a basket, if one can be priced honestly.

    A basket records its contracts but not its expiry, so the expiry is
    recovered from the leg symbols against the expiries the broker lists
    (see `broker.symbols`). Returns empties when that is not possible: no
    open legs, legs spanning different expiries (a calendar spread has no
    single time-to-expiry), or a feed with no implied vol for those strikes.

    `chains` is a cache keyed by (underlying, expiry token) so several
    baskets on the same expiry cost one chain request between them.
    """
    if not result.legs:
        return [], None, None

    symbols = [leg.symbol for leg in result.legs if leg.symbol]
    if len(symbols) != len(result.legs):
        return [], None, None

    listed = _listed_expiries(basket.underlying_symbol, broker, chains)
    expiry = common_expiry(symbols, listed)
    if expiry is None:
        return [], None, None

    key = (basket.underlying_symbol, expiry.token)
    chain = chains.get(key)
    if chain is None:
        chain = broker.get_option_chain(
            basket.underlying_symbol, strike_count=40, expiry_token=expiry.token
        )
        chains[key] = chain

    years = _years_to_expiry(chain)
    if years is None or years <= 0:
        return [], None, expiry.date
    sigmas = _leg_sigmas(result.legs, chain)
    if not any(sigma > 0 for sigma in sigmas):
        return [], round(years * 365.0, 2), expiry.date

    spots = curve_domain(result, include=chain.underlying_ltp)
    marks = _leg_marks(result.legs, chain)
    values = theoretical_curve(
        result.legs,
        spots,
        sigmas=sigmas,
        time_years=years,
        rate=_chain_rate(chain, years),
        realized_offset=result.realized_offset,
        calibrate_to=marks,
        at_spot=chain.underlying_ltp if marks else None,
    )
    curve = [PayoffPoint(spot=s, payoff=v) for s, v in zip(spots, values, strict=True)]
    return curve, round(years * 365.0, 2), expiry.date


_EXPIRY_CACHE: dict[str, tuple[float, list[Expiry]]] = {}
_EXPIRY_TTL = 600.0  # the listed contracts change once a day at most


def _listed_expiries(
    underlying: str, broker: Broker, chains: dict[tuple[str, str], OptionChain]
) -> list[Expiry]:
    """Every expiry the broker lists for `underlying`.

    Held for ten minutes: reading it needs a whole chain request, and the
    answer is a contract list that changes daily, not by the second. Before
    this it cost one request per basket per poll purely to re-learn dates we
    already knew.
    """
    for (sym, _token), chain in chains.items():
        if sym == underlying and chain.expiries:
            return chain.expiries

    hit = _EXPIRY_CACHE.get(underlying)
    if hit is not None and time.monotonic() - hit[0] < _EXPIRY_TTL:
        return hit[1]

    chain = broker.get_option_chain(underlying, strike_count=1)
    chains[(underlying, chain.expiry_token or "")] = chain
    if chain.expiries:
        _EXPIRY_CACHE[underlying] = (time.monotonic(), chain.expiries)
    return chain.expiries


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    init_schema(conn)
    return conn


def _spans_expiries(basket: Basket) -> bool:
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
        for prefix in [series_prefix(leg.symbol, leg.strike)]
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


def _basket_to_response(
    basket: Basket,
    live: tuple[list[PayoffPoint], float | None, str | None] = ([], None, None),
    rows: dict[str, OptionChainRow] | None = None,
    spot: float | None = None,
) -> BasketResponse:
    payoff = get_basket_payoff(basket)
    today, days, expiry_date = live
    rows = rows or {}
    spans_expiries = _spans_expiries(basket)
    if spans_expiries:
        # Everything below is derived from intrinsic value at a single expiry.
        # For a calendar that is not merely imprecise, it is wrong: the far leg
        # still has months of time value the model prices at zero, so the whole
        # net debit is reported as a certain loss. Better to show nothing.
        payoff = replace(payoff, max_profit=0.0, max_loss=0.0, breakevens=[], legs=[])
        today = []
    return BasketResponse(
        id=basket.id,
        name=basket.name,
        strategy=basket.strategy,
        underlying_symbol=basket.underlying_symbol,
        created_at=basket.created_at.isoformat(),
        stop_loss=basket.stop_loss,
        legs=[_leg_response(leg, rows.get(leg.symbol)) for leg in basket.legs],
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


# Broker failures become statuses a client can act on, with a stable `code`,
# instead of an opaque 500 that leaves the desk frozen with no explanation.
_BROKER_STATUS: dict[type[BrokerError], int] = {
    RateLimited: 429,
    BrokerUnreachable: 503,
    AuthFailed: 401,
}


@app.exception_handler(BrokerError)
def broker_error_handler(request: Request, exc: BrokerError) -> JSONResponse:
    status = next(
        (code for cls, code in _BROKER_STATUS.items() if isinstance(exc, cls)),
        502,
    )
    return JSONResponse(
        status_code=status,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


@app.get("/api/health")
def health(broker: BrokerDep) -> dict[str, object]:
    """Liveness, plus whether broker reads are currently degraded.

    Polled rarely; the per-request status codes above are what the desk reacts
    to. This is for the case where cached values are being served over a rate
    limit and every request still looks like a success.
    """
    since = getattr(broker, "seconds_since_rate_limited", None)
    recently = since is not None and since < 60
    return {"status": "ok", "rate_limited": recently}


#: Least time between recorded P&L snapshots. The portfolio is polled every
#: few seconds, and a point that often is noise rather than history - this
#: keeps a trading day to a few dozen rows that actually plot.
SNAPSHOT_EVERY = timedelta(minutes=2)
_last_snapshot: datetime | None = None


@app.get("/api/portfolio", response_model=PortfolioResponse)
def portfolio(
    broker: BrokerDep, db_path: DbPathDep, holidays: HolidaysDep
) -> PortfolioResponse:
    status = get_portfolio_status(broker)
    _record_snapshot(status, db_path, holidays)
    return PortfolioResponse(
        positions=status.positions,
        realized_pnl=status.realized_pnl,
        unrealized_pnl=status.unrealized_pnl,
        total_pnl=status.total_pnl,
    )


def _record_snapshot(status: PortfolioStatus, db_path: Path, holidays: Holidays) -> None:
    """Keep a throttled history of P&L, for the chart that plots it.

    Recorded here rather than by a background task on purpose: it costs no
    extra broker call, because it stores what was just fetched anyway, and it
    only accumulates while somebody is actually watching. A timer would keep
    polling an empty room.

    Nothing is recorded while the exchange is shut. Prices do not move
    overnight, at weekends or on a holiday, so a snapshot then is a duplicate
    of the close - it would pad the table and draw a flat line across hours
    when nothing was happening.

    Never allowed to fail the request - the P&L on screen matters more than
    the history behind it.
    """
    global _last_snapshot
    now = datetime.now(UTC)
    if not in_session(now, holidays.dates()):
        return
    if _last_snapshot is not None and now - _last_snapshot < SNAPSHOT_EVERY:
        return
    try:
        conn = _open_db(db_path)
        save_portfolio_snapshot(
            conn,
            status.positions,
            status.realized_pnl,
            status.unrealized_pnl,
            fetched_at=now,
        )
        conn.close()
        _last_snapshot = now
    except sqlite3.Error:
        pass


@app.get("/api/option-chain/{symbol:path}", response_model=OptionChain)
def option_chain(
    symbol: str, broker: BrokerDep, strike_count: int = 15, expiry: str = ""
) -> OptionChain:
    """`expiry` is a token from a previous response's `expiries`; empty = nearest."""
    return broker.get_option_chain(symbol, strike_count=strike_count, expiry_token=expiry)


@app.get("/api/market/{symbol:path}", response_model=MarketContextResponse)
def market_context(symbol: str, broker: BrokerDep, hv_sessions: int = 20) -> MarketContextResponse:
    """The chain's own read on an underlying: walls, max pain, straddle, IV vs HV.

    Computed here rather than in the browser because historical volatility
    needs candle history, and because this is a dozen numbers where the chain
    it comes from is a few hundred rows.

    The near-month expiry is used for the walls and max pain, since that is
    where the open interest is, and its futures contract for the carry.
    """
    chain = broker.get_option_chain(symbol, strike_count=_CONTEXT_STRIKE_COUNT)
    monthly = next((e for e in chain.expiries if not e.weekly), None)
    if monthly is not None and monthly.token != chain.expiry_token:
        chain = broker.get_option_chain(
            symbol, strike_count=_CONTEXT_STRIKE_COUNT, expiry_token=monthly.token
        )

    strikes = mc.by_strike(chain)
    spot = chain.underlying_ltp
    iv = mc.atm_iv(strikes, spot)
    hv = _historical_vol(symbol, broker, hv_sessions)

    futures_sym: str | None = None
    if chain.rows:
        futures_sym = mc.futures_symbol(chain.rows[0].symbol, chain.rows[0].strike)
    futures = _futures_price(futures_sym, broker)

    premium = None if futures is None else futures - spot
    carry = None
    years = _years_to_expiry(chain)
    if futures is not None and years and years > 0 and spot > 0 and futures > 0:
        implied = math.log(futures / spot) / years
        # Same guard as the implied rate: days from expiry, a few rupees of
        # premium over a tiny year-fraction reports an absurd carry.
        if PLAUSIBLE_RATE[0] <= implied <= PLAUSIBLE_RATE[1]:
            carry = implied * 100

    support = mc.oi_wall(strikes, "PE", spot)
    resistance = mc.oi_wall(strikes, "CE", spot)

    quote = _index_quote(symbol, broker)
    change = 0.0 if quote is None else spot - quote.prev_close
    change_pct = 0.0 if quote is None or quote.prev_close <= 0 else change / quote.prev_close

    return MarketContextResponse(
        underlying_symbol=symbol,
        spot=spot,
        change=change,
        change_pct=change_pct,
        expiry_date=monthly.date if monthly else None,
        futures_symbol=futures_sym,
        futures=futures,
        futures_premium=premium,
        carry_pct=carry,
        atm_strike=mc.atm_strike(strikes, spot),
        atm_straddle=mc.atm_straddle(strikes, spot),
        atm_iv=iv,
        historical_vol=hv,
        iv_over_hv=None if iv is None or not hv else iv / hv,
        put_call_ratio=mc.put_call_ratio(chain, strikes),
        max_pain=mc.max_pain(strikes),
        resistance=resistance.strike if resistance else None,
        resistance_prominence=resistance.prominence if resistance else None,
        resistance_heaviest=resistance.heaviest if resistance else None,
        support=support.strike if support else None,
        support_prominence=support.prominence if support else None,
        support_heaviest=support.heaviest if support else None,
        skew=mc.skew(strikes, spot),
    )


def _historical_vol(symbol: str, broker: Broker, sessions: int) -> float | None:
    """Annualised volatility from daily closes, or None if history is unavailable.

    Deliberately swallowed: the header should still render its other dozen
    figures when the history endpoint is unhappy.
    """
    try:
        # enough calendar days to cover `sessions` trading ones, with slack
        span = max(40, int(sessions * 2.2))
        candles = broker.get_history(
            symbol, "D", date.today() - timedelta(days=span), date.today()
        )
    except BrokerError:
        return None
    return mc.historical_vol([c.close for c in candles], sessions=sessions)


def _futures_price(symbol: str | None, broker: Broker) -> float | None:
    if not symbol:
        return None
    try:
        quotes = broker.get_quote([symbol])
    except BrokerError:
        return None
    quote = quotes.get(symbol)
    return quote.ltp if quote else None


def _index_quote(symbol: str, broker: Broker) -> Quote | None:
    try:
        return broker.get_quote([symbol]).get(symbol)
    except BrokerError:
        return None


@app.get("/api/events", response_model=EventsResponse)
def events(feeds: FeedsDep, days: int = 45, importance: str = "HM") -> EventsResponse:
    """Scheduled releases from today out to `days` ahead.

    `importance` is a string of the levels to keep, e.g. "H" or "HM". The
    calendar carries several hundred entries, most of them minor, so filtering
    is the default rather than an option.
    """
    cached = feeds.events()
    wanted = upcoming(cached.events, date.today(), days=days, importance=importance.upper())
    return EventsResponse(
        events=[
            EventResponse(
                day=e.day.isoformat(),
                name=e.name,
                label=e.label,
                importance=e.importance,
                coverage=e.coverage,
                country=e.country,
            )
            for e in wanted
        ],
        age_seconds=feeds.age_seconds(cached),
        error=cached.error,
    )


@app.get("/api/news", response_model=NewsResponse)
def news(feeds: FeedsDep, limit: int = 40) -> NewsResponse:
    """Recent market headlines, newest first, pooled across the feeds."""
    cached = feeds.headlines()
    return NewsResponse(
        headlines=[
            HeadlineResponse(
                title=h.title,
                link=h.link,
                source=h.source,
                published=h.published.isoformat() if h.published else None,
            )
            for h in cached.headlines[: max(1, limit)]
        ],
        age_seconds=feeds.age_seconds(cached),
        error=cached.error,
    )


@app.get("/api/quotes", response_model=dict[str, Quote])
def quotes(symbols: str, broker: BrokerDep) -> dict[str, Quote]:
    """Latest quote per symbol, for a comma-separated list.

    Lets a watchlist show every underlying's price without pulling a whole
    option chain per underlying just to read one number off it.
    """
    wanted = [s.strip() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="Pass at least one symbol")
    return broker.get_quote(wanted)


@app.get("/api/strategies/{name}", response_model=StrategySignalResponse)
def strategy_signal(
    name: str,
    symbol: str,
    broker: BrokerDep,
    quantity: int = 1,
    expiry: str = "",
) -> StrategySignalResponse:
    chain, legs, result, pre_trade = _evaluate(name, symbol, quantity, broker, expiry)
    years = _years_to_expiry(chain)

    return StrategySignalResponse(
        strategy=name,
        symbol=symbol,
        underlying_ltp=chain.underlying_ltp,
        legs=[LegResponse(**vars(leg)) for leg in legs],
        max_profit=None if math.isinf(result.max_profit) else result.max_profit,
        max_loss=None if math.isinf(result.max_loss) else result.max_loss,
        breakevens=result.breakevens,
        payoff_curve=[
            PayoffPoint(spot=x, payoff=value)
            for x, value in payoff_curve_points(result, chain.underlying_ltp)
        ],
        payoff_curve_today=_today_curve(result, chain),
        days_to_expiry=None if years is None else round(years * 365.0, 2),
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
        request.strategy, request.symbol, request.quantity, broker, request.expiry or ""
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
def list_baskets_endpoint(
    db_path: DbPathDep, broker: BrokerDep, live: bool = False
) -> list[BasketResponse]:
    """`live=true` also prices each basket where it stands now, not just at
    expiry. That costs broker calls - one chain per distinct expiry held - so
    it is opt-in rather than the default."""
    conn = _open_db(db_path)
    baskets = repo_list_baskets(conn)
    conn.close()
    if not live:
        return [_basket_to_response(b) for b in baskets]
    # shared across baskets, so two structures on one expiry cost one request
    chains: dict[tuple[str, str], OptionChain] = {}
    out: list[BasketResponse] = []
    for basket in baskets:
        payoff = get_basket_payoff(basket)
        try:
            valued = _basket_live_curve(basket, payoff, broker, chains)
        except Exception:  # noqa: BLE001 - a live extra must never 500 the list
            valued = ([], None, None)
        out.append(
            _basket_to_response(
                basket,
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


@app.delete("/api/baskets/{basket_id}", status_code=204)
def delete_basket_endpoint(basket_id: int, db_path: DbPathDep) -> None:
    """Forget a grouping. Does not touch the broker - nothing is squared off."""
    conn = _open_db(db_path)
    existed = repo_delete_basket(conn, basket_id)
    conn.close()
    if not existed:
        raise HTTPException(status_code=404, detail=f"Basket {basket_id} not found")


@app.delete("/api/baskets/{basket_id}/legs/{leg_id}", status_code=204)
def delete_leg_endpoint(basket_id: int, leg_id: int, db_path: DbPathDep) -> None:
    """Take a leg out of a basket it never belonged to.

    Closing a leg that was genuinely exited is a different thing - that is
    POST .../close, which keeps the leg and its exit price on the basket.
    """
    conn = _open_db(db_path)
    existed = repo_delete_leg(conn, basket_id, leg_id)
    conn.close()
    if not existed:
        raise HTTPException(status_code=404, detail=f"Leg {leg_id} not found in basket {basket_id}")


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
