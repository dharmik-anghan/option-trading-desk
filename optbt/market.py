"""What the engine knows about the market, and what a strategy is allowed to see.

Two objects, deliberately separate:

  `History` reads the store. The engine uses it to fill orders, which means it
  reads the bar a fill happens in - a bar the strategy has not seen close.

  `View` is what a strategy decides with. It is pinned to the last *closed* bar
  and has no way to read past it. Every "beautiful meaningless equity curve" in
  options backtesting comes from a strategy that could see a price from the
  minute it was trading in; making the strategy's only window unable to do that
  is cheaper than checking every strategy for it.

A bar is named by the minute it starts. The bar named 09:19 closes at 09:20, so a
decision "at 09:20" is made on the 09:19 close and fills in the 09:20 bar.

The session is the index's: 09:15 to 15:29. Option bars run to 15:39 in the store
- a closing session with real volume - but nothing can be opened or closed there
on a normal order, so the engine never looks at them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import duckdb

from optbt.data.models import Kind
from venues.calendar import NSE_OPEN
from venues.instruments import OPTION_SERIES

SESSION_OPEN = NSE_OPEN
SESSION_LAST_BAR = time(15, 29)

#: Every lot size each index has used. Per underlying, because a size from another
#: index can fit by coincidence: 120 (MIDCPNIFTY's) divides any NIFTY figure that
#: is a multiple of 600, and was picked for two 2025 days before this was split.
KNOWN_LOT_SIZES: dict[str, frozenset[int]] = {
    "NIFTY": frozenset({25, 50, 65, 75}),
    "BANKNIFTY": frozenset({15, 25, 30, 35}),
    "FINNIFTY": frozenset({25, 40, 65}),
    "MIDCPNIFTY": frozenset({50, 75, 120, 140}),
    "SENSEX": frozenset({10, 20}),
}

#: Share of a day's open-interest figures a lot size must divide to be the lot.
#: Not 95%: a monthly listed before a lot change carries positions in the old
#: size, and on 25 Mar and 29 Jun 2026 only 89-90% of the monthly's figures were
#: whole lots of 65 - the rest were from when it was 75, which fitted 6%. 80%
#: still separates the lot from the others by a wide margin.
LOT_VOTE = 0.80


@dataclass(frozen=True, order=True)
class OptionKey:
    """One option contract, by what it is rather than by a broker's symbol."""

    expiry: date
    strike: float
    kind: Kind

    def intrinsic(self, spot: float) -> float:
        if self.kind is Kind.CALL:
            return max(0.0, spot - self.strike)
        return max(0.0, self.strike - spot)

    def __str__(self) -> str:
        return f"{self.expiry:%d%b%y} {self.strike:g} {self.kind}"


@dataclass(frozen=True)
class Bar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Quote:
    """One contract at the last closed bar, as a strategy sees it."""

    key: OptionKey
    price: float
    volume: int
    oi: int


