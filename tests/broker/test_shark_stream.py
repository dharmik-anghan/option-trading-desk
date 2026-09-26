"""The tick stream's parsing and subscription shape, without a network.

Connecting is exercised against the live venue by hand (and was, repeatedly, to
learn the protocol); what is worth a test is the frame handling, because a bad
frame arriving at 3am must cost one frame rather than the connection.
"""

from __future__ import annotations

from datetime import UTC

from broker.base import AsyncStreaming
from broker.shark.stream import STREAM_URL, SharkStream, parse_tick, topic


def test_topics_are_lowercased() -> None:
    # Uppercase subscribes to nothing: the venue accepts it and sends no frames,
    # which is a silent failure rather than an error.
    assert topic("BTCUSDT") == "btcusdt@ticker"
    assert topic("xauusdt") == "xauusdt@ticker"


def test_the_stream_is_a_different_host_from_the_rest_api() -> None:
    # Recorded because it is not in the docs and cost time to find.
    assert "fawss" in STREAM_URL
    assert "api.sharkexchange" not in STREAM_URL


def test_it_satisfies_the_async_streaming_protocol() -> None:
    assert isinstance(SharkStream(), AsyncStreaming)


class TestFrames:
    def test_a_good_frame_becomes_a_tick(self) -> None:
        tick = parse_tick({"s": "BTCUSDT", "c": "84105.1", "E": 1790417728854})
        assert tick is not None
        assert tick.symbol == "BTCUSDT"
        assert tick.price == 84105.1
        assert tick.at.tzinfo is not None
        assert tick.at.utcoffset() == UTC.utcoffset(None)

    def test_a_frame_without_a_symbol_is_dropped(self) -> None:
        assert parse_tick({"c": "84105.1"}) is None

    def test_a_frame_without_a_price_is_dropped(self) -> None:
        assert parse_tick({"s": "BTCUSDT"}) is None

    def test_an_unparseable_price_is_dropped(self) -> None:
        assert parse_tick({"s": "BTCUSDT", "c": "not-a-number"}) is None

    def test_a_zero_price_is_dropped(self) -> None:
        # Not a market at nothing - a bad frame. Passing it on would move every
        # level being watched.
        assert parse_tick({"s": "BTCUSDT", "c": "0"}) is None
        assert parse_tick({"s": "BTCUSDT", "c": "-5"}) is None

    def test_a_frame_without_a_time_is_stamped_on_arrival(self) -> None:
        tick = parse_tick({"s": "BTCUSDT", "c": "1"})
        assert tick is not None and tick.at.tzinfo is not None

    def test_the_venues_own_time_is_preferred(self) -> None:
        tick = parse_tick({"s": "BTCUSDT", "c": "1", "E": 1790417728854})
        assert tick is not None
        assert int(tick.at.timestamp() * 1000) == 1790417728854

    def test_a_string_timestamp_falls_back_rather_than_failing(self) -> None:
        # The venue sends E as a number on this event; a string would still be
        # readable as a price, and a wrong clock is not worth losing the frame.
        tick = parse_tick({"s": "BTCUSDT", "c": "1", "E": "1790417728854"})
        assert tick is not None


class TestLifecycle:
    def test_stop_before_start_does_nothing(self) -> None:
        # Shutdown runs whether or not the venue was ever configured.
        import asyncio

        asyncio.run(SharkStream().stop())

    def test_it_starts_disconnected_and_counts_nothing(self) -> None:
        stream = SharkStream()
        assert stream.connected is False
        assert stream.ticks_received == 0
