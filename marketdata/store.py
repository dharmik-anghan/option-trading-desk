"""Where bars are kept.

DuckDB rather than the SQLite the rest of the desk uses, and the reason is the
query: every chart is a range scan over one series, which is what a columnar store
is for, and a few years of one-minute bars is a few million rows. Its Parquet
support is also the way out if this data ever outgrows the app.

It costs about 44MB installed, which is worth saying plainly - more than the AWS
SDK the Fyers SDK brings in. SQLite with an index on the key would serve the same
queries adequately at these sizes. The engine is behind this module and nothing
above it imports duckdb, so changing that decision later is one file.

Single-writer: DuckDB allows one process to write a database file. That suits a
desk that is one process, and is why the backup script leaves this file alone.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from marketdata.models import Bar, Interval, Series

log = logging.getLogger(__name__)


def _to_db(at: datetime) -> datetime:
    """UTC, without a zone, for storage. See the note on the `ts` column."""
    if at.tzinfo is None:
        return at
    return at.astimezone(UTC).replace(tzinfo=None)


def _from_db(at: datetime) -> datetime:
    """Back to an aware UTC datetime, so callers never handle a naive one."""
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bar (
    source   VARCHAR     NOT NULL,
    symbol   VARCHAR     NOT NULL,
    interval VARCHAR     NOT NULL,
    -- Start of the bar, UTC, without a zone. Naive on purpose: DuckDB needs pytz
    -- to bind an aware datetime, and adding a dependency to carry a zone that is
    -- always UTC is a poor trade. Everything is converted at this module's edge,
    -- so nothing above it sees a naive time.
    ts       TIMESTAMP   NOT NULL,
    open     DOUBLE      NOT NULL,
    high     DOUBLE      NOT NULL,
    low      DOUBLE      NOT NULL,
    close    DOUBLE      NOT NULL,
    volume   DOUBLE      NOT NULL,
    PRIMARY KEY (source, symbol, interval, ts)
);

-- When each series was last asked for, and how it went. This is what stops a
-- rate-limited source being asked again immediately: Yahoo answers 429 after
-- about ten requests in two minutes, so a desk that refetched on every chart
-- render would spend most of its time refused.
CREATE TABLE IF NOT EXISTS series_fetch (
    source     VARCHAR     NOT NULL,
    symbol     VARCHAR     NOT NULL,
    interval   VARCHAR     NOT NULL,
    fetched_at TIMESTAMP   NOT NULL,
    ok         BOOLEAN     NOT NULL,
    note       VARCHAR     NOT NULL,
    PRIMARY KEY (source, symbol, interval)
);

-- Funding payments on a perpetual, as a rate per settlement.
--
-- Not bars, and not derivable from them: funding is what longs pay shorts to
-- keep the contract near spot, and over a six-month backtest it can exceed the
-- gross profit of a short-horizon rule outright. A result computed without it is
-- not a conservative result, it is a wrong one.
--
-- Keyed by source like everything else here, because the venue we trade
-- publishes no history and the rate stored against "binance" is a proxy for
-- Shark's - one that has to be labelled as such wherever it is used.
CREATE TABLE IF NOT EXISTS funding (
    source VARCHAR   NOT NULL,
    symbol VARCHAR   NOT NULL,
    ts     TIMESTAMP NOT NULL,
    rate   DOUBLE    NOT NULL,
    PRIMARY KEY (source, symbol, ts)
);
"""


