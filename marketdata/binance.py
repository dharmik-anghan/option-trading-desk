"""Bars from Binance's public market data.

Where Yahoo is the endpoint behind somebody's web page, this is a documented API
with a published rate budget - 1,200 weight a minute against Yahoo's roughly ten
requests before a flat refusal - and a thousand bars per request going back years.
For crypto it is simply the better source.

It does not have gold or oil, which is why Yahoo stays: no single free source
covers what this desk trades, and the store is keyed on source so the two can sit
side by side without pretending to be the same series.

No key, no handshake. The only thing worth knowing is that it answers 451 from
some countries, which is a refusal to serve rather than a fault.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import requests

from marketdata.models import Bar, Interval
from marketdata.yahoo import Fetched, RateLimited, Unavailable

log = logging.getLogger(__name__)

KLINES_URL = "https://api.binance.com/api/v3/klines"
TIMEOUT = 25.0

#: Bars per request. The documented maximum is 1000, and asking for exactly that
#: keeps the number of requests down - which is the only thing a rate budget cares
#: about.
MAX_BARS = 1000

#: Binance spells intervals the same way the desk does, except for the ones it does
#: not have. It does have a real 4h, unlike Yahoo.
BINANCE_INTERVAL: dict[Interval, str] = {
    Interval.M1: "1m",
    Interval.M5: "5m",
    Interval.M15: "15m",
    Interval.M30: "30m",
    Interval.H1: "1h",
    Interval.H4: "4h",
    Interval.D1: "1d",
    Interval.W1: "1w",
}

#: What the desk calls these against what Binance calls them. Identical here, since
#: the desk's perpetual symbols came from a venue with a Binance-shaped API - but
#: mapped explicitly rather than assumed, because the day they diverge should be a
#: missing entry and not a wrong chart.
#:
#: Only the crypto pairs. Binance quotes no gold or oil, so XAUUSDT and CLUSDT are
#: absent here on purpose and reach Yahoo instead.
FOR_SYMBOL: dict[str, str] = {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT"}


def binance_symbol(symbol: str) -> str | None:
    return FOR_SYMBOL.get(symbol)


def parse_klines(rows: list[list[Any]]) -> list[Bar]:
    """Binance's klines, which are arrays rather than objects.

    Position is the whole schema: open time, open, high, low, close, volume, close
    time, and six more nobody here reads. A row shorter than six fields is not a
    candle and is skipped rather than guessed at.
    """
    bars: list[Bar] = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            bars.append(
                Bar(
                    ts=datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        except (TypeError, ValueError):
            # One unreadable row costs one bar, not the series.
            continue
    bars.sort(key=lambda b: b.ts)
    return bars


class BinanceBars:
    """Fetches bars. Knows nothing about the store."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def fetch_from(self, symbol: str, interval: Interval, start: datetime) -> list[Bar]:
        """One page of bars beginning at `start`, oldest first.

        The piece `fetch` cannot do. `fetch` asks for the most recent N bars, which
        is all a chart needs and useless for history: the endpoint caps at a
        thousand, so three years of five-minute bars is three hundred pages and
        there is no way to ask for the second one without naming a time.

        Returns fewer than `MAX_BARS` at the end of the series, and an empty list
        past it - which is how the caller knows to stop.
        """
        rows = self._get(
            {
                "symbol": symbol,
                "interval": BINANCE_INTERVAL[interval],
                "startTime": str(int(start.timestamp() * 1000)),
                "limit": str(MAX_BARS),
            }
        )
        return parse_klines(rows)

    def fetch(self, symbol: str, interval: Interval, days: int) -> Fetched:
        """The most recent bars for one pair.

        `days` decides how many bars to ask for rather than a date window, because
        the endpoint counts bars and converting is arithmetic the caller should not
        have to do. Capped at the documented maximum.
        """
        wanted = max(1, min(MAX_BARS, int(days * 86400 / interval.seconds) + 1))
        rows = self._get(
            {
                "symbol": symbol,
                "interval": BINANCE_INTERVAL[interval],
                "limit": str(wanted),
            }
        )
        return Fetched(bars=parse_klines(rows), name=symbol, currency="USDT")

    def _get(self, params: dict[str, str]) -> list[Any]:
        """One call to the klines endpoint, with its refusals named.

        Shared by both callers so that a backfill running for several minutes
        reports a rate limit the same way a chart does, rather than as a crash.
        """
        try:
            response = self._session.get(KLINES_URL, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise Unavailable(f"cannot reach Binance ({type(exc).__name__})") from exc

        if response.status_code in {418, 429}:
            # 418 is its "you kept going after a 429" status.
            raise RateLimited("Binance is rate limiting us")
        if response.status_code == 451:
            raise Unavailable("Binance will not serve this country")
        if response.status_code >= 400:
            raise Unavailable(f"Binance answered {response.status_code}")
        try:
            rows = response.json()
        except ValueError as exc:
            raise Unavailable("Binance sent something that is not JSON") from exc
        if not isinstance(rows, list):
            raise Unavailable("Binance sent no candles")
        return rows
