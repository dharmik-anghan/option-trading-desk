"""Where the alert watcher gets what it judges.

The routing test is the one that matters. A price watch is offered for whichever
desk is on screen, so a crypto symbol reaches this code as a matter of course -
and asking the options broker for one does not come back empty, it raises on a
response shape it cannot read. Because those quotes are one batched call, a
single crypto symbol stopped every price watch working, index ones included.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from alerting.models import Direction, WatchKind
from api.alert_inputs import gather
from api.store import open_db
from broker.fake import FakeBroker
from broker.models import Quote, Tick
from feeds.fetch import Feeds
from storage.alert_repo import add_watch
from streaming import TickHub


class Exploding(FakeBroker):
    """Stands in for the options broker meeting a symbol it does not list."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[list[str]] = []

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        self.asked.append(list(symbols))
        if any(s in {"BTCUSDT", "XAUUSDT", "CLUSDT"} for s in symbols):
            # What Fyers actually does: KeyError on a response it cannot read.
            raise KeyError("lp")
        return super().get_quote(symbols)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "alerts.db"
    open_db(path).close()
    return path


def _watch(db: Path, symbol: str) -> None:
    conn = open_db(db)
    try:
        add_watch(
            conn,
            kind=WatchKind.PRICE,
            direction=Direction.ABOVE,
            level=1.0,
            symbol=symbol,
            note="",
            created_at=datetime.now(UTC).isoformat(),
        )
    finally:
        conn.close()


def _hub(**prices: float) -> TickHub:
    hub = TickHub()
    for symbol, price in prices.items():
        hub.publish(Tick(symbol=symbol, price=price, at=datetime.now(UTC)))
    return hub


class TestQuoteRouting:
    def test_a_streamed_symbol_comes_from_the_hub(self, db: Path) -> None:
        _watch(db, "BTCUSDT")
        broker = Exploding()
        inputs = gather(db, broker, Feeds(), _hub(BTCUSDT=84000.0))
        assert inputs.quotes == {"BTCUSDT": 84000.0}
        assert broker.asked == [], "the options broker should not be asked for a perp"

    def test_one_crypto_symbol_no_longer_breaks_the_index_watches(self, db: Path) -> None:
        # The bug: both went in one batched call, the call raised, and every
        # price watch stopped working.
        _watch(db, "BTCUSDT")
        _watch(db, "NSE:NIFTY50-INDEX")
        broker = Exploding()
        inputs = gather(db, broker, Feeds(), _hub(BTCUSDT=84000.0))
        assert inputs.quotes["BTCUSDT"] == 84000.0
        assert "NSE:NIFTY50-INDEX" in inputs.quotes
        assert broker.asked == [["NSE:NIFTY50-INDEX"]]

    def test_a_streamed_symbol_the_stream_has_not_carried_is_absent(self, db: Path) -> None:
        # Absent, not zero: zero would fire every "below" watch on startup.
        _watch(db, "XAUUSDT")
        inputs = gather(db, Exploding(), Feeds(), _hub())
        assert "XAUUSDT" not in inputs.quotes

    def test_no_hub_is_survivable(self, db: Path) -> None:
        # The stream may have failed to connect; the pass still has to run.
        _watch(db, "BTCUSDT")
        inputs = gather(db, Exploding(), Feeds(), None)
        assert inputs.quotes == {}

    def test_watching_nothing_asks_nobody(self, db: Path) -> None:
        broker = Exploding()
        inputs = gather(db, broker, Feeds(), _hub(BTCUSDT=84000.0))
        assert inputs.quotes == {}
        assert broker.asked == []
