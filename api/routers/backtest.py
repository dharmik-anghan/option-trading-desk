"""Running a built strategy over stored history.

The strategy arrives as the tree the builder assembled - see `backtest/spec.py` -
and everything that decides what a result means arrives with it: which series,
which window, how much capital, what leverage, how much slippage to assume. A
result that cannot be reproduced from its own request is a result nobody can
check, so the request is echoed back with it.

Two things are deliberately not returned in full. Three years of five-minute bars
is 315,361 equity points, which is megabytes of JSON to draw a line a thousand
pixels wide, so the curve is thinned - but every figure computed from it, the
drawdown especially, is computed on the whole series first. And a rule that traded
forty thousand times sends the most recent few hundred with a count of the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backtest.engine import Execution, run
from backtest.market import FundingSchedule, PerpetualMarket
from backtest.metrics import Metrics, measure
from backtest.resample import resample
from backtest.rules import SpecRule
from backtest.spec import SpecError, parse
from marketdata import BarService, Interval
from marketdata.store import BarStore

router = APIRouter(tags=["backtest"], prefix="/api/backtest")

#: Points on the returned curve. Enough to draw three years without a visible
#: step, few enough to send.
CURVE_POINTS = 1200

#: Trades sent back. Raised well above what anyone scrolls, because the point of
#: a trade log is to be searched and totalled rather than read - and a run that
#: made two thousand trades should hand over two thousand rather than a sample
#: that cannot be added up.
MAX_TRADES = 5000


class RunRequest(BaseModel):
    """A strategy, and everything needed to say what running it meant."""

    spec: dict[str, Any]
    source: str = "binance"
    symbol: str = "BTCUSDT"
    #: The bar size to trade. Resampled from what is stored when it is longer.
    interval: str = "5m"
    days: int = Field(default=1095, ge=1, le=3650)
    capital: float = Field(default=1000.0, gt=0)
    leverage: float = Field(default=1.0, gt=0, le=150)
    slippage_bps: float = Field(default=1.0, ge=0, le=100)
    maker_entry: bool = False
    #: "equity" - a fraction of the account; "quantity" - a fixed lot;
    #: "notional" - a fixed amount of money at work.
    sizing: str = "equity"
    risk: float = Field(default=1.0, gt=0, le=1)
    quantity: float = Field(default=0.0, ge=0)
    notional: float = Field(default=0.0, ge=0)
    #: The venue's floors, so a run refuses a size nobody could place.
    min_quantity: float = Field(default=0.001, ge=0)
    min_notional: float = Field(default=0.0, ge=0)
    quantity_dp: int = Field(default=3, ge=0, le=8)
    #: Whose funding to charge. Empty means none, which is only honest for an
    #: instrument that has none.
    funding_source: str = "binance"


class TradeOut(BaseModel):
    """One round trip, in full.

    Everything needed to reconstruct it: when, which way, at what prices, how
    much, why it was taken, why it ended, and where every unit of money went.
    """

    side: str
    opened_at: str
    closed_at: str
    entry: float
    exit_price: float
    quantity: float
    #: Position value at entry, in the quote currency.
    notional: float
    why: str
    #: The condition that opened it, in the words it was built with.
    entry_reason: str
    #: What closed it - the exit condition, or the stop, target or liquidation.
    exit_reason: str
    gross: float
    fees: float
    funding: float
    slippage: float
    net: float
    #: Net as a fraction of the margin the position required.
    net_pct: float
    #: How long it was held, in bars of the traded size.
    bars_held: float


class SideOut(BaseModel):
    trades: int
    wins: int
    win_rate: float
    gross: float
    net: float


class MetricsOut(BaseModel):
    trades: int
    wins: int
    win_rate: float
    total_return: float
    buy_and_hold: float
    beat_holding: bool
    max_drawdown: float
    sharpe: float
    gross: float
    fees: float
    funding: float
    slippage: float
    cost_share: float | None
    exposure: float
    best: float
    worst: float
    endings: dict[str, int]
    by_side: dict[str, SideOut]
    average_bars_held: float


class RunResponse(BaseModel):
    name: str
    #: The strategy in words, as it was built.
    reads: str
    symbol: str
    source: str
    interval: str
    bars: int
    started: str | None
    ended: str | None
    capital: float
    final: float
    metrics: MetricsOut
    #: Thinned for drawing. `bars` says how many there really were.
    curve: list[list[float]]
    trades: list[TradeOut]
    trades_total: int
    #: Positions closed and reopened the other way on the same signal.
    reversals: int
    skipped_too_small: int
    skipped_unaffordable: int
    #: Anything a reader has to know to judge the numbers.
    caveats: list[str]


class CandleOut(BaseModel):
    at: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class WindowResponse(BaseModel):
    source: str
    symbol: str
    interval: str
    candles: list[CandleOut]


def _service(request: Request) -> BarService:
    service = getattr(request.app.state, "bar_service", None)
    if not isinstance(service, BarService):
        raise HTTPException(
            status_code=503, detail="The bar store is not open, so there is nothing to test on"
        )
    return service


@router.get("/candles", response_model=WindowResponse)
def candles(
    request: Request,
    source: str,
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> WindowResponse:
    """The bars over one window, for looking at a single trade.

    A result says a trade made money; this is what lets you see whether it was a
    trade anybody would have taken. The window is named by time rather than by a
    count of days, because the caller is asking about a particular trade and not
    about a period.

    Read from the store only. A backtest is over history that has already been
    fetched, and going to a source here could return bars that differ from the
    ones the run was computed on - which would make the picture disagree with the
    numbers beside it.
    """
    try:
        size = Interval(interval)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{interval} is not a bar size") from None
    try:
        from_at = datetime.fromisoformat(start)
        to_at = datetime.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=400, detail="start and end are ISO timestamps") from None
    if to_at <= from_at:
        raise HTTPException(status_code=400, detail="the window ends before it begins")

    service = _service(request)
    held = _stored_interval(service, source, symbol, size)
    # Asked for by days because that is what the store's reader takes, then cut to
    # the window. Generous enough to cover it, and bounded so a trade opened three
    # years ago does not read the whole series.
    days = max(1, int((datetime.now(UTC) - from_at).total_seconds() / 86400) + 1)
    bars = service.stored(source, symbol, held, days=days).bars
    if held != size:
        bars = resample(bars, size)
    inside = [b for b in bars if from_at <= b.ts <= to_at]

    return WindowResponse(
        source=source,
        symbol=symbol,
        interval=str(size),
        candles=[
            CandleOut(
                at=b.ts.isoformat(),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in inside
        ],
    )


@router.post("/run", response_model=RunResponse)
def run_backtest(request: Request, body: RunRequest) -> RunResponse:
    """Run one strategy over one series."""
    try:
        spec = parse({**body.spec, "interval": body.interval})
    except SpecError as bad:
        # The builder's own message, which says what to change.
        raise HTTPException(status_code=400, detail=str(bad)) from None

    try:
        size = Interval(body.interval)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{body.interval} is not a bar size") from None

    service = _service(request)
    store = getattr(request.app.state, "bar_store", None)

    # Stored at the shortest size we hold; anything longer is built from it rather
    # than fetched, so the two cannot disagree about a bar boundary.
    held = _stored_interval(service, body.source, body.symbol, size)
    bars = service.stored(body.source, body.symbol, held, days=body.days).bars
    if not bars:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No {held} bars stored for {body.symbol} from {body.source}. "
                "Run scripts/backfill_bars.py first."
            ),
        )
    if held != size:
        bars = resample(bars, size)

    caveats: list[str] = []
    funding = None
    if body.funding_source and isinstance(store, BarStore):
        # From the first bar rather than from `days` ago by the wall clock. The
        # two are not the same thing - a store that ends last week gives a window
        # that ended last week - and taking the clock's answer fetched funding for
        # a period the run does not cover, which is to say none of it.
        rates = store.read_funding(body.funding_source, body.symbol, start=bars[0].ts)
        if rates:
            funding = FundingSchedule.of(body.funding_source, rates)
            if body.funding_source != body.source or body.source != "shark":
                caveats.append(
                    f"Funding is {body.funding_source}'s, charged every 8 hours. The venue "
                    "publishes none of its own, so this is a proxy - and it settles every "
                    "4 or 8 hours there depending on the contract"
                )
        else:
            caveats.append(
                "No funding history for this symbol, so none was charged. A perpetual "
                "held for days pays it, so this reads better than it would have been"
            )

    market = PerpetualMarket(
        symbol=body.symbol,
        funding=funding,
        min_quantity=body.min_quantity,
        min_notional=body.min_notional,
        quantity_dp=body.quantity_dp,
    )
    execution = Execution(
        capital=body.capital,
        sizing=body.sizing,
        risk=body.risk,
        quantity=body.quantity,
        notional=body.notional,
        leverage=body.leverage,
        slippage_bps=body.slippage_bps,
        maker_entry=body.maker_entry,
    )
    result = run(bars, SpecRule(spec), market, interval=size, execution=execution)
    metrics = measure(result.trades, result.equity, bars, result.capital, size)
    caveats.extend(result.caveats)
    # A side that was declared and never traded is the kind of thing a result
    # should say out loud. It usually means the strategy contradicts itself, or
    # that every signal for that side was consumed by the other side's exit.
    for side, declared in (("long", spec.long_entry), ("short", spec.short_entry)):
        if declared is not None and side not in metrics.by_side:
            caveats.append(
                f"This strategy declares {side} entries but never took one, so the result "
                f"is the other side alone"
            )
    if body.maker_entry:
        caveats.append(
            "Entries are resting limit orders, filled only where a bar traded through "
            "them. Exits still pay the taker fee"
        )

    return RunResponse(
        name=spec.name,
        reads=_sentence(spec),
        symbol=body.symbol,
        source=body.source,
        interval=str(size),
        bars=len(bars),
        started=result.started.isoformat() if result.started else None,
        ended=result.ended.isoformat() if result.ended else None,
        capital=result.capital,
        final=result.final,
        metrics=_metrics_out(metrics),
        curve=_thinned(bars, result.equity),
        trades=[_trade_out(t, size, execution) for t in result.trades[-MAX_TRADES:]],
        trades_total=len(result.trades),
        reversals=result.reversals,
        skipped_too_small=result.skipped_too_small,
        skipped_unaffordable=result.skipped_unaffordable,
        caveats=caveats,
    )


def _stored_interval(service: BarService, source: str, symbol: str, wanted: Interval) -> Interval:
    """The stored series to build `wanted` out of.

    Prefers the exact size when it is held and otherwise the longest stored size
    that divides it - resampling up is arithmetic, and down is impossible.
    """
    held = {
        series.interval
        for series, count in service.held()
        if series.source == source and series.symbol == symbol and count
    }
    if wanted in held:
        return wanted
    usable = [i for i in held if wanted.seconds % i.seconds == 0 and i.seconds < wanted.seconds]
    if not usable:
        return wanted
    return max(usable, key=lambda i: i.seconds)


def _sentence(spec: Any) -> str:
    """The strategy read back, so a result says what produced it."""
    parts: list[str] = []
    if spec.sessions:
        during = " or ".join(s.describe() for s in spec.sessions)
        parts.append(f"Only during {during}")
        if spec.close_outside_session:
            parts.append("Close whatever is open when the session ends")
    if spec.long_entry:
        parts.append(f"Long when {spec.long_entry.describe()}")
    if spec.short_entry:
        parts.append(f"Short when {spec.short_entry.describe()}")
    if spec.long_exit:
        parts.append(f"Close longs when {spec.long_exit.describe()}")
    if spec.short_exit:
        parts.append(f"Close shorts when {spec.short_exit.describe()}")
    return ". ".join(parts)


def _thinned(bars: list[Any], equity: list[float]) -> list[list[float]]:
    """The curve, at a size worth sending.

    Each bucket keeps its lowest point rather than its last. A curve thinned by
    sampling loses exactly the spikes a drawdown is made of, and a drawdown that
    disappears when a chart is drawn is the one figure nobody should have to
    take on trust.
    """
    if not equity:
        return []
    step = max(1, len(equity) // CURVE_POINTS)
    out: list[list[float]] = []
    for at in range(0, len(equity), step):
        window = equity[at : at + step]
        lowest = min(range(len(window)), key=lambda k: window[k])
        index = at + lowest
        out.append([bars[index].ts.timestamp(), round(equity[index], 2)])
    # The end matters: it is the result.
    last = len(equity) - 1
    if out[-1][0] != bars[last].ts.timestamp():
        out.append([bars[last].ts.timestamp(), round(equity[last], 2)])
    return out


def _metrics_out(m: Metrics) -> MetricsOut:
    return MetricsOut(
        trades=m.trades,
        wins=m.wins,
        win_rate=m.win_rate,
        total_return=m.total_return,
        buy_and_hold=m.buy_and_hold,
        beat_holding=m.beat_holding,
        max_drawdown=m.max_drawdown,
        sharpe=m.sharpe,
        gross=m.gross,
        fees=m.fees,
        funding=m.funding,
        slippage=m.slippage,
        cost_share=m.cost_share,
        exposure=m.exposure,
        best=m.best,
        worst=m.worst,
        endings=m.endings,
        average_bars_held=m.average_bars_held,
        by_side={
            side: SideOut(
                trades=s.trades, wins=s.wins, win_rate=s.win_rate, gross=s.gross, net=s.net
            )
            for side, s in m.by_side.items()
        },
    )


def _trade_out(t: Any, interval: Interval, execution: Execution) -> TradeOut:
    notional = t.quantity * t.entry
    margin = notional / execution.leverage if execution.leverage else notional
    return TradeOut(
        side=str(t.side),
        opened_at=t.opened_at.isoformat(),
        closed_at=t.closed_at.isoformat(),
        entry=t.entry,
        exit_price=t.exit_price,
        quantity=t.quantity,
        notional=notional,
        why=str(t.why),
        entry_reason=t.entry_reason,
        exit_reason=t.exit_reason,
        gross=t.gross,
        fees=t.costs.fees,
        funding=t.costs.funding,
        slippage=t.costs.slippage,
        net=t.net,
        # Against the margin the position tied up rather than against the whole
        # account: at 10x a 1% move is 10% of what was committed, and reporting
        # it as 1% would describe a different trade.
        net_pct=t.net / margin if margin else 0.0,
        bars_held=t.bars_held / interval.seconds,
    )
