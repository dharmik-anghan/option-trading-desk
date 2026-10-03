"""What options cost, against what the market actually did.

The question the options desk could not answer. It showed greeks and a payoff -
what a position *is* - and nothing about whether premium was expensive or cheap,
which is the first thing a seller wants to know.

Four numbers, and they are different things:

    implied      what the chain is charging, at the money, on this expiry
    realised     what the price has actually done, annualised
    rank         where implied sits in its own past
    the spread   implied minus realised, which is the edge being sold

The rank matters more than the level. Measured on India VIX against the
twenty-one sessions that followed it, over 469 overlapping windows, implied
exceeded realised 79% of the time by a median of three vol points - so the
premium is nearly always there and the only question is whether today's is
large or small by its own standards.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from analytics.vol_snapshot import snapshot_from
from analytics.volatility import (
    close_to_close,
    expected_move,
    iv_hv_ratio,
    parkinson,
    rank_of,
)
from api.deps import BrokerDep, DbPathDep, bar_service
from api.store import open_db
from broker.errors import BrokerError
from marketdata import Interval
from marketdata.models import Bar
from storage.vol_repo import vol_history
from venues import OPTION_UNDERLYINGS
from venues.calendar import IST
from venues.instruments import INDIA_VIX
from venues.instruments import NSE_BARS as SOURCE

router = APIRouter(tags=["volatility"], prefix="/api/volatility")


#: Windows for realised volatility. Ten is a fortnight of sessions, twenty a
#: month, sixty a quarter - and the three together say whether the market has
#: been getting quieter or louder, which one of them alone cannot.
WINDOWS = (10, 20, 60)

#: Sessions the India VIX is ranked against - the options backtest's default
#: `vix_lookback`, so the panel and a backtest filter mean the same percentile.
VIX_LOOKBACK = 252


class RankOut(BaseModel):
    value: float
    low: float
    high: float
    rank: float
    percentile: float
    #: How many readings are behind it. A rank over two months is a statement
    #: about two months, and a screen should be able to say so.
    days: int
    says: str


class RealisedOut(BaseModel):
    window: int
    close_to_close: float | None
    #: Only on the longest window, where the comparison is worth drawing.
    parkinson: float | None = None


class VolatilityOut(BaseModel):
    underlying: str
    name: str
    spot: float
    expiry: str
    days_to_expiry: float
    atm_strike: float
    #: What the chain is charging at the money, now.
    atm_iv: float | None
    straddle: float | None
    #: The straddle as a share of spot: what the options are priced to cover
    #: between now and this expiry, which is what a seller is short.
    expected_move_pct: float | None
    #: In points, because a range is easier to picture than a percentage.
    expected_move_points: float | None
    india_vix: float | None
    realised: list[RealisedOut]
    #: Implied minus realised over twenty sessions. The edge, in vol points -
    #: what a seller actually collects.
    spread: float | None
    #: Implied over realised. Above one means options cost more than the recent
    #: past would justify. Beside the difference rather than instead of it: the
    #: ratio travels between a quiet index and a wild one where the subtraction
    #: does not.
    iv_hv: float | None
    #: Where India VIX sits in two years of its own history. Available today.
    vix_rank: RankOut | None
    #: Where this underlying's own implied sits in what has been recorded. None
    #: until enough days have been written down - it cannot be backfilled.
    iv_rank: RankOut | None
    #: How many days of our own implied history exist, so a screen can say how
    #: long until a rank appears rather than showing a blank.
    iv_days: int
    caveats: list[str]


def _bars(request: Request, symbol: str, days: int) -> list[Bar]:
    service = bar_service(request)
    if service is None:
        return []
    return service.stored(SOURCE, symbol, Interval.D1, days=days).bars


@router.get("/{underlying:path}", response_model=VolatilityOut)
def volatility(
    request: Request, underlying: str, db_path: DbPathDep, broker: BrokerDep
) -> VolatilityOut:
    """Everything about how much this thing moves, and what that is being sold for."""
    listed = dict(OPTION_UNDERLYINGS)
    if underlying not in listed:
        raise HTTPException(status_code=404, detail=f"{underlying} is not an underlying here")

    try:
        chain = broker.get_option_chain(underlying, strike_count=2)
    except BrokerError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc

    now = snapshot_from(chain, underlying)
    if now is None:
        raise HTTPException(
            status_code=503,
            detail="The chain came back without greeks, so there is no implied volatility",
        )

    caveats: list[str] = []
    bars = _bars(request, underlying, 400)
    closes = [b.close for b in bars]
    realised: list[RealisedOut] = []
    for window in WINDOWS:
        realised.append(
            RealisedOut(
                window=window,
                close_to_close=close_to_close(closes, window),
                # Parkinson on the middle window only. It reads the whole day's
                # range rather than the two endpoints, and printing it beside
                # every close-to-close figure would be six numbers where the
                # interesting thing is one comparison.
                parkinson=parkinson(bars, window) if window == 20 else None,
            )
        )
    if not bars:
        caveats.append(
            "No stored daily bars for this underlying, so nothing can be said about "
            "what it actually did. Run scripts/backfill_nse.py"
        )

    twenty = next((r.close_to_close for r in realised if r.window == 20), None)
    spread = now.atm_iv - twenty if now.atm_iv is not None and twenty is not None else None
    ratio = iv_hv_ratio(now.atm_iv, twenty)

    # Ranked exactly as the options backtest ranks it (optbt/context.py): the
    # previous year of sessions, today's close left out. Over two years the same
    # reading came out a different percentile here than in a backtest filter, so
    # a rule tested as "VIX above the 70th" did not mean what this panel said.
    vix_bars = _bars(request, INDIA_VIX, 400)
    today = datetime.now(IST).date()
    vix_history = [b.close for b in vix_bars if b.ts.astimezone(IST).date() < today][-VIX_LOOKBACK:]
    vix_rank = (
        rank_of(now.india_vix, vix_history, minimum=max(20, VIX_LOOKBACK // 4))
        if now.india_vix is not None and vix_history
        else None
    )
    if now.india_vix is not None and vix_rank is None:
        caveats.append(
            "Not enough India VIX history stored to rank it. Run scripts/backfill_nse.py"
        )

    conn = open_db(db_path)
    try:
        recorded = vol_history(conn, underlying)
    finally:
        conn.close()
    implied = [v.atm_iv for v in recorded if v.atm_iv is not None]
    iv_rank = rank_of(now.atm_iv, implied) if now.atm_iv is not None and implied else None
    if iv_rank is None:
        caveats.append(
            f"Only {len(implied)} day{'' if len(implied) == 1 else 's'} of this "
            "underlying's own implied volatility have been recorded, so it cannot be "
            "ranked yet. It is written down every session and cannot be backfilled - "
            "India VIX stands in for the regime meanwhile"
        )

    return VolatilityOut(
        underlying=underlying,
        name=listed[underlying],
        spot=now.spot,
        expiry=now.expiry,
        days_to_expiry=now.days_to_expiry,
        atm_strike=now.atm_strike,
        atm_iv=now.atm_iv,
        straddle=now.straddle,
        expected_move_pct=(
            expected_move(now.straddle, now.spot) if now.straddle is not None else None
        ),
        expected_move_points=now.straddle,
        india_vix=now.india_vix,
        realised=realised,
        spread=spread,
        iv_hv=ratio,
        vix_rank=_rank_out(vix_rank),
        iv_rank=_rank_out(iv_rank),
        iv_days=len(implied),
        caveats=caveats,
    )


def _rank_out(rank: object) -> RankOut | None:
    if rank is None:
        return None
    return RankOut(
        value=rank.value,  # type: ignore[attr-defined]
        low=rank.low,  # type: ignore[attr-defined]
        high=rank.high,  # type: ignore[attr-defined]
        rank=rank.rank,  # type: ignore[attr-defined]
        percentile=rank.percentile,  # type: ignore[attr-defined]
        days=rank.days,  # type: ignore[attr-defined]
        says=rank.says,  # type: ignore[attr-defined]
    )