class History:
    """Read access to the option store. One underlying per instance.

    Caches what a run asks for repeatedly - a day's index bars, a contract's bars
    for a day - because a four-year run asks for the same day's spot hundreds of
    times and each query, fast as it is, is still a round trip.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection, underlying: str = "NIFTY") -> None:
        self._conn = conn
        self.underlying = underlying
        self.index_symbol = OPTION_SERIES[underlying]
        self._index: dict[date, list[Bar]] = {}
        self._contract: dict[tuple[OptionKey, date], dict[datetime, Bar]] = {}
        self._lots: dict[tuple[date, date], int] = {}
        self._prev: dict[tuple[OptionKey, date], float | None] = {}
        self._expiries: list[date] | None = None
        self._monthlies: list[date] | None = None

    @classmethod
    def open(cls, path: Path | str, underlying: str = "NIFTY", *, wait: float = 30.0) -> History:
        """Read-only, and waiting out a writer: a backfill holds the file only for
        the moment of each write, so a backtest that arrives mid-write waits
        milliseconds rather than being refused."""
        from optbt.data.store import connect

        return cls(connect(str(path), read_only=True, wait=wait), underlying)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------- calendar

    def trading_days(self, start: date, end: date) -> list[date]:
        rows = self._conn.execute(
            "SELECT DISTINCT CAST(ts AS DATE) AS d FROM index_bar "
            "WHERE symbol = ? AND ts >= ? AND ts < ? + INTERVAL 1 DAY ORDER BY d",
            [self.index_symbol, start, end],
        ).fetchall()
        return [row[0] for row in rows]

    def next_trading_day(self, day: date) -> date | None:
        """The first session after `day`, or None past the end of the data."""
        row = self._conn.execute(
            "SELECT min(CAST(ts AS DATE)) FROM index_bar "
            "WHERE symbol = ? AND ts >= ? + INTERVAL 1 DAY",
            [self.index_symbol, day],
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def expiries(self) -> list[date]:
        """Every option expiry the exchange listed, ascending - held or not.

        From the calendar, not from what has been fetched. Asking only what is
        held would make "the nearest expiry" silently mean "the nearest one we
        happen to have", and a straddle would trade next week's contracts on a
        day whose own weekly was never downloaded. With the calendar, that day's
        chain comes back empty and the day is skipped, visibly.
        """
        if self._expiries is None:
            rows = self._conn.execute(
                "SELECT expiry FROM expiry WHERE underlying = ? AND kind = 'options' "
                "ORDER BY expiry",
                [self.underlying],
            ).fetchall()
            self._expiries = [row[0] for row in rows]
        return self._expiries

    def monthly_expiries(self) -> list[date]:
        """The monthly expiries: those futures expire on too."""
        if self._monthlies is None:
            rows = self._conn.execute(
                "SELECT expiry FROM expiry WHERE underlying = ? AND kind = 'futures' "
                "ORDER BY expiry",
                [self.underlying],
            ).fetchall()
            self._monthlies = [row[0] for row in rows]
        return self._monthlies

    # ----------------------------------------------------------------- index

    def index_day(self, day: date) -> list[Bar]:
        """The index's session bars for a day, in order."""
        if day not in self._index:
            rows = self._conn.execute(
                "SELECT ts, open, high, low, close, volume FROM index_bar "
                "WHERE symbol = ? AND resolution = '1' AND ts >= ? AND ts <= ? ORDER BY ts",
                [
                    self.index_symbol,
                    datetime.combine(day, SESSION_OPEN),
                    datetime.combine(day, SESSION_LAST_BAR),
                ],
            ).fetchall()
            self._index = {day: [Bar(*row) for row in rows]}  # one day held at a time
        return self._index[day]

    # --------------------------------------------------------------- options

    def contract_day(self, key: OptionKey, day: date) -> dict[datetime, Bar]:
        """A contract's session bars for a day, by minute."""
        cache_key = (key, day)
        if cache_key not in self._contract:
            if len(self._contract) > 512:
                self._contract.clear()
            rows = self._conn.execute(
                "SELECT ts, open, high, low, close, volume FROM option_bar "
                "WHERE underlying = ? AND expiry = ? AND strike = ? AND kind = ? "
                "AND ts >= ? AND ts <= ?",
                [
                    self.underlying,
                    key.expiry,
                    key.strike,
                    str(key.kind),
                    datetime.combine(day, SESSION_OPEN),
                    datetime.combine(day, SESSION_LAST_BAR),
                ],
            ).fetchall()
            self._contract[cache_key] = {row[0]: Bar(*row) for row in rows}
        return self._contract[cache_key]

    def prev_close(self, key: OptionKey, day: date) -> float | None:
        """The contract's last close before `day`'s session, or None if it had
        never traded. Cached: asked every minute of a day the contract is quiet."""
        cache_key = (key, day)
        if cache_key not in self._prev:
            row = self._conn.execute(
                "SELECT close FROM option_bar WHERE underlying = ? AND expiry = ? "
                "AND strike = ? AND kind = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                [self.underlying, key.expiry, key.strike, str(key.kind), day],
            ).fetchone()
            self._prev[cache_key] = float(row[0]) if row else None
        return self._prev[cache_key]

    def chain_at(self, expiry: date, ts: datetime) -> list[Quote]:
        """Every contract of one expiry, at the close of the bar named `ts`."""
        rows = self._conn.execute(
            "SELECT strike, kind, close, volume, oi FROM option_bar "
            "WHERE underlying = ? AND expiry = ? AND ts = ? AND kind <> 'FUT' "
            "ORDER BY strike, kind",
            [self.underlying, expiry, ts],
        ).fetchall()
        return [
            Quote(OptionKey(expiry, float(r[0]), Kind(r[1])), float(r[2]), int(r[3]), int(r[4]))
            for r in rows
        ]

    def lot_size(self, day: date, expiry: date) -> int:
        """The lot size in force on a day, read from the data rather than a table.

        Open interest and volume are whole lots, so the lot is the largest known
        size that divides most of a day's figures. A table typed from memory is
        how every trade in a run ends up mis-sized: NIFTY has been 75, 50, 25, 75
        and 65 inside four years.

        Two sources, because each is unreliable in its own way:

        - Open interest carries positions opened under an earlier lot. In the
          week of the 27 Mar 2025 monthly, 6% of its figures were still lots of
          25 from 2024 - so 75 fitted only 94%, and a strict test picked 25,
          sizing those trades at a third.
        - Volume is only ever new trades, so always the current lot - except
          that Fyers' volume in 2026 has stray figures (45,502 on 3 Jul 2026),
          and on 25 Mar 2026 only 60% of it divided by 65.

        So each size is judged by whichever source supports it better, and the
        largest size either source puts past LOT_VOTE wins.
        """
        if (day, expiry) not in self._lots:
            known = KNOWN_LOT_SIZES[self.underlying]
            lot = _vote_lot(self._figures(day, expiry), known)
            if lot is None:
                # Too little on this expiry today - the day's contracts together,
                # dominated by the near expiries, still say what a trade is sized in.
                lot = _vote_lot(self._figures(day, None), known)
            if lot is None:
                raise ValueError(f"no lot size fits the open interest on {day} for {expiry}")
            self._lots[(day, expiry)] = lot
        return self._lots[(day, expiry)]

    def _figures(self, day: date, expiry: date | None) -> tuple[list[int], list[int]]:
        """The day's distinct open-interest and volume figures."""
        where = "underlying = ? AND kind <> 'FUT' AND ts >= ? AND ts < ? + INTERVAL 1 DAY"
        params: list[object] = [self.underlying, day, day]
        if expiry is not None:
            where += " AND expiry = ?"
            params.append(expiry)
        oi = self._conn.execute(
            f"SELECT DISTINCT oi FROM option_bar WHERE {where} AND oi > 0", params
        ).fetchall()
        volume = self._conn.execute(
            f"SELECT DISTINCT volume FROM option_bar WHERE {where} AND volume > 0", params
        ).fetchall()
        return [r[0] for r in oi], [r[0] for r in volume]


