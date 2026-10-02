"""Reading and writing NSE's pre-open auction, one row per stock per day.

A day can arrive more than once and from two places: the recorder fetches the
API through the day, and older days come in from files downloaded by hand. The
rules for putting them together:

- The API wins. It is dated by NSE's own timestamp and carries fields a file
  does not, so its rows replace whatever a file said.
- A file never overwrites a stored row. It can add stocks a day does not have
  yet - a NIFTY BANK file after a NIFTY 50 one - but if it disagrees with a row
  already there, the day is reported as a conflict and left alone.
- A file whose rows are exactly another day's is a copy saved under the wrong
  date, and is skipped. The page names its download after the date on screen,
  which is not always the session the figures are from.

`replace=True` overrides the last two, for when the stored day is the one that
is wrong.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime

from marketdata.nse_preopen import BookLevel, IndexPreOpen, PreOpenDay, PreOpenQuote

#: Every stored column of a quote, in table order.
_COLUMNS = tuple(f.name for f in fields(PreOpenQuote) if f.name != "book")

#: What both sources report, so a file and the API for the same session agree
#: on these and nothing else is compared between them.
_CORE = ("symbol", "prev_close", "final_price", "final_quantity")


@dataclass(frozen=True)
class SaveResult:
    day: date
    #: `stored` (a new day), `added` (new stocks on a known day), `updated`
    #: (the API changed rows), `unchanged`, `conflict`, `duplicate`, `unsettled`.
    outcome: str
    rows: int = 0
    detail: str = ""

    @property
    def written(self) -> bool:
        return self.outcome in ("stored", "added", "updated")


@dataclass(frozen=True)
class RecordedDay:
    day: date
    source: str
    as_of: datetime | None
    rows: int
    recorded_at: datetime
    index: IndexPreOpen | None = None


def fingerprint(quotes: tuple[PreOpenQuote, ...] | list[PreOpenQuote]) -> str:
    core = sorted(tuple(getattr(q, c) for c in _CORE) for q in quotes)
    return hashlib.sha256(repr(core).encode()).hexdigest()


def save_day(
    conn: sqlite3.Connection,
    session: PreOpenDay,
    *,
    replace: bool = False,
    now: datetime | None = None,
) -> SaveResult:
    """Record a session's pre-open. Safe to repeat: the same data twice is
    `unchanged`, not a second copy."""
    day = session.day
    if not session.settled:
        # Written at 09:05 this would be a guess from an auction still taking
        # orders, and nothing afterwards would say so.
        return SaveResult(day, "unsettled", detail=f"figures from {session.as_of:%H:%M:%S}")

    from_api = session.source == "nse"
    incoming = {q.symbol: q for q in session.quotes}
    key = day.isoformat()

    if not from_api and not replace:
        twin = conn.execute(
            "SELECT day FROM preopen_day WHERE fingerprint = ? AND day != ?",
            (fingerprint(session.quotes), key),
        ).fetchone()
        if twin is not None:
            return SaveResult(day, "duplicate", detail=f"same rows as {twin[0]}")

    stored = {
        row[0]: row
        for row in conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM preopen_quote WHERE day = ?", (key,)
        ).fetchall()
    }
    compare = _COLUMNS if from_api else _CORE
    index = [_COLUMNS.index(c) for c in compare]
    new = [s for s in incoming if s not in stored]
    changed = [
        s for s, q in incoming.items()
        if s in stored and tuple(getattr(q, c) for c in compare)
        != tuple(stored[s][i] for i in index)
    ]

    if changed and not from_api and not replace:
        return SaveResult(
            day, "conflict",
            detail=f"{len(changed)} stocks differ from what is stored, e.g. {changed[0]}",
        )
    write = new + changed if (from_api or replace) else new
    index_known = conn.execute(
        "SELECT 1 FROM preopen_day WHERE day = ? AND index_price IS NOT NULL", (key,)
    ).fetchone()
    if not write and (session.index is None or index_known):
        return SaveResult(day, "unchanged", rows=len(stored))

    marks = ", ".join("?" * len(_COLUMNS))
    conn.executemany(
        f"INSERT OR REPLACE INTO preopen_quote (day, {', '.join(_COLUMNS)}) "
        f"VALUES (?, {marks})",
        [(key, *(getattr(incoming[s], c) for c in _COLUMNS)) for s in write],
    )
    if from_api:
        conn.executemany(
            "DELETE FROM preopen_book WHERE day = ? AND symbol = ?",
            [(key, s) for s in write],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO preopen_book "
            "(day, symbol, price, buy_qty, sell_qty, is_iep) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (key, s, lvl.price, lvl.buy_qty, lvl.sell_qty, int(lvl.is_iep))
                for s in write for lvl in incoming[s].book
            ],
        )
    _refresh_day(conn, session, now or datetime.now(UTC))
    conn.commit()

    if not stored:
        outcome = "stored"
    elif changed or not write:
        # Nothing new but the index figure is still an update to the day.
        outcome = "updated"
    else:
        outcome = "added"
    return SaveResult(day, outcome, rows=len(write))


def _refresh_day(conn: sqlite3.Connection, session: PreOpenDay, now: datetime) -> None:
    key = session.day.isoformat()
    previous = conn.execute(
        "SELECT source, as_of, index_price, index_change, index_pct_change "
        "FROM preopen_day WHERE day = ?", (key,)
    ).fetchone()
    source = "nse" if session.source == "nse" or (previous and previous[0] == "nse") else "csv"
    as_of = session.as_of.isoformat() if session.as_of else (previous[1] if previous else None)
    index = session.index
    figures = (
        (index.price, index.change, index.pct_change) if index
        else tuple(previous[2:]) if previous else (None, None, None)
    )
    quotes = quotes_for_day(conn, session.day)
    conn.execute(
        "INSERT OR REPLACE INTO preopen_day (day, source, as_of, fingerprint, rows, "
        "index_price, index_change, index_pct_change, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, source, as_of, fingerprint(quotes), len(quotes), *figures, now.isoformat()),
    )


def recorded_days(conn: sqlite3.Connection) -> list[RecordedDay]:
    return [
        RecordedDay(
            day=date.fromisoformat(r[0]),
            source=r[1],
            as_of=datetime.fromisoformat(r[2]) if r[2] else None,
            rows=r[3],
            recorded_at=datetime.fromisoformat(r[4]),
            index=IndexPreOpen("NIFTY 50", r[5], r[6], r[7]) if r[5] is not None else None,
        )
        for r in conn.execute(
            "SELECT day, source, as_of, rows, recorded_at, "
            "index_price, index_change, index_pct_change FROM preopen_day ORDER BY day"
        ).fetchall()
    ]


def breadth_by_day(conn: sqlite3.Connection) -> dict[date, tuple[int, int, int]]:
    """Advances, declines and unchanged for every recorded day."""
    rows = conn.execute(
        "SELECT day, SUM(change > 0), SUM(change < 0), SUM(change = 0) "
        "FROM preopen_quote GROUP BY day"
    ).fetchall()
    return {
        date.fromisoformat(r[0]): (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows
    }


def quotes_for_day(conn: sqlite3.Connection, day: date) -> list[PreOpenQuote]:
    """A day's stocks, with their books where the API supplied them."""
    key = day.isoformat()
    books: dict[str, list[BookLevel]] = {}
    for symbol, price, buy, sell, is_iep in conn.execute(
        "SELECT symbol, price, buy_qty, sell_qty, is_iep FROM preopen_book "
        "WHERE day = ? ORDER BY symbol, price",
        (key,),
    ).fetchall():
        books.setdefault(symbol, []).append(BookLevel(price, buy, sell, bool(is_iep)))
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM preopen_quote WHERE day = ? ORDER BY symbol",
        (key,),
    ).fetchall()
    return [
        PreOpenQuote(**dict(zip(_COLUMNS, row, strict=True)),
                     book=tuple(books.get(row[0], ())))
        for row in rows
    ]


def symbol_history(conn: sqlite3.Connection, symbol: str) -> list[tuple[date, PreOpenQuote]]:
    """One stock's pre-open on every recorded day, oldest first. No books -
    a backtest over a year of days wants the figures, not ten levels each."""
    rows = conn.execute(
        f"SELECT day, {', '.join(_COLUMNS)} FROM preopen_quote WHERE symbol = ? ORDER BY day",
        (symbol,),
    ).fetchall()
    return [
        (date.fromisoformat(row[0]), PreOpenQuote(**dict(zip(_COLUMNS, row[1:], strict=True))))
        for row in rows
    ]
