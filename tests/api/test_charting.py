"""Where a chart's bars come from, and what keeps them current.

The bug these describe: the options desk's chart was never refreshed at all. No
source was registered under "fyers", so every request fell through to whatever the
offline backfill had written - and the backfill needs the desk stopped to take the
store's write lock, so during a session the chart could only sit still. It looked
exactly like a quiet market, because nothing said otherwise.

Two halves to the fix, both here: the size a source is asked for is the size it
actually serves rather than the one on screen, and a source that could not be read
says so where a chart can draw it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from api.charting import TAIL_BARS, days_for, fetched_size, series_for
from marketdata import Interval
from marketdata.models import Bar, Series
from marketdata.service import BarsResult

NOW = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)


def _bars(n: int, size: Interval) -> list[Bar]:
    return [
        Bar(
            ts=NOW - timedelta(seconds=size.seconds * (n - i)),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1.0,
        )
        for i in range(n)
    ]


@dataclass
class FakeService:
    """The two calls `series_for` makes, recorded rather than performed."""

    #: What the store holds, by (source, symbol, interval).
    held: dict[tuple[str, str, Interval], list[Bar]] = field(default_factory=dict)
    #: What a refresh reports back.
    result: BarsResult | None = None
    refreshed: list[tuple[str, Interval, int]] = field(default_factory=list)
    read: list[tuple[str, str, Interval]] = field(default_factory=list)

    def bars(
        self, symbol: str, interval: Interval, days: int, *, source: str | None = None
    ) -> BarsResult:
        self.refreshed.append((symbol, interval, days))
        if self.result is not None:
            return self.result
        return BarsResult(series=Series(source or "none", symbol, interval), bars=[], fetched=True)

    def stored(
        self, source: str, symbol: str, interval: Interval, *, days: int = 365
    ) -> BarsResult:
        self.read.append((source, symbol, interval))
        bars = self.held.get((source, symbol, interval), [])
        return BarsResult(series=Series(source, symbol, interval), bars=bars, fetched=False)


class TestWhichSizeIsAskedFor:
    """A four-hour chart is only as fresh as the bars it is built from."""

    def test_a_size_the_source_serves_is_asked_for_itself(self) -> None:
        assert fetched_size("fyers", Interval.M15) == Interval.M15
        assert fetched_size("fyers", Interval.D1) == Interval.D1

    def test_a_size_it_does_not_serve_asks_for_the_one_underneath(self) -> None:
        # Fyers would answer a request for four hours. That is the trap rather than
        # the convenience: a fetched 4h series and 15m bars combined into four hours
        # can disagree about a boundary, and nothing on a chart would show it.
        assert fetched_size("fyers", Interval.H1) == Interval.M15
        assert fetched_size("fyers", Interval.H4) == Interval.M15
        assert fetched_size("fyers", Interval.W1) == Interval.D1

    def test_a_source_that_serves_every_size_is_asked_for_what_is_wanted(self) -> None:
        # A venue serving its own candles at every size - the perpetuals desk.
        assert fetched_size("shark", Interval.H4) == Interval.H4
        assert fetched_size("shark", Interval.W1) == Interval.W1


class TestRefreshing:
    def test_a_resampled_size_refreshes_the_series_it_is_built_from(self) -> None:
        svc = FakeService(
            held={("fyers", "NSE:NIFTY50-INDEX", Interval.M15): _bars(300, Interval.M15)}
        )

        bars, note = series_for(
            svc, "fyers", "NSE:NIFTY50-INDEX", Interval.H4, days_for(Interval.H4, 180)
        )

        assert svc.refreshed == [
            ("NSE:NIFTY50-INDEX", Interval.M15, days_for(Interval.M15, TAIL_BARS))
        ]
        assert bars, "and it is drawn from the fifteen-minute bars, combined up"
        assert note == "built from 15m bars"

    def test_only_the_tail_is_asked_for_however_wide_the_chart_is(self) -> None:
        # The store holds the depth a backfill put there; what a live chart is
        # missing is the last few bars. Asking for the window would be refused
        # anyway - Fyers serves a hundred days of intraday per request.
        svc = FakeService(
            held={("fyers", "NSE:NIFTY50-INDEX", Interval.D1): _bars(400, Interval.D1)}
        )
        window = days_for(Interval.W1, 180)

        series_for(svc, "fyers", "NSE:NIFTY50-INDEX", Interval.W1, window)

        _symbol, _size, days = svc.refreshed[0]
        assert days < window
        assert days == days_for(Interval.D1, TAIL_BARS)
        # and the window is still read in full off disk
        assert ("fyers", "NSE:NIFTY50-INDEX", Interval.W1) in svc.read

    def test_it_is_read_at_the_key_the_source_writes_under(self) -> None:
        """A source whose names differ from the desk's is read at its own name."""
        svc = FakeService(
            held={("yahoo", "GC=F", Interval.D1): _bars(30, Interval.D1)},
            result=BarsResult(series=Series("yahoo", "GC=F", Interval.D1), bars=[], fetched=True),
        )

        bars, _note = series_for(svc, "yahoo", "XAUUSDT", Interval.D1, 30)

        assert svc.read == [("yahoo", "GC=F", Interval.D1)]
        assert len(bars) == 30

    def test_refresh_can_be_declined(self) -> None:
        """A backtest reads what exists rather than provoking a fetch mid-run."""
        svc = FakeService(
            held={("fyers", "NSE:NIFTY50-INDEX", Interval.D1): _bars(30, Interval.D1)}
        )

        series_for(svc, "fyers", "NSE:NIFTY50-INDEX", Interval.D1, 30, refresh=False)

        assert svc.refreshed == []


