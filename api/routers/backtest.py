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

#: Trades sent back. A five-minute rule can make tens of thousands, and nobody
#: reads past the first page of those.
MAX_TRADES = 400


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
    #: Whose funding to charge. Empty means none, which is only honest for an
    #: instrument that has none.
    funding_source: str = "binance"


class TradeOut(BaseModel):
    side: str
    opened_at: str
    closed_at: str
    entry: float
    exit_price: float
    quantity: float
    why: str
    reason: str
    gross: float
    fees: float
    funding: float
    slippage: float
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
    #: Anything a reader has to know to judge the numbers.
    caveats: list[str]


def _service(request: Request) -> BarService:
    service = getattr(request.app.state, "bar_service", None)
    if not isinstance(service, BarService):
        raise HTTPException(
            status_code=503, detail="The bar store is not open, so there is nothing to test on"
        )
    return service


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

    market = PerpetualMarket(symbol=body.symbol, funding=funding)
    result = run(
        bars,
        SpecRule(spec),
        market,
        interval=size,
        execution=Execution(
            capital=body.capital,
            leverage=body.leverage,
            slippage_bps=body.slippage_bps,
            maker_entry=body.maker_entry,
        ),
    )
    metrics = measure(result.trades, result.equity, bars, result.capital, size)
    caveats.extend(result.caveats)
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
        trades=[_trade_out(t) for t in result.trades[-MAX_TRADES:]],
        trades_total=len(result.trades),
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
    )


def _trade_out(t: Any) -> TradeOut:
    return TradeOut(
        side=str(t.side),
        opened_at=t.opened_at.isoformat(),
        closed_at=t.closed_at.isoformat(),
        entry=t.entry,
        exit_price=t.exit_price,
        quantity=t.quantity,
        why=str(t.why),
        reason=t.reason,
        gross=t.gross,
        fees=t.costs.fees,
        funding=t.costs.funding,
        slippage=t.costs.slippage,
        net=t.net,
    )
