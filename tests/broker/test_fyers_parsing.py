"""Unit tests for FyersBroker's response parsing, using real recorded API
responses as fixtures (captured once, checked in) so parsing is verified
against Fyers' actual wire format, not a guessed shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from broker.fyers import (
    FyersApiError,
    parse_candles,
    parse_option_chain,
    parse_quotes,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return result


def test_parse_option_chain_splits_underlying_from_strikes() -> None:
    raw = load_fixture("nifty_option_chain.json")

    chain = parse_option_chain(raw, requested_symbol="NSE:NIFTY50-INDEX")

    assert chain.underlying_symbol == "NSE:NIFTY50-INDEX"
    assert chain.underlying_ltp == pytest.approx(23346.4)
    assert len(chain.rows) == 10  # 11 entries in fixture minus 1 underlying row
    assert {row.option_type for row in chain.rows} == {"CE", "PE"}


def test_parse_option_chain_includes_greeks_when_present() -> None:
    raw = load_fixture("nifty_option_chain.json")

    chain = parse_option_chain(raw, requested_symbol="NSE:NIFTY50-INDEX")

    row_with_greeks = next(row for row in chain.rows if row.greeks is not None)
    assert row_with_greeks.greeks is not None
    assert -1.0 <= row_with_greeks.greeks.delta <= 1.0


def test_parse_option_chain_raises_on_error_response() -> None:
    with pytest.raises(FyersApiError):
        parse_option_chain(
            {"s": "error", "code": -1, "message": "boom"},
            requested_symbol="NSE:NIFTY50-INDEX",
        )


def test_parse_quotes_maps_symbol_to_quote() -> None:
    raw = load_fixture("nifty_quote.json")

    quotes = parse_quotes(raw)

    assert set(quotes.keys()) == {"NSE:NIFTY50-INDEX"}
    quote = quotes["NSE:NIFTY50-INDEX"]
    assert quote.ltp == pytest.approx(23346.4)
    assert quote.open == pytest.approx(23334.7)


def test_parse_candles_returns_chronological_candles() -> None:
    raw = load_fixture("nifty_history.json")

    candles = parse_candles(raw)

    assert len(candles) == 4
    assert candles[0].open == pytest.approx(23576.15)
    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)