class TestWhatTheChartIsTold:
    def test_a_source_that_could_not_be_read_is_reported(self) -> None:
        svc = FakeService(
            held={("fyers", "NSE:NIFTY50-INDEX", Interval.D1): _bars(30, Interval.D1)},
            result=BarsResult(
                series=Series("fyers", "NSE:NIFTY50-INDEX", Interval.D1),
                bars=[],
                fetched=False,
                note="fyers has no bars for NSE:NIFTY50-INDEX",
                ok=False,
            ),
        )

        bars, note = series_for(svc, "fyers", "NSE:NIFTY50-INDEX", Interval.D1, 30)

        assert len(bars) == 30, "what is stored is still drawn"
        assert note == "fyers has no bars for NSE:NIFTY50-INDEX"

    def test_the_cache_working_is_not_reported(self) -> None:
        # "Held off" is the cooldown doing its job. A chart that says so every two
        # minutes is a chart whose notes nobody reads.
        svc = FakeService(
            held={("fyers", "NSE:NIFTY50-INDEX", Interval.D1): _bars(30, Interval.D1)},
            result=BarsResult(
                series=Series("fyers", "NSE:NIFTY50-INDEX", Interval.D1),
                bars=[],
                fetched=False,
                note="held off",
            ),
        )

        _bars_out, note = series_for(svc, "fyers", "NSE:NIFTY50-INDEX", Interval.D1, 30)

        assert note == ""

    def test_a_failure_and_a_resample_are_both_said(self) -> None:
        svc = FakeService(
            held={("fyers", "NSE:NIFTY50-INDEX", Interval.M15): _bars(300, Interval.M15)},
            result=BarsResult(
                series=Series("fyers", "NSE:NIFTY50-INDEX", Interval.M15),
                bars=[],
                fetched=False,
                note="Fyers is rate limiting us. Showing what is stored.",
                ok=False,
            ),
        )

        _bars_out, note = series_for(svc, "fyers", "NSE:NIFTY50-INDEX", Interval.H1, 40)

        assert "rate limiting" in note
        assert "built from 15m bars" in note

    def test_nothing_held_and_nothing_fetched_says_which(self) -> None:
        svc = FakeService()

        bars, note = series_for(svc, "fyers", "NSE:BANKNIFTY-INDEX", Interval.D1, 30)

        assert bars == []
        assert "Nothing stored" in note
