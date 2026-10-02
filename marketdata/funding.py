"""Funding rates for a perpetual.

A perpetual has no expiry, so nothing mechanically pulls it towards spot. Funding
is what does: every few hours one side pays the other in proportion to how far the
contract sits from the index. For a position held for minutes it is nothing; for
one held for days it is often the difference between a rule that makes money and
one that does not.

The venue this desk trades publishes no funding history and has no endpoint for
it - their fee page says only that it settles "every 4 or 8 hours depending on the
contract". Binance publishes theirs, free and going back years, and funding is an
arbitrage-driven number, so the two track closely. That makes it a usable stand-in
and nothing more, which is why the source is stored alongside the rate and why
anything computed from it should say whose funding it used.

Two things would make a backtest read low against reality: Shark settling every
four hours where this assumes Binance's eight, and Shark's own rate running above
the market. Both are measurable the moment a position is held across a settlement,
by reading what was actually charged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import requests

from marketdata.yahoo import RateLimited, Unavailable

log = logging.getLogger(__name__)

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
TIMEOUT = 25.0

#: Settlements per request. The documented maximum.
MAX_ROWS = 1000

#: What the desk calls these against what Binance's futures API calls them.
#: Only the perpetuals it lists: there is no Binance funding for gold or oil, and
#: inventing one would be worse than having none.
FOR_SYMBOL: dict[str, str] = {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT"}


def funding_symbol(symbol: str) -> str | None:
    return FOR_SYMBOL.get(symbol)


class BinanceFunding:
    """Funding history, a page at a time."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def fetch_from(self, symbol: str, start: datetime) -> list[tuple[datetime, float]]:
        """Settlements at or after `start`, oldest first.

        Returns fewer than `MAX_ROWS` at the end of the history and an empty list
        past it, which is how a caller paging through knows to stop.
        """
        params = {
            "symbol": symbol,
            "startTime": str(int(start.timestamp() * 1000)),
            "limit": str(MAX_ROWS),
        }
        try:
            response = self._session.get(FUNDING_URL, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise Unavailable(f"cannot reach Binance ({type(exc).__name__})") from exc

        if response.status_code in {418, 429}:
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
            raise Unavailable("Binance sent no funding rates")
        return parse_funding(rows)


def parse_funding(rows: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    """Settlement times and rates, skipping anything unreadable.

    A rate is a fraction, not a percentage: 0.0001 is one basis point charged on
    the position's notional at that moment.
    """
    out: list[tuple[datetime, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                (
                    datetime.fromtimestamp(int(row["fundingTime"]) / 1000, tz=UTC),
                    float(row["fundingRate"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    out.sort()
    return out
