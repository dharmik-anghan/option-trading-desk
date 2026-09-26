"""Bars, from the store when possible and the source when necessary.

The rule: never ask a source for what is already held, and never ask it twice in
quick succession. Yahoo answers 429 after about ten requests in two minutes, so a
desk that fetched on every chart render would spend most of its time refused - and
the bars it wanted were on disk the whole time.

What "necessary" means is deliberately crude: if the newest bar held is older than
one bar's width, the tail is refetched. Not a diff of every gap. A source that
serves a fixed window cannot fill an arbitrary hole anyway, and the complexity of
tracking holes buys nothing a refetch of the window does not.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from marketdata.binance import BinanceBars, binance_symbol
from marketdata.models import Bar, Interval, Series
from marketdata.store import BarStore
from marketdata.yahoo import Fetched, RateLimited, Unavailable, YahooBars, yahoo_symbol

log = logging.getLogger(__name__)


class BarSource(Protocol):
    """A place bars come from."""

    def fetch(self, symbol: str, interval: Interval, days: int) -> Fetched: ...

#: Least time between fetches of one series, whatever the caller asks. A chart being
#: redrawn is not a reason to ask again; a bar closing is, and the shortest bar the
#: desk offers is a minute.
MIN_BETWEEN_FETCHES = timedelta(seconds=55)

#: How long to stay away after a refusal. Longer than the ordinary cooldown, because
#: the thing to do about a rate limit is wait rather than ask more politely.
AFTER_REFUSAL = timedelta(minutes=10)


@dataclass(frozen=True)
class BarsResult:
    """Bars, and where they came from - which the desk should be able to say."""

    series: Series
    bars: list[Bar]
    #: True when this call went to the source. False means everything came off disk.
    fetched: bool
    #: Why a fetch did not happen or did not work, when that is worth reporting.
    note: str = ""
    #: The source's own name for the instrument, when it has told us one. Worth
    #: showing: Yahoo's gold is "Gold Dec 26", a dated contract, not the perpetual
    #: the desk trades.
    name: str = ""


class BarService:
    """A read-through cache over a bar source."""

    def __init__(
        self,
        store: BarStore,
        source: BarSource | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        binance: BarSource | None = None,
    ) -> None:
        self._store = store
        self._yahoo = source or YahooBars()
        self._binance = binance or BinanceBars()
        self._now = now

    def _route(self, symbol: str) -> tuple[str, str, BarSource] | None:
        """Which source serves this symbol, and what it calls it.

        Binance first where it has the pair: it is a documented API with a
        published budget where Yahoo is the endpoint behind a web page and refuses
        after about ten requests. Yahoo stays because it has gold and oil, which
        Binance does not - no free source covers everything this desk trades.

        Returning None rather than guessing. A symbol no source lists should draw
        no chart, not a chart of something else.
        """
        mapped = binance_symbol(symbol)
        if mapped is not None:
            return "binance", mapped, self._binance
        mapped = yahoo_symbol(symbol)
        if mapped is not None:
            return "yahoo", mapped, self._yahoo
        return None

    def _due(self, series: Series, interval: Interval) -> tuple[bool, str]:
        """Whether to ask the source, and why not when the answer is no."""
        last = self._store.last_fetch(series)
        now = self._now()
        if last is not None:
            at, ok, note = last
            wait = MIN_BETWEEN_FETCHES if ok else AFTER_REFUSAL
            if now - at < wait:
                held = "held off" if ok else f"held off after: {note}"
                return False, held

        span = self._store.span(series)
        if span is None:
            return True, ""
        _first, newest = span
        # One bar's width of staleness is the trigger. Anything tighter asks for a
        # bar that has not closed yet.
        if now - newest < timedelta(seconds=interval.seconds):
            return False, "up to date"
        return True, ""

    def bars(
        self, symbol: str, interval: Interval, days: int, *, refresh: bool = True
    ) -> BarsResult:
        """Bars for a desk symbol, filling from the source when due.

        Returns what is held even when the source refuses, because a chart drawn
        from yesterday's bars with a note saying so is more use than an error.
        """
        route = self._route(symbol)
        if route is None:
            return BarsResult(
                series=Series("none", symbol, interval),
                bars=[],
                fetched=False,
                note=f"No source is mapped to {symbol}",
            )
        source_name, mapped, source = route
        series = Series(source_name, mapped, interval)

        note = ""
        name = ""
        fetched = False
        due, why = self._due(series, interval) if refresh else (False, "not asked")
        if due:
            try:
                got = source.fetch(mapped, interval, days)
                self._store.write(series, got.bars)
                self._store.note_fetch(
                    series, ok=True, note=f"{len(got.bars)} bars", at=self._now()
                )
                fetched = True
                name = got.name
            except RateLimited as exc:
                # Recorded as a refusal so the cooldown applies, and reported rather
                # than raised: the store still has yesterday.
                self._store.note_fetch(series, ok=False, note=str(exc), at=self._now())
                note = f"{exc}. Showing what is stored."
                log.info("%s rate limited for %s %s", source_name, mapped, interval)
            except Unavailable as exc:
                self._store.note_fetch(series, ok=False, note=str(exc), at=self._now())
                note = f"{exc}. Showing what is stored."
                log.warning("%s unavailable for %s %s: %s", source_name, mapped, interval, exc)
        else:
            note = why

        start = self._now() - timedelta(days=days)
        return BarsResult(
            series=series,
            bars=self._store.read(series, start=start),
            fetched=fetched,
            note=note,
            name=name,
        )

    def held(self) -> list[tuple[Series, int]]:
        return self._store.series_held()
