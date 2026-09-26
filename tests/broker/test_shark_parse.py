"""Shark's wire format, read against responses the live API actually returned.

The fixtures in `fixtures/shark/` were captured from the real venue; the private
ones have the account's own ids and figures replaced, because shapes are what the
parsers must handle and a trading history is not the repository's business.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest

from broker.shark.parse import SharkParseError, parse_klines, parse_positions, parse_ticker

FIXTURES = Path(__file__).parent / "fixtures" / "shark"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class TestTicker:
    def test_reads_a_real_ticker(self) -> None:
        quote = parse_ticker(load("ticker24hr_btcusdt.json"))
        assert quote.symbol == "BTCUSDT"
        assert quote.ltp > 0
        assert quote.high >= quote.ltp >= quote.low or quote.high >= quote.low

    def test_prev_close_is_derived_from_the_change_not_the_open(self) -> None:
        # `o` is the price at the start of a rolling 24-hour window, which on a
        # market that never closes is not yesterday's close. The venue's own `p`
        # is the change it displays, so last minus p is the figure to compare to.
        payload = {"data": {"s": "BTCUSDT", "c": "100", "o": "90", "h": "101", "l": "89",
                            "p": "-5", "v": "1", "E": 1790417728854}}
        quote = parse_ticker(payload)
        assert quote.prev_close == 105.0
        assert quote.open == 90.0

    def test_a_ticker_without_a_change_falls_back_to_flat(self) -> None:
        payload = {"data": {"s": "X", "c": "100", "o": "90", "h": "101", "l": "89", "v": "1"}}
        assert parse_ticker(payload).prev_close == 100.0

    def test_no_bid_or_ask_is_reported_as_unquoted(self) -> None:
        # The ticker does not carry them, and inventing one would be a price the
        # desk could act on.
        quote = parse_ticker(load("ticker24hr_btcusdt.json"))
        assert quote.bid == 0.0
        assert quote.ask == 0.0

    def test_a_missing_price_is_an_error_not_a_zero(self) -> None:
        with pytest.raises(SharkParseError, match="last price"):
            parse_ticker({"data": {"s": "X", "o": "1", "h": "1", "l": "1"}})

    def test_a_payload_with_no_object_is_an_error(self) -> None:
        with pytest.raises(SharkParseError):
            parse_ticker({"data": "nonsense"})

    def test_timestamps_are_utc_aware(self) -> None:
        quote = parse_ticker(load("ticker24hr_btcusdt.json"))
        assert quote.timestamp.tzinfo is not None
        assert quote.timestamp.utcoffset() == UTC.utcoffset(None)


class TestKlines:
    def test_reads_real_candles_oldest_first(self) -> None:
        candles = parse_klines(load("klines_xauusdt_1h.json"))
        assert len(candles) >= 2
        assert [c.timestamp for c in candles] == sorted(c.timestamp for c in candles)

    def test_values_come_through_as_numbers(self) -> None:
        first = parse_klines(load("klines_xauusdt_1h.json"))[0]
        assert first.high >= first.low
        assert first.volume > 0

    def test_repeated_buckets_collapse_to_the_later_one(self) -> None:
        # The venue repeats candles on some intervals - the reference hit this on
        # 1d. A series that is not strictly ascending is wrong arithmetic before
        # it is a broken chart; the later entry is the more complete aggregation.
        rows = [
            {"startTime": "1000", "open": "1", "high": "2", "low": "1", "close": "1",
             "endTime": "1999", "volume": "5"},
            {"startTime": "1000", "open": "1", "high": "3", "low": "1", "close": "2",
             "endTime": "1999", "volume": "9"},
        ]
        candles = parse_klines(rows)
        assert len(candles) == 1
        assert candles[0].close == 2.0
        assert candles[0].volume == 9.0

    def test_a_candle_without_a_start_is_an_error(self) -> None:
        with pytest.raises(SharkParseError, match="startTime"):
            parse_klines([{"open": "1", "high": "1", "low": "1", "close": "1"}])

    def test_fractional_volume_survives(self) -> None:
        # A crypto venue quotes 1762.221, not 1762 - rounding it to an int would
        # quietly lose size on every candle.
        rows = [{"startTime": "1000", "open": "1", "high": "1", "low": "1", "close": "1",
                 "endTime": "1999", "volume": "1762.221"}]
        assert parse_klines(rows)[0].volume == pytest.approx(1762.221)


class TestPositions:
    def test_an_empty_list_is_a_normal_answer(self) -> None:
        assert parse_positions(load("positions_open_empty.json")) == []

    def test_a_closed_position_is_left_out(self) -> None:
        # The fixture is a real CLOSED position, and it still carries its original
        # positionAmount of 0.01 - closure shows in positionStatus, not in the
        # size. Filtering on size alone read a finished trade as a live holding.
        rows = load("positions_closed.json")
        assert rows[0]["positionStatus"] == "CLOSED"
        assert rows[0]["positionAmount"] != 0
        assert parse_positions(rows) == []

    def test_a_row_claiming_to_be_closed_is_believed_over_the_url(self) -> None:
        rows = [{"contractPair": "X", "positionType": "LONG", "positionAmount": 1,
                 "entryPrice": 1, "positionStatus": "LIQUIDATED"}]
        assert parse_positions(rows) == []

    def test_a_short_is_signed_negative(self) -> None:
        # The venue reports SHORT with a positive quantity, so the sign has to be
        # applied. A short held as a positive number is how a hedge gets counted
        # as exposure.
        rows = [{"contractPair": "XAUUSDT", "positionType": "SHORT", "positionAmount": 0.01,
                 "entryPrice": 4300.0, "marginType": "ISOLATED"}]
        (position,) = parse_positions(rows)
        assert position.net_quantity == -0.01

    def test_a_long_keeps_its_sign(self) -> None:
        rows = [{"contractPair": "BTCUSDT", "positionType": "LONG", "positionAmount": 0.002,
                 "entryPrice": 84000.0, "marginType": "CROSS"}]
        (position,) = parse_positions(rows)
        assert position.net_quantity == 0.002
        assert position.average_price == 84000.0
        assert position.product_type == "CROSS"

    def test_fractional_size_survives(self) -> None:
        rows = [{"contractPair": "BTCUSDT", "positionType": "LONG", "positionAmount": 0.001,
                 "entryPrice": 1.0}]
        assert parse_positions(rows)[0].net_quantity == pytest.approx(0.001)

    def test_a_position_without_a_pair_is_an_error(self) -> None:
        with pytest.raises(SharkParseError, match="contract pair"):
            parse_positions([{"positionType": "LONG", "positionAmount": 1}])

    def test_the_margin_mode_is_carried_as_the_product_type(self) -> None:
        # It is the perps equivalent, and the thing that decides how a
        # liquidation is calculated.
        rows = [{"contractPair": "X", "positionType": "LONG", "positionAmount": 1,
                 "entryPrice": 1, "marginType": "ISOLATED"}]
        assert parse_positions(rows)[0].product_type == "ISOLATED"
