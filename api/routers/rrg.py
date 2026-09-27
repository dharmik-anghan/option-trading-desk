"""Relative rotation: what is leading, improving, weakening and lagging.

One request draws a whole graph. It returns a path per security rather than a
single point, because the rotation is the information - where something is
matters much less than which way it is travelling, and a tail of a few periods
shows that where a dot cannot.

The path also makes replay free: the client already holds the history, so
stepping back through it is a slider rather than a request per frame.

Read from the bar store only. Nothing here goes to a venue: a graph of two
hundred securities would be two hundred requests, and the point of storing daily
bars is that it is one query.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from analytics.rrg import DEFAULT_WINDOW, Quadrant, rrg
from backtest.resample import resample
from marketdata import BarService, Interval
from marketdata.models import Bar
from universe.nse import (
    DEFAULT_BENCHMARK,
    INDICES,
    IndexSpec,
    constituents,
    fyers_symbol,
    index,
    sectors,
)

router = APIRouter(tags=["rrg"], prefix="/api/rrg")

#: Where Indian daily bars are stored. One source, because mixing two would put
#: two different closes for the same day on one graph.
SOURCE = "fyers"

#: How much of each path to return. Enough for a long tail and a long replay,
#: short enough that two hundred securities is not megabytes.
DEFAULT_POINTS = 120


class Options(BaseModel):
    """What can be asked for."""

    indices: list[dict[str, Any]]
    timeframes: list[str]
    benchmark: str


class PathPoint(BaseModel):
    at: str
    ratio: float
    momentum: float


class SeriesOut(BaseModel):
    symbol: str
    #: What to label the dot with. Short, because it is drawn on the graph.
    label: str
    name: str
    #: Newest last, so the head of the tail is the last element.
    path: list[PathPoint]
    quadrant: str


class SnapshotResponse(BaseModel):
    #: What was plotted and against what.
    index_id: str
    index_name: str
    benchmark: str
    benchmark_name: str
    timeframe: str
    window: int
    #: The members that had enough history to place. Those that did not are
    #: named, because a missing sector is otherwise indistinguishable from one
    #: that happens to sit under another dot.
    series: list[SeriesOut]
    missing: list[str]
    #: When the constituent list was fetched. Membership changes, and a graph
    #: drawn over two years with today's list is measuring a universe that did
    #: not exist then.
    members_as_at: str | None
    caveats: list[str]


def _service(request: Request) -> BarService:
    service = getattr(request.app.state, "bar_service", None)
    if not isinstance(service, BarService):
        raise HTTPException(status_code=503, detail="The bar store is not open")
    return service


@router.get("/options", response_model=Options)
def options() -> Options:
    """Everything that can be plotted, for the picker.

    "All sectors" is first because it is the view most people mean by an RRG:
    every sector index against the broad market.
    """
    listed: list[dict[str, Any]] = [
        {"id": "SECTORS", "name": "All sectors", "plots": len(sectors()), "kind": "sectors"}
    ]
    for spec in INDICES:
        if spec.members_file is None:
            continue
        held = constituents(spec.id)
        listed.append(
            {
                "id": spec.id,
                "name": spec.name,
                "plots": len(held.members) if held else 0,
                "kind": "constituents",
            }
        )
    return Options(
        indices=listed,
        timeframes=["daily", "weekly"],
        benchmark=DEFAULT_BENCHMARK,
    )


@router.get("/snapshot", response_model=SnapshotResponse)
def snapshot(
    request: Request,
    index_id: str = "SECTORS",
    timeframe: str = "daily",
    benchmark: str = DEFAULT_BENCHMARK,
    window: int = DEFAULT_WINDOW,
    points: int = DEFAULT_POINTS,
) -> SnapshotResponse:
    """The rotation graph for one index against one benchmark."""
    if timeframe not in ("daily", "weekly"):
        raise HTTPException(status_code=400, detail="timeframe is daily or weekly")
    if not 2 <= window <= 200:
        raise HTTPException(status_code=400, detail="window has to be between 2 and 200")
    points = max(2, min(500, points))

    mark = index(benchmark)
    if mark is None:
        raise HTTPException(status_code=400, detail=f"{benchmark} is not an index here")

    wanted_id = index_id.strip().upper()
    plotted, members_at, caveats = _members(wanted_id)
    if not plotted:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Nothing to plot for {wanted_id}. Run scripts/fetch_constituents.py, "
                "then scripts/backfill_nse.py."
            ),
        )

    service = _service(request)
    weekly = timeframe == "weekly"
    mark_bars = _read(service, mark.symbol, weekly)
    if len(mark_bars) < window * 2:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Not enough stored history for {mark.name} to normalise against. "
                "Run scripts/backfill_nse.py."
            ),
        )

    by_time = {bar.ts: bar.close for bar in mark_bars}
    out: list[SeriesOut] = []
    missing: list[str] = []

    for symbol, label, name in plotted:
        bars = _read(service, symbol, weekly)
        # Only the days both have. A security that did not trade on a day the
        # index did would otherwise be compared against the wrong close.
        paired = [(b.ts, b.close, by_time[b.ts]) for b in bars if b.ts in by_time]
        if len(paired) < window * 2:
            missing.append(label)
            continue
        path = rrg(
            [close for _, close, _ in paired],
            [mark_close for _, _, mark_close in paired],
            [at for at, _, _ in paired],
            window=window,
        )
        if not path:
            missing.append(label)
            continue
        tail = path[-points:]
        out.append(
            SeriesOut(
                symbol=symbol,
                label=label,
                name=name,
                path=[
                    PathPoint(at=p.at.isoformat(), ratio=p.ratio, momentum=p.momentum)
                    for p in tail
                ],
                quadrant=str(Quadrant.of(tail[-1].ratio, tail[-1].momentum)),
            )
        )

    if missing:
        caveats.append(
            f"{len(missing)} of {len(plotted)} could not be placed for want of stored "
            f"history: {', '.join(missing[:8])}"
            + (" and others" if len(missing) > 8 else "")
        )

    return SnapshotResponse(
        index_id=wanted_id,
        index_name="All sectors" if wanted_id == "SECTORS" else _name(wanted_id),
        benchmark=mark.id,
        benchmark_name=mark.name,
        timeframe=timeframe,
        window=window,
        series=sorted(out, key=lambda s: s.label),
        missing=missing,
        members_as_at=members_at,
        caveats=caveats,
    )


def _name(index_id: str) -> str:
    spec = index(index_id)
    return spec.name if spec else index_id


def _members(index_id: str) -> tuple[list[tuple[str, str, str]], str | None, list[str]]:
    """What to plot: (symbol, label, name) for each, and when the list is from."""
    if index_id == "SECTORS":
        return (
            [(spec.symbol, spec.name, spec.name) for spec in sectors()],
            None,
            [],
        )

    spec: IndexSpec | None = index(index_id)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"{index_id} is not an index here")
    if spec.members_file is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{spec.name} publishes no constituent list, so it can be a benchmark "
                "but has nothing to plot"
            ),
        )

    held = constituents(index_id)
    if held is None:
        return [], None, []

    caveats = [
        f"Members are {spec.name}'s as at {held.at:%d %b %Y}. Membership changes, and a "
        "graph drawn over a long window with today's list leaves out what fell out of "
        "the index - which flatters, because indices drop what has done badly"
    ]
    return (
        [(fyers_symbol(m), m.symbol, m.name) for m in held.members],
        held.at.isoformat(),
        caveats,
    )


def _read(service: BarService, symbol: str, weekly: bool) -> list[Bar]:
    """Stored daily bars, resampled to weeks when asked.

    Weekly is built from the daily series rather than fetched, for the same
    reason every other higher timeframe here is: two fetched series can disagree
    about a boundary and nothing would show it.
    """
    bars = service.stored(SOURCE, symbol, Interval.D1, days=365 * 6).bars
    return resample(bars, Interval.W1) if weekly else bars
