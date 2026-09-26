"""Bars from Yahoo Finance.

Shark serves a short history - a few hundred bars - which is enough to draw a chart
and not enough to look at a year. Yahoo has the depth, needs no key, and is not
really an API: it is the endpoint its own charts use, undocumented and free to
change. That shapes everything here.

It rate-limits hard. Ten requests in two minutes earned a plain `429 Too Many
Requests`, which is why this module is paired with a store and why a refusal is a
normal outcome rather than an exception to log: the desk is expected to serve what
it already has and try again later.

Its own limits on history, which is the other reason for the store - a bar older
than these windows cannot be fetched again once it falls out:

    1m    7 days
    5m    60 days
    15m   60 days
    1h    730 days
    1d    decades

The instruments are not the same as the venue's, and that is not a detail to gloss
over. Yahoo's gold is a dated futures contract (GC=F, "Gold Dec 26") and Shark's is
a perpetual; they were 4,321 and 4,285 at the same moment. The store keys on source
for that reason, and a chart drawn from one should say which.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

import requests

from marketdata.models import Bar, Interval

log = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
TIMEOUT = 25.0

#: Where a cookie comes from. This request is expected to fail - it answers 404 -
#: and it is made for the `Set-Cookie` header rather than the body.
COOKIE_URL = "https://fc.yahoo.com/"
#: Exchanges that cookie for a token Yahoo wants alongside it.
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"

#: Yahoo serves its own charts to browsers and refuses an obvious script.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

#: How far back each bar size can be asked for. Beyond these Yahoo answers with an
#: error or silently truncates, so the request is clamped rather than sent hopefully.
MAX_RANGE_DAYS: dict[Interval, int] = {
    Interval.M1: 7,
    Interval.M5: 60,
    Interval.M15: 60,
    Interval.M30: 60,
    Interval.H1: 730,
    Interval.H4: 730,
    Interval.D1: 10_000,
}

#: What Yahoo calls each interval. It has no four-hour bar, so that one is asked for
#: hourly and left to the caller to combine - better than silently serving a
#: different bar size than the one requested.
YAHOO_INTERVAL: dict[Interval, str] = {
    Interval.M1: "1m",
    Interval.M5: "5m",
    Interval.M15: "15m",
    Interval.M30: "30m",
    Interval.H1: "1h",
    Interval.H4: "1h",
    Interval.D1: "1d",
}

#: The three the desk cares about, and what Yahoo calls them. Held here rather than
#: guessed: "XAUUSD=X" looks right and is delisted, while GC=F and CL=F are the
#: contracts that actually serve data.
FOR_SYMBOL: dict[str, str] = {
    "BTCUSDT": "BTC-USD",
    "XAUUSDT": "GC=F",
    "CLUSDT": "CL=F",
}


class RateLimited(Exception):
    """Yahoo refused for asking too often. Expected, not exceptional."""


class Unavailable(Exception):
    """Yahoo could not answer, or answered with something unreadable."""


@dataclass(frozen=True)
class Fetched:
    """What one request returned, and what the source said about it."""

    bars: list[Bar]
    #: Yahoo's own name for the instrument, for showing beside a chart drawn from
    #: it - "Gold Dec 26" is worth seeing when the desk trades a perpetual.
    name: str
    currency: str


def yahoo_symbol(symbol: str) -> str | None:
    """Yahoo's name for a desk symbol, or None if there is no mapping."""
    return FOR_SYMBOL.get(symbol)


def parse_chart(payload: dict) -> Fetched:  # type: ignore[type-arg]
    """Yahoo's chart response, turned into bars.

    The shape is arrays in parallel - timestamps in one, opens in another - and any
    of them can carry a null where the market was closed or a bar is missing. Those
    rows are dropped rather than interpolated: a chart with a gap is honest, and a
    filled gap is a candle that never traded.
    """
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise Unavailable(str(error.get("description") or error))
    results = chart.get("result") or []
    if not results:
        raise Unavailable("no result in the response")

    result = results[0]
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    bars: list[Bar] = []
    for i, stamp in enumerate(stamps):
        values = [
            opens[i] if i < len(opens) else None,
            highs[i] if i < len(highs) else None,
            lows[i] if i < len(lows) else None,
            closes[i] if i < len(closes) else None,
        ]
        if any(v is None for v in values) or stamp is None:
            continue
        volume = volumes[i] if i < len(volumes) else None
        bars.append(
            Bar(
                ts=datetime.fromtimestamp(int(stamp), tz=UTC),
                open=float(values[0]),  # type: ignore[arg-type]
                high=float(values[1]),  # type: ignore[arg-type]
                low=float(values[2]),  # type: ignore[arg-type]
                close=float(values[3]),  # type: ignore[arg-type]
                volume=float(volume) if volume is not None else 0.0,
            )
        )
    bars.sort(key=lambda b: b.ts)
    return Fetched(
        bars=bars,
        name=str(meta.get("shortName") or meta.get("symbol") or ""),
        currency=str(meta.get("currency") or ""),
    )


