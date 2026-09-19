"""Fyers implementation of the `Broker` protocol.

Parsing is split into standalone functions (`parse_*`) so they're testable
against recorded fixtures without any network access or live credentials —
see tests/broker/test_fyers_parsing.py.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from fyers_apiv3 import fyersModel

from broker.base import Broker
from broker.models import Candle, Funds, Greeks, OptionChain, OptionChainRow, Quote


class FyersApiError(RuntimeError):
    """Raised when Fyers returns a non-ok response."""


def _check_ok(raw: dict[str, Any]) -> None:
    if raw.get("s") == "error" or (raw.get("code") is not None and raw.get("code") != 200):
        raise FyersApiError(f"Fyers API error: {raw.get('message') or raw}")


def parse_option_chain(raw: dict[str, Any], *, requested_symbol: str) -> OptionChain:
    _check_ok(raw)
    entries = raw["data"]["optionsChain"]

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
            greeks=Greeks(**e["greeks"]) if e.get("greeks") else None,
        )
        for e in entries
        if e.get("strike_price", -1) != -1
    ]

    return OptionChain(
        underlying_symbol=requested_symbol,
        underlying_ltp=underlying["ltp"],
        fetched_at=datetime.now(UTC),
        rows=rows,
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


class FyersBroker(Broker):
    def __init__(self, client_id: str, access_token: str) -> None:
        self._client = fyersModel.FyersModel(
            client_id=client_id, token=access_token, is_async=False
        )

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        raw = self._client.quotes(data={"symbols": ",".join(symbols)})
        return parse_quotes(raw)

    def get_option_chain(self, symbol: str, strike_count: int = 10) -> OptionChain:
        raw = self._client.optionchain(
            data={
                "symbol": symbol,
                "strikecount": strike_count,
                "timestamp": "",
                "greeks": "1",
            }
        )
        return parse_option_chain(raw, requested_symbol=symbol)

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
        raw = self._client.funds()
        return parse_funds(raw)

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        raise NotImplementedError("WebSocket tick streaming lands later in Phase 1")
