"""NSE's pre-open auction, as the desk has recorded it.

Read-only. The recorder in `storage/preopen_recorder.py` writes it each morning
and `scripts/preopen.py` imports older days from downloaded files; this only
shows what is there.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import DbPathDep
from api.store import open_db
from storage.preopen_repo import quotes_for_day, recorded_days
from universe.nse import INDICES, load

router = APIRouter(tags=["preopen"], prefix="/api/preopen")

#: The indices a stock can be filtered by: the ones with options on this desk.
#: Membership is today's list, not the list on the day - the constituent files
#: are a snapshot - which is close enough for reading a morning and wrong for a
#: survivorship-sensitive backtest.
FILTER_INDICES = ("NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")


class IndexOut(BaseModel):
    name: str
    price: float
    change: float
    pct_change: float


class DayOut(BaseModel):
    day: date
    source: str
    as_of: datetime | None
    rows: int
    index: IndexOut | None
    advances: int
    declines: int
    unchanged: int


class RecorderOut(BaseModel):
    running: bool
    last_day: date | None
    last_error: str | None


class DaysOut(BaseModel):
    days: list[DayOut]
    recorder: RecorderOut


class LevelOut(BaseModel):
    price: float
    buy_qty: int
    sell_qty: int
    is_iep: bool


class QuoteOut(BaseModel):
    symbol: str
    name: str | None
    industry: str | None
    indices: list[str]
    prev_close: float
    final_price: float
    final_quantity: int
    change: float
    pct_change: float
    turnover_cr: float | None
    ffm_cap_cr: float | None
    best_bid: float | None
    best_ask: float | None
    total_buy_qty: int | None
    total_sell_qty: int | None
    year_high: float | None
    year_low: float | None
    book: list[LevelOut]


class FilterOut(BaseModel):
    id: str
    name: str


class SessionOut(BaseModel):
    day: DayOut
    quotes: list[QuoteOut]
    filters: list[FilterOut]


def _breadth(conn: sqlite3.Connection) -> dict[str, tuple[int, int, int]]:
    rows = conn.execute(
        "SELECT day, SUM(change > 0), SUM(change < 0), SUM(change = 0) "
        "FROM preopen_quote GROUP BY day"
    ).fetchall()
    return {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows}


def _days(db_path: Path) -> list[DayOut]:
    conn = open_db(db_path)
    try:
        breadth = _breadth(conn)
        out = []
        for d in recorded_days(conn):
            up, down, flat = breadth.get(d.day.isoformat(), (0, 0, 0))
            out.append(DayOut(
                day=d.day, source=d.source, as_of=d.as_of, rows=d.rows,
                index=IndexOut(**vars(d.index)) if d.index else None,
                advances=up, declines=down, unchanged=flat,
            ))
        return out
    finally:
        conn.close()


@router.get("/days", response_model=DaysOut)
def days(request: Request, db_path: DbPathDep) -> DaysOut:
    """Every recorded session, newest first, and whether the recorder is well."""
    recorder = getattr(request.app.state, "preopen_recorder", None)
    return DaysOut(
        days=list(reversed(_days(db_path))),
        recorder=RecorderOut(
            running=recorder is not None,
            last_day=getattr(recorder, "last_day", None),
            last_error=getattr(recorder, "last_error", None),
        ),
    )


@router.get("/days/{day}", response_model=SessionOut)
def session(day: date, db_path: DbPathDep) -> SessionOut:
    """One session: every stock, its book where there is one."""
    summary = next((d for d in _days(db_path) if d.day == day), None)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"No pre-open recorded for {day}")
    conn = open_db(db_path)
    try:
        quotes = quotes_for_day(conn, day)
    finally:
        conn.close()

    memberships = load()
    names = {spec.id: spec.name for spec in INDICES}
    member_of: dict[str, list[str]] = {}
    about: dict[str, tuple[str, str]] = {}
    for index_id, membership in memberships.items():
        for m in membership.members:
            about.setdefault(m.symbol, (m.name, m.industry))
            if index_id in FILTER_INDICES:
                member_of.setdefault(m.symbol, []).append(index_id)

    return SessionOut(
        day=summary,
        quotes=[
            QuoteOut(
                symbol=q.symbol,
                name=about.get(q.symbol, (None, None))[0],
                industry=about.get(q.symbol, (None, None))[1],
                indices=member_of.get(q.symbol, []),
                prev_close=q.prev_close,
                final_price=q.final_price,
                final_quantity=q.final_quantity,
                change=q.change,
                pct_change=q.pct_change,
                turnover_cr=q.turnover_cr,
                ffm_cap_cr=q.ffm_cap_cr,
                best_bid=q.best_bid,
                best_ask=q.best_ask,
                total_buy_qty=q.total_buy_qty,
                total_sell_qty=q.total_sell_qty,
                year_high=q.year_high,
                year_low=q.year_low,
                book=[LevelOut(**vars(lvl)) for lvl in q.book],
            )
            for q in quotes
        ],
        filters=[
            FilterOut(id=i, name=names.get(i, i))
            for i in FILTER_INDICES if i in memberships
        ],
    )
