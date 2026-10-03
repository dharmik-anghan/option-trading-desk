"""Prices: the option chain, the context strip, and raw quotes.

Reads only - nothing here can change an account.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from analytics import market_context as mc
from analytics.payoff import (
    PLAUSIBLE_RATE,
)
from analytics.volatility import iv_hv_ratio
from api.deps import BrokerDep, CodecDep
from api.pricing import (
    years_to_expiry,
)
from api.schemas import (
    MarketContextResponse,
)
from broker.base import OptionsBroker
from broker.errors import BrokerError
from broker.fyers.stream import ALSO_STREAMED
from broker.models import OptionChain, Quote
from streaming import TickHub
from streaming.sse import sse, tick_events
from venues import AssetClass, serving
from venues.instruments import listed_on

router = APIRouter()


_CONTEXT_STRIKE_COUNT = 40


@router.get("/api/option-chain/{symbol:path}", response_model=OptionChain)
def option_chain(
    symbol: str, broker: BrokerDep, strike_count: int = 15, expiry: str = ""
) -> OptionChain:
    """`expiry` is a token from a previous response's `expiries`; empty = nearest."""
    return broker.get_option_chain(symbol, strike_count=strike_count, expiry_token=expiry)


@router.get("/api/market/{symbol:path}", response_model=MarketContextResponse)
def market_context(
    symbol: str, broker: BrokerDep, codec: CodecDep, hv_sessions: int = 20
) -> MarketContextResponse:
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
        futures_sym = codec.futures_symbol(chain.rows[0].symbol, chain.rows[0].strike)
    futures = _futures_price(futures_sym, broker)

    premium = None if futures is None else futures - spot
    carry = None
    years = years_to_expiry(chain)
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
        iv_over_hv=iv_hv_ratio(iv, hv),
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


def _historical_vol(symbol: str, broker: OptionsBroker, sessions: int) -> float | None:
    """Annualised volatility from daily closes, or None if history is unavailable.

    Deliberately swallowed: the header should still render its other dozen
    figures when the history endpoint is unhappy.
    """
    try:
        # enough calendar days to cover `sessions` trading ones, with slack
        span = max(40, int(sessions * 2.2))
        candles = broker.get_history(symbol, "D", date.today() - timedelta(days=span), date.today())
    except BrokerError:
        return None
    return mc.historical_vol([c.close for c in candles], sessions=sessions)


def _futures_price(symbol: str | None, broker: OptionsBroker) -> float | None:
    if not symbol:
        return None
    try:
        quotes = broker.get_quote([symbol])
    except BrokerError:
        return None
    quote = quotes.get(symbol)
    return quote.ltp if quote else None


def _index_quote(symbol: str, broker: OptionsBroker) -> Quote | None:
    try:
        return broker.get_quote([symbol]).get(symbol)
    except BrokerError:
        return None


#: Contracts one page may ask the stream for. A position book, its baskets and
#: a chain's visible strikes come to well under this.
MAX_WATCHED = 300

_SYMBOL = re.compile(r"^(NSE|BSE):[A-Z0-9-]+$")


@router.get("/api/quotes/stream")
async def quote_stream(request: Request, symbols: str = "") -> StreamingResponse:
    """The options venue's prices as they arrive.

    Always the indices and the VIX. `symbols` adds contracts - the legs of open
    positions and baskets, the strikes the chain is showing - which the venue's
    socket carries for as long as this page is reading them. OI is not among
    what arrives: Fyers' SDK strips it from socket updates, so it stays with the
    chain fetch.
    """
    closing: asyncio.Event | None = getattr(request.app.state, "shutting_down", None)
    hub = getattr(request.app.state, "tick_hub", None)
    spec = serving(AssetClass.INDEX_OPTIONS)
    stream = getattr(request.app.state, "tick_streams", {}).get(spec.id)
    extra = [s for s in dict.fromkeys(x.strip() for x in symbols.split(",")) if _SYMBOL.match(s)]
    if len(extra) > MAX_WATCHED:
        raise HTTPException(status_code=400, detail=f"At most {MAX_WATCHED} symbols")
    wanted = [*listed_on(spec.id), *ALSO_STREAMED, *extra]

    async def events() -> AsyncIterator[str]:
        ticks = tick_events(request, hub if isinstance(hub, TickHub) else None, closing, wanted)
        watching = getattr(stream, "watching", None)
        if watching is None or not extra:
            async for frame in ticks:
                yield frame
            return
        async with watching(extra):
            async for frame in ticks:
                yield frame

    return sse(events())


@router.get("/api/quotes", response_model=dict[str, Quote])
def quotes(symbols: str, broker: BrokerDep) -> dict[str, Quote]:
    """Latest quote per symbol, for a comma-separated list.

    Lets a watchlist show every underlying's price without pulling a whole
    option chain per underlying just to read one number off it.
    """
    wanted = [s.strip() for s in symbols.split(",") if s.strip()]
    if not wanted:
        raise HTTPException(status_code=400, detail="Pass at least one symbol")
    return broker.get_quote(wanted)
