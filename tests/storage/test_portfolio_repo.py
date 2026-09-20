from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from broker.models import Position
from storage.db import connect, init_schema
from storage.portfolio_repo import save_portfolio_snapshot, snapshots_since


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = connect(":memory:")
    init_schema(connection)
    return connection


def _sample_positions() -> list[Position]:
    return [
        Position(
            symbol="NSE:NIFTY2692223500CE",
            net_quantity=-50,
            average_price=40.5,
            ltp=38.0,
            unrealized_pnl=125.0,
            product_type="MARGIN",
        )
    ]


def test_save_and_read_back_snapshot(conn: sqlite3.Connection) -> None:
    fetched_at = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)

    save_portfolio_snapshot(
        conn, _sample_positions(), realized_pnl=300.0, unrealized_pnl=125.0, fetched_at=fetched_at
    )
    results = snapshots_since(conn, since=datetime(2026, 9, 1, tzinfo=UTC))

    assert len(results) == 1
    row = results[0]
    assert row.fetched_at == fetched_at
    assert row.realized_pnl == pytest.approx(300.0)
    assert row.unrealized_pnl == pytest.approx(125.0)
    assert len(row.positions) == 1
    assert row.positions[0].symbol == "NSE:NIFTY2692223500CE"


def test_snapshots_since_excludes_older_entries(conn: sqlite3.Connection) -> None:
    save_portfolio_snapshot(
        conn,
        _sample_positions(),
        realized_pnl=100.0,
        unrealized_pnl=0.0,
        fetched_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    save_portfolio_snapshot(
        conn,
        _sample_positions(),
        realized_pnl=200.0,
        unrealized_pnl=0.0,
        fetched_at=datetime(2026, 9, 20, tzinfo=UTC),
    )

    results = snapshots_since(conn, since=datetime(2026, 9, 10, tzinfo=UTC))

    assert len(results) == 1
    assert results[0].fetched_at == datetime(2026, 9, 20, tzinfo=UTC)
