"""Contract tests every `Broker` implementation must satisfy.

Run against `FakeBroker` here. When a second real broker adapter is added
later, it gets parametrized into `broker_under_test` too — proving Liskov
substitution rather than just asserting it in a docstring.
"""

from __future__ import annotations

from datetime import date

import pytest

from broker.base import Broker
from broker.fake import FakeBroker
from broker.models import Candle, OptionChain, Quote


@pytest.fixture(params=[FakeBroker])
def broker_under_test(request: pytest.FixtureRequest) -> Broker:
    return request.param()  # type: ignore[no-any-return]


def test_get_quote_returns_quote_per_symbol(broker_under_test: Broker) -> None:
    result = broker_under_test.get_quote(["NSE:NIFTY50-INDEX"])

    assert set(result.keys()) == {"NSE:NIFTY50-INDEX"}
    assert isinstance(result["NSE:NIFTY50-INDEX"], Quote)


def test_get_option_chain_returns_rows_for_both_option_types(
    broker_under_test: Broker,
) -> None:
    chain = broker_under_test.get_option_chain("NSE:NIFTY50-INDEX", strike_count=2)

    assert isinstance(chain, OptionChain)
    assert chain.underlying_symbol == "NSE:NIFTY50-INDEX"
    assert len(chain.rows) > 0
    option_types = {row.option_type for row in chain.rows}
    assert option_types == {"CE", "PE"}


def test_get_history_returns_candles_in_chronological_order(
    broker_under_test: Broker,
) -> None:
    candles = broker_under_test.get_history(
        "NSE:NIFTY50-INDEX", "D", date(2026, 1, 1), date(2026, 1, 5)
    )

    assert all(isinstance(c, Candle) for c in candles)
    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)


def test_subscribe_ticks_invokes_callback(broker_under_test: Broker) -> None:
    received: list[Quote] = []

    broker_under_test.subscribe_ticks(["NSE:NIFTY50-INDEX"], received.append)

    assert len(received) >= 1
    assert received[0].symbol == "NSE:NIFTY50-INDEX"