class YahooBars:
    """Fetches bars. Knows nothing about the store.

    Performs the handshake Yahoo's own pages perform before asking for data: a
    request that sets a cookie, then one that exchanges it for a token. Calling the
    chart endpoint without them works for a handful of requests and is then refused
    with a flat 429, which is what happened here - ten requests in two minutes and
    the address was cut off for the best part of an hour.

    The handshake is done once and reused. It is also allowed to fail: the chart
    endpoint does not strictly require the token, so a failed handshake means "ask
    anyway and hope", not "give up".
    """

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._crumb: str | None = None
        self._handshaken = False

    def _handshake(self) -> None:
        """Get a cookie and a crumb, once. Never raises.

        Never raises because this is preparation rather than the request. If Yahoo
        will not hand over a token, the chart endpoint may still answer, and
        refusing to try would turn a degraded source into a dead one.
        """
        if self._handshaken:
            return
        self._handshaken = True
        try:
            # 404 expected. The cookie is in the response headers either way.
            self._session.get(COOKIE_URL, headers=HEADERS, timeout=TIMEOUT)
            answer = self._session.get(CRUMB_URL, headers=HEADERS, timeout=TIMEOUT)
            if answer.status_code == 200 and answer.text and len(answer.text) < 64:
                self._crumb = answer.text.strip()
                log.info("yahoo handshake complete")
            else:
                log.info("yahoo gave no crumb (%s); continuing without one",
                         answer.status_code)
        except requests.RequestException as exc:
            log.info("yahoo handshake failed (%s); continuing without one",
                     type(exc).__name__)

    def reset(self) -> None:
        """Forget the handshake, so the next fetch performs it again.

        Used after a refusal that might be a stale cookie rather than a rate limit -
        the two are indistinguishable from the response, so the cheap thing is to
        renew and try once more.
        """
        self._crumb = None
        self._handshaken = False
        self._session.cookies.clear()

    def fetch(self, symbol: str, interval: Interval, days: int) -> Fetched:
        """Bars for one instrument, over at most `days`.

        `days` is clamped to what Yahoo will serve for that bar size, because
        asking for two years of one-minute candles is not a bigger answer - it is
        an error or a silent truncation, and both are worse than asking for what
        exists.
        """
        self._handshake()
        wanted = min(max(days, 1), MAX_RANGE_DAYS[interval])
        params = {"interval": YAHOO_INTERVAL[interval], "range": f"{wanted}d"}
        if self._crumb:
            params["crumb"] = self._crumb
        # Quoted because the symbols carry characters a path will not take as-is:
        # gold is "GC=F" and crude is "CL=F".
        url = f"{CHART_URL}/{quote(symbol, safe='')}"

        response = self._get(url, params)
        if response.status_code in {401, 403}:
            # Could be a stale cookie rather than a closed door. One renewal, then
            # believe it.
            self.reset()
            self._handshake()
            if self._crumb:
                params["crumb"] = self._crumb
            response = self._get(url, params)

        if response.status_code == 429:
            raise RateLimited("Yahoo is rate limiting us")
        if response.status_code >= 400:
            raise Unavailable(f"Yahoo answered {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            # A 429 has been seen as a bare text body rather than JSON, so this is
            # not always a broken feed.
            raise Unavailable("Yahoo sent something that is not JSON") from exc
        return parse_chart(payload)

    def _get(self, url: str, params: dict[str, str]) -> requests.Response:
        try:
            return self._session.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise Unavailable(f"cannot reach Yahoo ({type(exc).__name__})") from exc
