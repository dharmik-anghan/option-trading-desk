"""Fyers implementation of the `Broker` protocol.

Parsing is split into standalone functions (`parse_*`) so they're testable
against recorded fixtures without any network access or live credentials —
see tests/broker/test_fyers_parsing.py.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fyers_apiv3 import fyersModel

from broker.base import Broker
from broker.errors import BrokerError, BrokerUnreachable, classify_status
from broker.models import (
    Candle,
    Expiry,
    Fill,
    Funds,
    Greeks,
    OptionChain,
    OptionChainRow,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
)
from venues.calendar import IST


def _call(send: Callable[[], Any]) -> Any:
    """Run an SDK call, turning a raised transport error into BrokerUnreachable.

    Most failures come back as an error dict (see `_check_ok`), but a socket
    that dies mid-flight raises instead, and an unclassified exception reaches
    the desk as an opaque 500.
    """
    try:
        return send()
    except BrokerError:
        raise
    except (OSError, ConnectionError, TimeoutError) as exc:
        raise BrokerUnreachable from exc


class FyersApiError(BrokerError):
    """Raised when Fyers returns a non-ok response.

    Kept as a subclass of `BrokerError` so existing `except FyersApiError`
    callers still work while the API layer can catch the broader class.
    """


def _check_ok(raw: dict[str, Any]) -> None:
    """Raise the error class that matches what Fyers said.

    The SDK swallows transport failures and hands back a dict describing them,
    so a rate limit, a dead connection and a stale token all arrive here
    looking alike. Telling them apart is what lets the desk say which it is
    instead of freezing silently.
    """
    if raw.get("s") != "error" and (raw.get("code") is None or raw.get("code") == 200):
        return

    detail = raw.get("Error") if isinstance(raw.get("Error"), dict) else None
    code = (detail or raw).get("code") if isinstance(detail or raw, dict) else None
    message = str((detail or raw).get("message") or raw)

    # The SDK reports connection failures in the message rather than raising.
    if any(
        marker in message
        for marker in (
            "Max retries exceeded",
            "NameResolutionError",
            "Connection aborted",
            "Connection reset",
            "ConnectionError",
            "Failed to resolve",
            "timed out",
        )
    ):
        raise BrokerUnreachable

    # An error we cannot classify stays this adapter's own type, rather than
    # the broker-agnostic base - callers already catch FyersApiError.
    cls = classify_status(code if isinstance(code, int) else None, message)
    if cls is BrokerError:
        cls = FyersApiError
    raise cls(f"Fyers API error: {message}")


def parse_option_chain(
    raw: dict[str, Any], *, requested_symbol: str, expiry_token: str = ""
) -> OptionChain:
    _check_ok(raw)
    data = raw["data"]
    entries = data["optionsChain"]

    underlying = next(e for e in entries if e.get("strike_price", -1) == -1)
    rows = [
        OptionChainRow(
            symbol=e["symbol"],
            strike=e["strike_price"],
            option_type=e["option_type"],
            ltp=e["ltp"],
            bid=e["bid"],
            ask=e["ask"],
            oi=e.get("oi", 0),
            prev_oi=e.get("prev_oi", 0),
            volume=e.get("volume", 0),
            ltp_change=e.get("ltpch", 0.0),
            ltp_change_pct=e.get("ltpchp", 0.0),
            oi_change=e.get("oich", 0),
            oi_change_pct=e.get("oichp", 0.0),
            greeks=Greeks(**e["greeks"]) if e.get("greeks") else None,
        )
        for e in entries
        if e.get("strike_price", -1) != -1
    ]

    expiries = [
        Expiry(
            date=e["date"],
            token=str(e["expiry"]),
            # Fyers flags monthly expiries "M" and weeklies "W"
            weekly=e.get("expiry_flag") != "M",
        )
        for e in data.get("expiryData", [])
    ]
    vix = data.get("indiavixData") or {}

    return OptionChain(
        underlying_symbol=requested_symbol,
        underlying_ltp=underlying["ltp"],
        fetched_at=datetime.now(UTC),
        rows=rows,
        expiries=expiries,
        # an empty request means "nearest", which is whatever came back first
        expiry_token=expiry_token or (expiries[0].token if expiries else None),
        call_oi=data.get("callOi", 0),
        put_oi=data.get("putOi", 0),
        india_vix=vix.get("ltp"),
    )


def parse_quotes(raw: dict[str, Any]) -> dict[str, Quote]:
    _check_ok(raw)
    quotes: dict[str, Quote] = {}
    for entry in raw["d"]:
        v = entry["v"]
        quotes[entry["n"]] = Quote(
            symbol=entry["n"],
            ltp=v["lp"],
            open=v["open_price"],
            high=v["high_price"],
            low=v["low_price"],
            prev_close=v["prev_close_price"],
            volume=v["volume"],
            bid=v["bid"],
            ask=v["ask"],
            timestamp=datetime.fromtimestamp(int(v["tt"]), tz=UTC),
        )
    return quotes


def parse_candles(raw: dict[str, Any]) -> list[Candle]:
    _check_ok(raw)
    return [
        Candle(
            timestamp=datetime.fromtimestamp(c[0], tz=UTC),
            open=c[1],
            high=c[2],
            low=c[3],
            close=c[4],
            volume=c[5],
        )
        for c in raw["candles"]
    ]


_FUND_TITLES = {
    "Total Balance": "total_balance",
    "Utilized Amount": "utilized_margin",
    "Available Balance": "available_balance",
    "Realized Profit and Loss": "realized_pnl",
}


def parse_funds(raw: dict[str, Any]) -> Funds:
    _check_ok(raw)
    values: dict[str, float] = {}
    for entry in raw["fund_limit"]:
        field = _FUND_TITLES.get(entry["title"])
        if field is not None:
            values[field] = entry["equityAmount"]
    missing = _FUND_TITLES.values() - values.keys()
    if missing:
        raise FyersApiError(f"Funds response missing expected fields: {missing}")
    return Funds(**values)


def _check_order_ok(raw: dict[str, Any]) -> None:
    """Order-related endpoints signal success via `s == "ok"` with a
    response-specific `code` (e.g. 1101), not the `code == 200` convention
    the data endpoints use - so this can't reuse `_check_ok`.
    """
    if raw.get("s") != "ok":
        raise FyersApiError(f"Fyers API error: {raw.get('message') or raw}")


def parse_place_order(raw: dict[str, Any]) -> OrderResult:
    _check_order_ok(raw)
    return OrderResult(order_id=str(raw["id"]), message=raw.get("message", ""))


def parse_positions(raw: dict[str, Any]) -> list[Position]:
    _check_ok(raw)
    return [
        Position(
            symbol=p["symbol"],
            net_quantity=p["netQty"],
            average_price=p["netAvg"],
            ltp=p["ltp"],
            unrealized_pnl=p["unrealized_profit"],
            product_type=p["productType"],
        )
        for p in raw["netPositions"]
        if p["netQty"] != 0
    ]


#: Rows per page of trade history. Fyers' own default and maximum.
HISTORY_PAGE = 100


def _fill_time(text: str) -> datetime:
    """"29-Sep-2026 10:36:04" (IST) -> aware UTC."""
    return datetime.strptime(text, "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST).astimezone(UTC)


def parse_fills(raw: dict[str, Any], *, history: bool) -> list[Fill]:
    """Fills from the day's tradebook, or from trade history.

    The two endpoints describe the same thing under different names -
    `tradedQty`/`tradePrice` today, `traded_qty`/`trade_price` for past days, and
    the rows under `tradeBook` or `data`. Their trade numbers differ too: today's
    is "<order>-<exchange trade>", history's the exchange trade alone. The fill id
    uses the part both share, so a fill seen today and again tomorrow in history
    is recognised as the same one.
    """
    _check_ok(raw)
    rows = raw.get("data") if history else raw.get("tradeBook")
    fills = []
    for row in rows or []:
        order_id = str(row["orderNumber"])
        trade_no = str(row["tradeNumber"]).split("-")[-1]
        fills.append(
            Fill(
                fill_id=f"{order_id}:{trade_no}",
                order_id=order_id,
                symbol=row["symbol"],
                side="BUY" if int(row["side"]) == 1 else "SELL",
                quantity=float(row["traded_qty"] if history else row["tradedQty"]),
                price=float(row["trade_price"] if history else row["tradePrice"]),
                at=_fill_time(row["orderDateTime"]),
            )
        )
    return fills


def parse_booked(raw: dict[str, Any]) -> float:
    """What has been realized today, from the positions response.

    Not from funds: its "Realized Profit and Loss" read 0 at 13:15 on 29 Sep
    2026, after a call spread closed at 10:36 for +4,881.50 - the figure the
    positions response carried, as `overall.pl_realized` and as the sum of its
    rows' `realized_profit`. The funds number evidently waits for settlement;
    a desk showing today's P&L cannot.
    """
    _check_ok(raw)
    overall = raw.get("overall") or {}
    if "pl_realized" in overall:
        return float(overall["pl_realized"])
    return float(sum(p.get("realized_profit", 0) for p in raw.get("netPositions", [])))


_ORDER_SIDE = {"BUY": 1, "SELL": -1}
_ORDER_TYPE = {"MARKET": 2, "LIMIT": 1}


class FyersBroker(Broker):
    def __init__(self, client_id: str, access_token: str) -> None:
        self._client = fyersModel.FyersModel(
            client_id=client_id, token=access_token, is_async=False
        )

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        raw = _call(lambda: self._client.quotes(data={"symbols": ",".join(symbols)}))
        return parse_quotes(raw)

    def get_option_chain(
        self, symbol: str, strike_count: int = 10, expiry_token: str = ""
    ) -> OptionChain:
        raw = _call(
            lambda: self._client.optionchain(
                data={
                    "symbol": symbol,
                    "strikecount": strike_count,
                    "timestamp": expiry_token,
                    "greeks": "1",
                }
            )
        )
        return parse_option_chain(raw, requested_symbol=symbol, expiry_token=expiry_token)

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        raw = self._client.history(
            data={
                "symbol": symbol,
                "resolution": resolution,
                "date_format": "1",
                "range_from": date_from.isoformat(),
                "range_to": date_to.isoformat(),
                "cont_flag": "1",
            }
        )
        return parse_candles(raw)

    def get_funds(self) -> Funds:
        raw = _call(self._client.funds)
        return parse_funds(raw)

    def place_order(self, order: OrderRequest) -> OrderResult:
        raw = self._client.place_order(
            data={
                "symbol": order.symbol,
                "qty": order.quantity,
                "type": _ORDER_TYPE[order.order_type],
                "side": _ORDER_SIDE[order.side],
                "productType": order.product_type,
                "limitPrice": order.limit_price,
                "stopPrice": 0,
                "validity": "DAY",
                "disclosedQty": 0,
                "offlineOrder": False,
            }
        )
        return parse_place_order(raw)

    def get_fills(self, date_from: date, date_to: date) -> list[Fill]:
        """Fills between two dates, from two read-only endpoints.

        Today's come from the tradebook; earlier days from trade history, which
        does not include today. Neither call can place or alter an order.
        """
        today = datetime.now(IST).date()
        fills: list[Fill] = []
        if date_from < today:
            last = min(date_to, today - timedelta(days=1))
            page = 1
            while True:
                query = {
                    "from_date": date_from.isoformat(),
                    "to_date": last.isoformat(),
                    "page_no": page,
                    "page_size": HISTORY_PAGE,
                    "segment_type": "0",
                    "exchange_type": "0",
                }
                raw = _call(lambda query=query: self._client.tradehistory(query))  # type: ignore[misc]
                batch = parse_fills(raw, history=True)
                fills.extend(batch)
                if len(batch) < HISTORY_PAGE:
                    break
                page += 1
        if date_to >= today:
            fills.extend(parse_fills(_call(self._client.tradebook), history=False))
        return sorted(fills, key=lambda f: f.at)

    def get_booked_pnl(self) -> float:
        """Realized today, from positions (read-only). See `parse_booked`."""
        return parse_booked(_call(self._client.positions))

    def get_positions(self) -> list[Position]:
        raw = _call(self._client.positions)
        return parse_positions(raw)

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        raise NotImplementedError("WebSocket tick streaming lands later in Phase 1")