def _vote_lot(
    figures: tuple[Sequence[int], Sequence[int]], known: frozenset[int]
) -> int | None:
    """The largest known lot that divides at least LOT_VOTE of the open interest
    figures or of the volume figures, whichever supports it better."""

    def share(values: Sequence[int], lot: int) -> float:
        return sum(1 for v in values if v % lot == 0) / len(values) if values else 0.0

    oi, volume = figures
    for lot in sorted(known, reverse=True):
        if max(share(oi, lot), share(volume, lot)) >= LOT_VOTE:
            return lot
    return None


class View:
    """The market as of the last closed bar. All a strategy can see."""

    def __init__(
        self, history: History, day: date, bars: Sequence[Bar], engine: Any = None
    ) -> None:
        self._history = history
        self._engine = engine
        self.day = day
        self._bars = bars
        self._i = -1

    @property
    def context(self) -> Any:
        """The run's daily context (pivots, gap, VIX): `optbt.context.Context`."""
        return self._engine.context

    def _advance(self, i: int) -> None:
        """Engine only: bar `i` of the day has closed."""
        self._i = i

    @property
    def now(self) -> datetime:
        """The start of the last closed bar. A decision now fills in the next one."""
        return self._bars[self._i].ts

    @property
    def clock(self) -> time:
        """The time a decision is being made at: the close of the last bar."""
        ts = self.now
        return time(ts.hour, ts.minute + 1) if ts.minute < 59 else time(ts.hour + 1, 0)

    @property
    def session_start(self) -> time:
        """When today's first bar closed - the earliest a decision can be made."""
        first = self._bars[0].ts
        return (first + timedelta(minutes=1)).time()

    @property
    def bars_left(self) -> int:
        """Bars still to come today. Zero on the last one - which is not always
        15:29: a Saturday special session ended at 12:29."""
        return len(self._bars) - 1 - self._i

    def spot(self) -> float:
        return self._bars[self._i].close

    def expiries(self) -> list[date]:
        """Expiries not yet passed, nearest first."""
        return [e for e in self._history.expiries() if e >= self.day]

    def monthly_expiries(self) -> list[date]:
        """Monthly expiries not yet passed, nearest first."""
        return [e for e in self._history.monthly_expiries() if e >= self.day]

    def chain(self, expiry: date) -> list[Quote]:
        return self._history.chain_at(expiry, self.now)

    def price(self, key: OptionKey) -> float | None:
        """Last traded price of one contract as of the last closed bar.

        Today's latest bar if it has traded today; otherwise its last close from
        an earlier session. Only valuing it on days it traded left a condor with
        an illiquid wing unvalued - and its stop and target unchecked - for whole
        days: the 19400 CE of Jan 2024 did not trade on 4 of its 34 sessions.
        This is a mark, not a fill: an order still waits for the contract to
        actually trade.
        """
        bars = self._history.contract_day(key, self.day)
        bar = bars.get(self.now)
        if bar is not None:
            return bar.close
        earlier = [ts for ts in bars if ts < self.now]
        if earlier:
            return bars[max(earlier)].close
        return self._history.prev_close(key, self.day)

    def lot_size(self, expiry: date) -> int:
        return self._history.lot_size(self.day, expiry)
