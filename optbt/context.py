"""What a day looked like before a decision: pivots, gap, VIX, days to expiry.

Everything here is known *before* the entry it describes. Pivots come from the
previous session's high, low and close. The gap is today's open against
yesterday's close. The VIX percentile ranks the VIX at the decision against
earlier days' closes only - including today's close would be ranking a number
against a day that has not finished.

Used twice, and it is the same definition both times: as a day filter ("only
trade when the VIX percentile is above 70") and as a tag on every trade, so a
result can be sliced by any of these afterwards.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, time

from optbt.data.source import VIX_SYMBOL
from optbt.market import SESSION_LAST_BAR, SESSION_OPEN, History

#: Where the open sits against the day's pivots. Ordered low to high.
ZONES = ("below S2", "S2-S1", "S1-P", "P-R1", "R1-R2", "above R2")


@dataclass(frozen=True)
class Pivots:
    """Classic floor pivots from one session's high, low and close."""

    p: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float

    @classmethod
    def of(cls, high: float, low: float, close: float) -> Pivots:
        p = (high + low + close) / 3
        return cls(
            p=p,
            r1=2 * p - low,
            r2=p + (high - low),
            r3=high + 2 * (p - low),
            s1=2 * p - high,
            s2=p - (high - low),
            s3=low - 2 * (high - p),
        )

    def zone(self, price: float) -> str:
        edges = (self.s2, self.s1, self.p, self.r1, self.r2)
        return ZONES[bisect.bisect_right(edges, price)]

    def level(self, name: str) -> float:
        return float(getattr(self, name.lower()))


@dataclass(frozen=True)
class Day:
    day: date
    open: float
    high: float
    low: float
    close: float
    prev_close: float | None
    #: From the previous session. None on the first day of the data.
    pivots: Pivots | None
    vix_close: float | None

    @property
    def gap_pct(self) -> float | None:
        if self.prev_close is None:
            return None
        return (self.open - self.prev_close) / self.prev_close * 100

    @property
    def open_zone(self) -> str | None:
        return self.pivots.zone(self.open) if self.pivots else None


class Context:
    """Daily context for one underlying, loaded once per run."""

    def __init__(self, history: History) -> None:
        self._history = history
        conn = history._conn
        session = "CAST(ts AS TIME) BETWEEN ? AND ?"
        rows = conn.execute(
            f"""
            SELECT CAST(ts AS DATE) AS d, arg_min(open, ts), max(high), min(low), arg_max(close, ts)
            FROM index_bar WHERE symbol = ? AND resolution = '1' AND {session}
            GROUP BY d ORDER BY d
            """,
            [history.index_symbol, SESSION_OPEN, SESSION_LAST_BAR],
        ).fetchall()
        vix = dict(
            conn.execute(
                f"""
                SELECT CAST(ts AS DATE) AS d, arg_max(close, ts)
                FROM index_bar WHERE symbol = ? AND resolution = '1' AND {session}
                GROUP BY d
                """,
                [VIX_SYMBOL, SESSION_OPEN, SESSION_LAST_BAR],
            ).fetchall()
        )
        self._days: dict[date, Day] = {}
        prev: tuple[float, float, float] | None = None
        for d, o, h, lo, c in rows:
            self._days[d] = Day(
                day=d,
                open=float(o),
                high=float(h),
                low=float(lo),
                close=float(c),
                prev_close=prev[2] if prev else None,
                pivots=Pivots.of(*prev) if prev else None,
                vix_close=float(vix[d]) if d in vix else None,
            )
            prev = (float(h), float(lo), float(c))
        self._order = sorted(self._days)
        self._vix_closes = [self._days[d].vix_close for d in self._order]

    def day(self, d: date) -> Day | None:
        return self._days.get(d)

    def vix_at(self, ts: datetime) -> float | None:
        """India VIX at the close of the bar named `ts`, or the last one before."""
        row = self._history._conn.execute(
            "SELECT close FROM index_bar WHERE symbol = ? AND resolution = '1' "
            "AND ts <= ? AND ts >= ? ORDER BY ts DESC LIMIT 1",
            [VIX_SYMBOL, ts, datetime.combine(ts.date(), time(0))],
        ).fetchone()
        return float(row[0]) if row else None

    def vix_percentile(self, d: date, value: float, lookback: int = 252) -> float | None:
        """Where `value` ranks among the `lookback` sessions' VIX closes before `d`.

        0 is below every one of them, 100 above every one. None until there are
        at least a quarter of `lookback` earlier sessions to rank against.
        """
        i = bisect.bisect_left(self._order, d)
        earlier = [v for v in self._vix_closes[max(0, i - lookback) : i] if v is not None]
        if len(earlier) < max(20, lookback // 4):
            return None
        earlier.sort()
        return bisect.bisect_left(earlier, value) / len(earlier) * 100
