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
    parse_funds,
    parse_option_chain,
    parse_place_order,
    parse_positions,
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


def test_parse_funds_extracts_balances_by_title() -> None:
    raw = load_fixture("fyers_funds.json")

    funds = parse_funds(raw)

    assert funds.total_balance == pytest.approx(160273.03)
    assert funds.utilized_margin == pytest.approx(84049.94)
    assert funds.available_balance == pytest.approx(76223.09)


def test_parse_funds_raises_on_error_response() -> None:
    with pytest.raises(FyersApiError):
        parse_funds({"s": "error", "code": -1, "message": "boom"})


def test_parse_place_order_extracts_order_id() -> None:
    # Built from Fyers' publicly documented place-order response format
    # (not a live capture - that would mean placing a real order just to
    # record a fixture).
    raw = load_fixture("fyers_place_order_response.json")

    result = parse_place_order(raw)

    assert result.order_id == "24101300025444"
    assert "submitted" in result.message.lower()


def test_parse_place_order_raises_on_error_response() -> None:
    with pytest.raises(FyersApiError):
        parse_place_order({"s": "error", "code": -1, "message": "boom"})


def test_parse_positions_returns_only_open_positions() -> None:
    # Synthetic fixture matching the real Fyers positions schema (not the
    # account's actual real positions, to avoid checking in real financial
    # data - see the schema captured live during development).
    raw = load_fixture("fyers_positions.json")

    positions = parse_positions(raw)

    assert len(positions) == 1  # the netQty=0 (flat) entry is filtered out
    position = positions[0]
    assert position.symbol == "NSE:NIFTY2692223500CE"
    assert position.net_quantity == -50
    assert position.average_price == pytest.approx(40.5)
    assert position.unrealized_pnl == pytest.approx(125.0)


def test_parse_positions_raises_on_error_response() -> None:
    with pytest.raises(FyersApiError):
        parse_positions({"s": "error", "code": -1, "message": "boom"})
