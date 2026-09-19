from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from broker.models import Greeks, OptionChain, OptionChainRow
from storage.db import connect, init_schema
from storage.option_chain_repo import save_snapshot, snapshots_for_symbol


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = connect(":memory:")
    init_schema(connection)
    return connection


def _sample_chain() -> OptionChain:
    return OptionChain(
        underlying_symbol="NSE:NIFTY50-INDEX",
        underlying_ltp=23346.4,
        fetched_at=datetime(2026, 9, 19, 10, 0, tzinfo=UTC),
        rows=[
            OptionChainRow(
                symbol="NSE:NIFTY2692223250PE",
                strike=23250,
                option_type="PE",
                ltp=49.05,
                bid=48.5,
                ask=48.85,
                oi=6491680,
                prev_oi=4667200,
                volume=184740075,
                greeks=Greeks(delta=-0.32, gamma=0.0016, theta=-10.33, vega=8.8, iv=9.41),
            ),
            OptionChainRow(
                symbol="NSE:NIFTY2692223250CE",
                strike=23250,
                option_type="CE",
                ltp=145.0,
                bid=144.5,
                ask=145.5,
                oi=1000,
                prev_oi=900,
                volume=5000,
                greeks=None,
            ),
        ],
    )


def test_save_and_read_back_snapshot(conn: sqlite3.Connection) -> None:
    chain = _sample_chain()

    save_snapshot(conn, chain)
    results = snapshots_for_symbol(conn, "NSE:NIFTY50-INDEX")

    assert len(results) == 1
    saved = results[0]
    assert saved.underlying_symbol == chain.underlying_symbol
    assert saved.underlying_ltp == chain.underlying_ltp
    assert saved.fetched_at == chain.fetched_at
    assert len(saved.rows) == 2
    pe_row = next(r for r in saved.rows if r.option_type == "PE")
    assert pe_row.greeks is not None
    assert pe_row.greeks.delta == pytest.approx(-0.32)
    ce_row = next(r for r in saved.rows if r.option_type == "CE")
    assert ce_row.greeks is None


def test_snapshots_for_symbol_orders_by_fetched_at(conn: sqlite3.Connection) -> None:
    older = _sample_chain()
    newer = _sample_chain().model_copy(
        update={"fetched_at": datetime(2026, 9, 19, 15, 0, tzinfo=UTC)}
    )

    save_snapshot(conn, older)
    save_snapshot(conn, newer)
    results = snapshots_for_symbol(conn, "NSE:NIFTY50-INDEX")

    assert [r.fetched_at for r in results] == [older.fetched_at, newer.fetched_at]


def test_snapshots_for_symbol_returns_empty_for_unknown_symbol(
    conn: sqlite3.Connection,
) -> None:
    assert snapshots_for_symbol(conn, "NSE:UNKNOWN-INDEX") == []
