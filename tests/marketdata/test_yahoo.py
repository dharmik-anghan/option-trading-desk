"""Reading Yahoo's chart response, without asking Yahoo anything.

The parsing tests matter more than usual here: this is not an API but the endpoint
Yahoo's own charts use, so its shape is undocumented and free to change, and the
first sign of that will be a chart that looks wrong rather than an error.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

import pytest
import requests

from marketdata.errors import RateLimited, Unavailable
from marketdata.models import Interval
from marketdata.yahoo import (
    FOR_SYMBOL,
    MAX_RANGE_DAYS,
    YahooBars,
    parse_chart,
    yahoo_symbol,
)


def chart(
    stamps: list[int | None],
    opens: list[float | None],
    highs: list[float | None],
    lows: list[float | None],
    closes: list[float | None],
    volumes: list[float | None] | None = None,
) -> dict[str, Any]:
    return {
        "chart": {
            "result": [
                {
                    "meta": {"symbol": "BTC-USD", "shortName": "Bitcoin USD", "currency": "USD"},
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": opens,
                                "high": highs,
                                "low": lows,
                                "close": closes,
                                "volume": volumes if volumes is not None else [1.0] * len(stamps),
                            }
                        ]
                    },
                }
            ]
        }
    }


class FakeResponse:
    def __init__(self, status: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeCookies:
    def clear(self) -> None:
        pass


class FakeSession:
    """Answers the handshake plausibly and the chart with whatever the test wants.

    The handshake is separated out because it happens before every fetch, and a
    test asserting on "the first call" would otherwise be asserting about a cookie.
    """

    def __init__(self, response: FakeResponse, crumb: str = "test-crumb") -> None:
        self._response = response
        self._crumb = crumb
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.cookies = FakeCookies()

    def get(self, url: str, params: Any = None, headers: Any = None, timeout: float = 0) -> Any:
        self.calls.append((url, params or {}))
        if url.startswith("https://fc.yahoo.com"):
            return FakeResponse(404, text="not found")
        if "getcrumb" in url:
            return FakeResponse(200, text=self._crumb)
        return self._response

    @property
    def chart_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for c in self.calls if "/v8/finance/chart" in c[0]]


class TestParsing:
    def test_a_normal_response_becomes_bars(self) -> None:
        payload = chart([1_790_000_000, 1_790_003_600], [1.0, 2.0], [3.0, 4.0], [0.5, 1.5],
                        [2.0, 3.0])
        got = parse_chart(payload)
        assert len(got.bars) == 2
        assert got.name == "Bitcoin USD"
        assert got.currency == "USD"
        assert got.bars[0].close == 2.0

    def test_times_are_utc_aware(self) -> None:
        got = parse_chart(chart([1_790_000_000], [1.0], [2.0], [0.5], [1.5]))
        assert got.bars[0].ts.tzinfo is not None
        assert got.bars[0].ts.utcoffset() == UTC.utcoffset(None)

    def test_bars_come_back_in_order(self) -> None:
        got = parse_chart(chart([1_790_003_600, 1_790_000_000], [1.0, 2.0], [3.0, 4.0],
                                [0.5, 1.5], [2.0, 3.0]))
        assert [b.ts for b in got.bars] == sorted(b.ts for b in got.bars)

    def test_a_null_in_any_field_drops_that_bar(self) -> None:
        # Yahoo carries nulls where a market was closed or a bar is missing. Dropped
        # rather than interpolated: a gap is honest and a filled gap is a candle
        # that never traded.
        payload = chart([1, 2, 3], [1.0, None, 3.0], [1.0, 1.0, 3.0], [1.0, 1.0, 3.0],
                        [1.0, 1.0, None])
        assert len(parse_chart(payload).bars) == 1

    def test_a_missing_volume_is_zero_not_a_dropped_bar(self) -> None:
        # Volume is the one field worth keeping a bar without: index bars often
        # have none, and the candle is still true.
        payload = chart([1], [1.0], [2.0], [0.5], [1.5], volumes=[None])
        (bar,) = parse_chart(payload).bars
        assert bar.volume == 0.0

    def test_an_error_response_is_reported_with_its_reason(self) -> None:
        payload = {"chart": {"error": {"description": "No data found, symbol may be delisted"}}}
        with pytest.raises(Unavailable, match="delisted"):
            parse_chart(payload)

    def test_an_empty_result_is_unavailable(self) -> None:
        with pytest.raises(Unavailable):
            parse_chart({"chart": {"result": []}})

    def test_no_bars_is_not_an_error(self) -> None:
        # A market that has not opened yet answers with empty arrays, which is a
        # true answer rather than a failure.
        assert parse_chart(chart([], [], [], [], [])).bars == []


class TestFetching:
    def _bars(self, session: FakeSession) -> YahooBars:
        return YahooBars(session=session)

    def test_a_rate_limit_is_its_own_outcome(self) -> None:
        # Expected, not exceptional: ten requests in two minutes earned a 429, so
        # the desk serves what it has and tries later.
        session = FakeSession(FakeResponse(429, text="Too Many Requests"))
        with pytest.raises(RateLimited):
            self._bars(session).fetch("BTC-USD", Interval.H1, 5)

    def test_a_429_that_is_not_json_is_still_a_rate_limit(self) -> None:
        # Seen in the wild: the body was the bare text "Too Many Requests".
        session = FakeSession(FakeResponse(429))
        with pytest.raises(RateLimited):
            self._bars(session).fetch("BTC-USD", Interval.H1, 5)

    def test_a_range_is_clamped_to_what_the_interval_allows(self) -> None:
        # Two years of one-minute candles is not a bigger answer; it is an error or
        # a silent truncation.
        session = FakeSession(FakeResponse(200, chart([], [], [], [], [])))
        self._bars(session).fetch("BTC-USD", Interval.M1, 3650)
        assert session.chart_calls[0][1]["range"] == f"{MAX_RANGE_DAYS[Interval.M1]}d"

    def test_a_symbol_with_an_equals_sign_survives_the_url(self) -> None:
        # Gold is GC=F and crude is CL=F.
        session = FakeSession(FakeResponse(200, chart([], [], [], [], [])))
        self._bars(session).fetch("GC=F", Interval.D1, 30)
        assert session.chart_calls[0][0].endswith("GC%3DF")

    def test_four_hour_bars_are_asked_for_hourly(self) -> None:
        # Yahoo has no four-hour bar. Asking hourly and combining is better than
        # silently serving a different size than the one requested.
        session = FakeSession(FakeResponse(200, chart([], [], [], [], [])))
        self._bars(session).fetch("BTC-USD", Interval.H4, 30)
        assert session.chart_calls[0][1]["interval"] == "1h"


class TestSymbolMapping:
    def test_the_desk_symbols_map_to_instruments_that_exist(self) -> None:
        # "XAUUSD=X" looks right and is delisted; GC=F serves data.
        assert yahoo_symbol("XAUUSDT") == "GC=F"
        assert yahoo_symbol("CLUSDT") == "CL=F"
        assert yahoo_symbol("BTCUSDT") == "BTC-USD"

    def test_an_unmapped_symbol_is_none_rather_than_a_guess(self) -> None:
        assert yahoo_symbol("NSE:NIFTY50-INDEX") is None

    def test_every_mapping_is_for_a_symbol_the_desk_lists(self) -> None:
        from venues.instruments import instrument

        for symbol in FOR_SYMBOL:
            assert instrument(symbol) is not None, f"{symbol} is not on any desk"


class TestTheHandshake:
    """Yahoo's own pages fetch a cookie and a token before asking for data.

    Skipping it works for a handful of requests and is then refused with a flat
    429 - ten requests in two minutes cut this address off for the best part of an
    hour, which is what prompted all of this.
    """

    def _session(self, **kw: Any) -> FakeSession:
        return FakeSession(FakeResponse(200, chart([], [], [], [], [])), **kw)

    def test_a_cookie_and_a_crumb_are_fetched_first(self) -> None:
        session = self._session()
        YahooBars(session=session).fetch("BTC-USD", Interval.D1, 30)
        urls = [c[0] for c in session.calls]
        assert urls[0].startswith("https://fc.yahoo.com")
        assert "getcrumb" in urls[1]
        assert "/v8/finance/chart" in urls[2]

    def test_the_crumb_is_sent_with_the_request(self) -> None:
        session = self._session()
        YahooBars(session=session).fetch("BTC-USD", Interval.D1, 30)
        assert session.chart_calls[0][1]["crumb"] == "test-crumb"

    def test_it_happens_once_not_per_fetch(self) -> None:
        session = self._session()
        bars = YahooBars(session=session)
        bars.fetch("BTC-USD", Interval.D1, 30)
        bars.fetch("GC=F", Interval.D1, 30)
        assert len([c for c in session.calls if "getcrumb" in c[0]]) == 1

    def test_no_crumb_does_not_stop_the_request(self) -> None:
        # The chart endpoint does not strictly require one, so a failed handshake
        # means "ask anyway" rather than "give up" - refusing to try would turn a
        # degraded source into a dead one.
        session = self._session(crumb="")
        YahooBars(session=session).fetch("BTC-USD", Interval.D1, 30)
        assert session.chart_calls
        assert "crumb" not in session.chart_calls[0][1]

    def test_a_401_renews_the_handshake_and_tries_once_more(self) -> None:
        # A stale cookie and a closed door look the same from the response, so the
        # cheap thing is to renew and try again - once.
        class Renewing(FakeSession):
            def __init__(self) -> None:
                super().__init__(FakeResponse(200, chart([], [], [], [], [])))
                self.chart_attempts = 0

            def get(self, url: str, params: Any = None, headers: Any = None,
                    timeout: float = 0) -> Any:
                if "/v8/finance/chart" in url:
                    self.chart_attempts += 1
                    self.calls.append((url, params or {}))
                    if self.chart_attempts == 1:
                        return FakeResponse(401, text="unauthorized")
                    return FakeResponse(200, chart([], [], [], [], []))
                return super().get(url, params, headers, timeout)

        session = Renewing()
        YahooBars(session=session).fetch("BTC-USD", Interval.D1, 30)
        assert session.chart_attempts == 2
        assert len([c for c in session.calls if "getcrumb" in c[0]]) == 2

    def test_a_handshake_that_cannot_connect_is_survivable(self) -> None:
        class Offline(FakeSession):
            def get(self, url: str, params: Any = None, headers: Any = None,
                    timeout: float = 0) -> Any:
                if "yahoo.com" in url and "/v8/" not in url:
                    raise requests.ConnectionError("no route")
                return super().get(url, params, headers, timeout)

        session = Offline(FakeResponse(200, chart([], [], [], [], [])))
        YahooBars(session=session).fetch("BTC-USD", Interval.D1, 30)
        assert session.chart_calls, "a failed handshake must not stop the fetch"