class BarStore:
    """Bars on disk, and a note of when each series was last fetched.

    Guarded by a lock: DuckDB permits one writer, and the desk reaches this from
    request handlers and from a background task. The lock makes that one writer
    explicit rather than a race waiting for a busy afternoon.
    """

    def __init__(self, path: Path | str = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = duckdb.connect(self._path)
        self._conn.execute(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ writing

    def write(self, series: Series, bars: Sequence[Bar]) -> int:
        """Store bars, replacing any already held for the same minute.

        Replacing rather than ignoring, because the most recent bar of a live
        series is incomplete: fetched again an hour later it has a different high,
        low and close, and the later answer is the true one.
        """
        if not bars:
            return 0
        # Deduplicated here rather than left to the insert. Within a single
        # statement DuckDB keeps the first row for a key and discards the rest,
        # which is the opposite of what this method promises - and a page that
        # repeats a timestamp would also report writing more rows than it did.
        latest = {b.ts: b for b in bars}
        bars = list(latest.values())
        # Built as one statement with the numbers written into the SQL, because
        # DuckDB's parameter binding is the bottleneck here and not the insert:
        # 20,000 bars take 25 seconds through `executemany` and a quarter of a
        # second this way. At three hundred thousand bars for a backfill, that is
        # the difference between six seconds and six minutes.
        #
        # Only machine-generated numbers and timestamps are written into the text.
        # The three strings that identify the series stay bound parameters - they
        # are the only values a caller supplies, and inlining them would be putting
        # caller input into SQL for a speed-up worth nothing, since there are three
        # of them per call rather than three per bar.
        values = ",".join(
            # repr on a float, so the text round-trips to the same double.
            f"(TIMESTAMP '{_to_db(b.ts).isoformat(sep=' ')}',"
            f"{b.open!r},{b.high!r},{b.low!r},{b.close!r},{b.volume!r})"
            for b in bars
        )
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO bar "
                "SELECT ?, ?, ?, ts, open, high, low, close, volume "
                f"FROM (VALUES {values}) AS incoming(ts, open, high, low, close, volume)",
                [series.source, series.symbol, str(series.interval)],
            )
        return len(bars)

    def write_funding(
        self, source: str, symbol: str, rates: Sequence[tuple[datetime, float]]
    ) -> int:
        """Store funding rates, replacing any already held for the same moment."""
        if not rates:
            return 0
        latest = dict(rates)
        values = ",".join(
            f"(TIMESTAMP '{_to_db(at).isoformat(sep=' ')}',{rate!r})"
            for at, rate in latest.items()
        )
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO funding "
                f"SELECT ?, ?, ts, rate FROM (VALUES {values}) AS incoming(ts, rate)",
                [source, symbol],
            )
        return len(latest)

    def read_funding(
        self, source: str, symbol: str, *, start: datetime | None = None
    ) -> list[tuple[datetime, float]]:
        """Funding rates in time order."""
        sql = "SELECT ts, rate FROM funding WHERE source = ? AND symbol = ?"
        params: list[object] = [source, symbol]
        if start is not None:
            sql += " AND ts >= ?"
            params.append(_to_db(start))
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY ts", params).fetchall()
        return [(_from_db(row[0]), float(row[1])) for row in rows]

    def note_fetch(
        self, series: Series, *, ok: bool, note: str = "", at: datetime | None = None
    ) -> None:
        """Record that this series was asked for, and how it went.

        The time is passed in rather than read here. The service that decides when
        to fetch has its own clock, and a store reading a second one means the two
        disagree - which showed up as every fetch appearing to have happened in the
        future and the cooldown never expiring.
        """
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO series_fetch "
                "(source, symbol, interval, fetched_at, ok, note) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    series.source,
                    series.symbol,
                    str(series.interval),
                    _to_db(at if at is not None else datetime.now(UTC)),
                    ok,
                    note,
                ),
            )

    # ------------------------------------------------------------------ reading

    def read(
        self, series: Series, start: datetime | None = None, end: datetime | None = None
    ) -> list[Bar]:
        """Bars for a series, oldest first, within an optional window."""
        sql = (
            "SELECT ts, open, high, low, close, volume FROM bar "
            "WHERE source = ? AND symbol = ? AND interval = ?"
        )
        args: list[object] = [series.source, series.symbol, str(series.interval)]
        if start is not None:
            sql += " AND ts >= ?"
            args.append(_to_db(start))
        if end is not None:
            sql += " AND ts <= ?"
            args.append(_to_db(end))
        sql += " ORDER BY ts"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            Bar(ts=_from_db(r[0]), open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows
        ]

    def span(self, series: Series) -> tuple[datetime, datetime] | None:
        """The first and last bar held, or None when none are."""
        with self._lock:
            row = self._conn.execute(
                "SELECT min(ts), max(ts) FROM bar "
                "WHERE source = ? AND symbol = ? AND interval = ?",
                (series.source, series.symbol, str(series.interval)),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return _from_db(row[0]), _from_db(row[1])

    def latest(self, source: str, interval: Interval) -> dict[str, datetime]:
        """The last bar held for every symbol of one source and size, in one query."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, max(ts) FROM bar WHERE source = ? AND interval = ? "
                "GROUP BY symbol",
                (source, str(interval)),
            ).fetchall()
        return {str(r[0]): _from_db(r[1]) for r in rows}

    def fetched(self, source: str, interval: Interval) -> dict[str, datetime]:
        """When each symbol of one source and size was last fetched live, in one query."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, fetched_at FROM series_fetch "
                "WHERE source = ? AND interval = ? AND ok",
                (source, str(interval)),
            ).fetchall()
        return {str(r[0]): _from_db(r[1]) for r in rows}

    def count(self, series: Series) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT count(*) FROM bar WHERE source = ? AND symbol = ? AND interval = ?",
                (series.source, series.symbol, str(series.interval)),
            ).fetchone()
        return int(row[0]) if row else 0

    def last_fetch(self, series: Series) -> tuple[datetime, bool, str] | None:
        """When this series was last asked for, whether it worked, and what it said."""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched_at, ok, note FROM series_fetch "
                "WHERE source = ? AND symbol = ? AND interval = ?",
                (series.source, series.symbol, str(series.interval)),
            ).fetchone()
        if row is None:
            return None
        return _from_db(row[0]), bool(row[1]), str(row[2])

    def series_held(self) -> list[tuple[Series, int]]:
        """Every series in the store with how many bars it holds. For reporting."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, symbol, interval, count(*) FROM bar "
                "GROUP BY source, symbol, interval ORDER BY source, symbol, interval"
            ).fetchall()
        return [(Series(r[0], r[1], Interval(r[2])), int(r[3])) for r in rows]
