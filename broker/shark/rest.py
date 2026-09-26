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

# Imported by name: `time` above is datetime.time, which shadows the module.
from time import monotonic
from typing import Any

import requests

from broker.errors import BrokerError, BrokerUnreachable, classify_status
from broker.models import Candle, OrderRequest, OrderResult, Position, Quote
from broker.shark.models import ContractSpec, PerpPosition
from broker.shark.parse import (
    parse_contracts,
    parse_klines,
    parse_perp_positions,
    parse_positions,
    parse_ticker,
)
from broker.shark.signing import headers, signed_body, signed_query, timestamp_ms

log = logging.getLogger(__name__)

BASE_URL = "https://api.sharkexchange.in"
TIMEOUT = 20.0

#: Contract definitions change rarely, and the catalogue is 400KB.
CONTRACTS_TTL = 24 * 3600.0

#: What the venue will margin a position in. The account settles in rupees even
#: for a USDT-quoted contract, so that is the default.
MARGIN_ASSETS = frozenset({"INR", "USDT"})
DEFAULT_MARGIN_ASSET = "INR"

#: ISOLATED risks only the margin posted against a position; CROSS puts the rest
#: of the account behind it. Isolated is the default here for that reason.
MARGIN_MODES = frozenset({"ISOLATED", "CROSS"})
DEFAULT_MARGIN_MODE = "ISOLATED"

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
        self._contracts: dict[str, ContractSpec] | None = None
        self._contracts_at = 0.0

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
        """Translate a failure into something the desk can act on.

        `details` is included, and that is not a nicety. The venue answers a bad
        signature with message "Access denied" and details "Signature mismatch" -
        the first is a category and the second is the cause, and reading only the
        first turned a one-line fix into an afternoon of guessing.
        """
        message = response.text[:200]
        try:
            body = response.json()
            if isinstance(body, dict):
                headline = str(body.get("message") or body.get("error") or "").strip()
                details = str(body.get("details") or "").strip()
                if headline and details and details != headline:
                    message = f"{headline}: {details}"
                elif headline or details:
                    message = headline or details
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

    def get_contracts(self) -> dict[str, ContractSpec]:
        """What the venue will accept, per contract.

        Public and unsigned. Cached for a day: these are contract definitions, not
        prices, and re-fetching a 400KB catalogue to learn a leverage ceiling that
        has not moved in months is a waste of a 60-per-minute budget.
        """
        now = monotonic()
        if self._contracts is not None and now - self._contracts_at < CONTRACTS_TTL:
            return self._contracts
        payload = self._request("GET", "/v1/exchange/exchangeInfo")
        if not isinstance(payload, dict):
            raise BrokerError("Shark returned no contract list")
        self._contracts = parse_contracts(payload)
        self._contracts_at = now
        return self._contracts

    def get_perp_positions(self) -> list[PerpPosition]:
        """Open positions with their leverage, margin and liquidation price.

        The same request as `get_positions`, read into the richer model. Both
        exist because the generic one satisfies `Trading` - which anything
        venue-agnostic uses - while a perps desk needs what only this one carries.
        """
        rows = self._request("GET", "/v1/positions/OPEN", signed=True)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise BrokerError("Shark returned no position list")
        return parse_perp_positions(rows)

    def close_position(self, position: PerpPosition) -> OrderResult:
        """Close a position at the market, for its full size.

        A reduce-only market order on the opposite side, which is how this venue
        closes: there is no "close" verb, and an ordinary order would open a second
        position in the other direction if the size were ever wrong.

        `reduceOnly` is the whole safety of it. It tells the venue this order may
        only shrink an existing position, so the worst outcome of a stale size or a
        double click is nothing happening rather than a reversed position.

        The side is taken from the position rather than passed in. A caller that
        has to work out which way closes a short is a caller that can get it
        backwards, and getting it backwards doubles the position.
        """
        if position.quantity <= 0:
            raise BrokerError("nothing to close")
        body: dict[str, Any] = {
            "placeType": "ORDER_FORM",
            "symbol": position.symbol,
            "side": "SELL" if position.is_long else "BUY",
            "type": "MARKET",
            "quantity": position.quantity,
            "reduceOnly": True,
            "marginAsset": position.margin_asset or DEFAULT_MARGIN_ASSET,
        }
        payload = self._request("POST", "/v1/order/place-order", body=body, signed=True)
        if not isinstance(payload, dict):
            raise BrokerError("Shark accepted the close but said nothing useful")
        return OrderResult(
            order_id=str(payload.get("clientOrderId") or payload.get("id") or ""),
            message=str(payload.get("status") or "accepted"),
        )

    def set_protection(
        self,
        position_id: str,
        *,
        quantity: float,
        take_profit: float | None = None,
        stop_loss: float | None = None,
    ) -> None:
        """Ask the venue to hold a take-profit and a stop-loss for a position.

        The point of doing this at the venue rather than here: an exchange-held
        stop fires with this app closed and the machine asleep, which is the only
        kind that means anything on a market that trades overnight. A stop this
        desk watches for is a stop that stops working when a laptop lid shuts.

        Both are optional and sent only when given, so setting one does not clear
        the other by omission. Levels are prices, not distances - the venue wants
        the price to trigger at.

        This writes to a live account. Callers confirm first; nothing here does.
        """
        if take_profit is None and stop_loss is None:
            raise BrokerError("nothing to set: give a take-profit, a stop, or both")
        if quantity <= 0:
            raise BrokerError("a protective order needs a quantity")

        body: dict[str, Any] = {"positionId": position_id}
        if take_profit is not None:
            body["splitTakeProfitOrders"] = [{"quantity": quantity, "price": take_profit}]
        if stop_loss is not None:
            body["splitStopLossOrders"] = [{"quantity": quantity, "price": stop_loss}]

        self._request("POST", "/v2/order/split-tp-sl", body=body, signed=True)

    def set_preference(self, symbol: str, leverage: float, margin_mode: str) -> None:
        """Set both standing settings for one contract in one call.

        Margin mode has the same shape of trap leverage had: an order carries
        neither, so both come from whatever the symbol was last configured with.
        The difference between them is what happens when a position goes wrong -
        ISOLATED risks the margin posted against that position, CROSS risks the
        rest of the account behind it - so inheriting it silently is not a detail.

        One call rather than two because the venue offers one, and two would leave
        a window where the leverage had changed and the margin mode had not.
        """
        mode = margin_mode.upper()
        if mode not in MARGIN_MODES:
            raise BrokerError(f"{margin_mode!r} is not a margin mode")
        if leverage <= 0:
            raise BrokerError(f"{leverage} is not a leverage")
        self._request(
            "POST",
            "/v1/exchange/update/preference",
            body={
                "leverage": leverage,
                "marginMode": mode,
                "contractName": symbol,
                "timestamp": str(timestamp_ms()),
            },
            signed=True,
        )

    def set_leverage(self, symbol: str, leverage: float) -> None:
        """Set the standing leverage for one contract.

        This has to happen before the order, and that is the venue's design rather
        than a convenience: `place-order` has no leverage field, and the docs are
        explicit that an order executes with whatever was previously set for the
        symbol. Ours sent none, so every order ran at whatever the account already
        had - which on this account was 150x, the maximum, while the ticket showed
        the 10x that had been chosen. A 0.42% move would have closed it.

        A PUT, not a POST, and the timestamp goes in the body as a string in the
        venue's own example - so it is sent as one.
        """
        if leverage <= 0:
            raise BrokerError(f"{leverage} is not a leverage")
        self._request(
            "PUT",
            "/v1/exchange/update/leverage",
            body={
                "leverage": leverage,
                "contractName": symbol,
                "timestamp": str(timestamp_ms()),
            },
            signed=True,
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Place a real order, with real money, on leverage.

        The body shape is the venue's own - `placeType: ORDER_FORM` is what its
        web client sends, and omitting it is rejected. `marginAsset` is INR even
        for a USDT-quoted contract: the account is margined in rupees and the
        venue converts at a rate it reports on the position.

        That field is not `product_type`, which is the shared model's options
        concept and defaults to "MARGIN" - a value this venue has never heard of.
        Sending it produced an order the venue refused, which is the right outcome
        for the wrong reason: it would have kept failing until someone read the
        body. Only a real margin asset is passed through.

        Callers must have run the risk checks first, and must have set the leverage
        they want - see `set_leverage`. There is no leverage field here to pass it
        in; the venue applies whatever the symbol was last configured with.
        """
        body: dict[str, Any] = {
            "placeType": "ORDER_FORM",
            "symbol": order.symbol,
            "side": order.side.upper(),
            "type": order.order_type.upper(),
            "quantity": order.quantity,
            "reduceOnly": False,
            "marginAsset": (
                order.product_type if order.product_type in MARGIN_ASSETS else DEFAULT_MARGIN_ASSET
            ),
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
