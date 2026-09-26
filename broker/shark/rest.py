"""The Shark Exchange adapter: perpetual futures, crypto and commodities.

Implements `MarketData` and `Trading` from `broker/base.py`, and deliberately not
`Funds_`: the documented wallet endpoint (`GET /v1/order/futures-wallet-details`)
answers 404 on the live API, so this adapter cannot say what the account holds.
Declaring the capability and raising would have hidden that from the pre-trade
check, which is what the split protocols exist to prevent.

Rate limits are tighter than the options broker's: 60 requests a minute on most
endpoints against Fyers' ~200, and 20 a second on order placement. Every read
here is a single request, and anything polling this should go through a cache the
way `broker/cache.py` fronts Fyers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, time
from typing import Any

import requests

from broker.errors import BrokerError, BrokerUnreachable, classify_status
from broker.models import Candle, OrderRequest, OrderResult, Position, Quote
from broker.shark.parse import parse_klines, parse_positions, parse_ticker
from broker.shark.signing import headers, signed_body, signed_query

log = logging.getLogger(__name__)

BASE_URL = "https://api.sharkexchange.in"
TIMEOUT = 20.0

#: Candle sizes the venue accepts, mapped from the resolutions the desk asks for.
#: The options broker speaks in minutes ("1", "60", "D"); this one in Binance
#: intervals. Kept as a table so a caller does not need to know which venue it is
#: talking to.
_INTERVALS = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "240": "4h",
    "D": "1d",
    "1D": "1d",
}


class SharkBroker:
    """One account on Shark Exchange.

    Constructed per request like the Fyers adapter, so a rotated key is picked up
    without a restart. The session is shared for connection reuse.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
    ) -> None:
        self._key = api_key
        self._secret = api_secret
        self._base = base_url.rstrip("/")
        self._session = session or requests.Session()

    # ---------------------------------------------------------------- plumbing

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        url = f"{self._base}{path}"
        sent_headers: dict[str, str] = {}
        data: str | None = None

        if body is not None:
            if signed:
                data, signature = signed_body(self._secret, body)
                sent_headers = headers(self._key, signature, json_body=True)
            else:
                data = json.dumps(body)
                sent_headers = {"Content-Type": "application/json"}
        if signed and body is None:
            query, signature = signed_query(self._secret, params)
            url = f"{url}?{query}"
            sent_headers = headers(self._key, signature)
            params = None

        try:
            response = self._session.request(
                method, url, params=params, data=data, headers=sent_headers, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            # Never include the URL: a signed GET carries the signature in it.
            raise BrokerUnreachable(f"cannot reach Shark ({type(exc).__name__})") from exc

        if response.status_code >= 400:
            raise self._error(response)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise BrokerError(f"Shark returned a non-JSON body: {response.text[:120]}") from exc

    def _error(self, response: requests.Response) -> BrokerError:
        """Translate a failure into something the desk can act on."""
        message = response.text[:200]
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(body.get("message") or body.get("error") or message)
        except ValueError:
            pass
        return classify_status(response.status_code, message)(f"Shark: {message}")

    # ------------------------------------------------------------- market data

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        """Latest ticker per symbol.

        One request each: the venue has no batch ticker, so this is where the
        per-minute budget goes. Poll it through a cache.

        A symbol that fails is left out rather than failing the whole call - one
        delisted contract should not blank a watchlist.
        """
        out: dict[str, Quote] = {}
        for symbol in symbols:
            try:
                payload = self._request("GET", f"/v1/market/ticker24Hr/{symbol}")
                out[symbol] = parse_ticker(payload)
            except BrokerError:
                log.warning("no ticker for %s", symbol)
        return out

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        """Candles between two dates.

        A POST, unusually for a read, and unsigned - it is a public endpoint that
        takes its arguments in a body. The venue returns the most recent `limit`
        candles ending at `endTime`, so the window is expressed that way and then
        trimmed to the dates asked for.
        """
        interval = _INTERVALS.get(resolution, resolution)
        end = datetime.combine(date_to, time.max).timestamp()
        rows = self._request(
            "POST",
            "/v1/market/klines?priceType=LAST_PRICE",
            body={"pair": symbol, "interval": interval, "limit": 1000, "endTime": int(end * 1000)},
        )
        if not isinstance(rows, list):
            raise BrokerError("Shark returned no candles")
        candles = parse_klines(rows)
        start = datetime.combine(date_from, time.min, tzinfo=UTC)
        finish = datetime.combine(date_to, time.max, tzinfo=UTC)
        return [c for c in candles if start <= c.timestamp <= finish]

    # ------------------------------------------------------------------ trading

    def get_positions(self) -> list[Position]:
        """Open positions.

        Signed, and returns an empty list when there are none - which is a normal
        answer, not a failure.
        """
        rows = self._request("GET", "/v1/positions/OPEN", signed=True)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise BrokerError("Shark returned no position list")
        return parse_positions(rows)

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a real order, with real money, on leverage.

        The body shape is the venue's own - `placeType: ORDER_FORM` is what its
        web client sends, and omitting it is rejected. `marginAsset` is INR even
        for a USDT-quoted contract: the account is margined in rupees and the
        venue converts at a rate it reports on the position.

        Callers must have run the risk checks first. Nothing here second-guesses
        the quantity beyond what the venue itself rejects.
        """
        body: dict[str, Any] = {
            "placeType": "ORDER_FORM",
            "symbol": order.symbol,
            "side": order.side.upper(),
            "type": order.order_type.upper(),
            "quantity": order.quantity,
            "reduceOnly": False,
            "marginAsset": order.product_type or "INR",
        }
        if order.order_type.upper() in {"LIMIT", "STOP_LIMIT"}:
            if order.limit_price <= 0:
                raise BrokerError("a limit order needs a price")
            body["price"] = order.limit_price

        payload = self._request("POST", "/v1/order/place-order", body=body, signed=True)
        if not isinstance(payload, dict):
            raise BrokerError("Shark accepted the order but said nothing useful")
        order_id = str(
            payload.get("clientOrderId") or payload.get("id") or payload.get("orderId") or ""
        )
        return OrderResult(order_id=order_id, message=str(payload.get("status") or "accepted"))
