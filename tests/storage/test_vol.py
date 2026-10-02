"""Recording what options cost, which cannot be fetched again."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from analytics.vol_snapshot import VolSnapshot, snapshot_from
from broker.models import Greeks, OptionChain, OptionChainRow
from jobs.vol_recorder import VolRecorder
from storage.db import connect, init_schema
from storage.migrations import migrate
from storage.vol_repo import save_vol_snapshot, vol_history

AT = datetime(2026, 9, 27, 9, 55, tzinfo=UTC)  # 15:25 in Mumbai


@pytest.fixture
def conn() -> sqlite3.Connection:
    db = connect(":memory:")
    init_schema(db)
    migrate(db)
    return db


def _row(strike: float, kind: str, ltp: float, iv: float) -> OptionChainRow:
    return OptionChainRow(
        symbol=f"NSE:NIFTY{int(strike)}{kind}",
        strike=strike,
        option_type=kind,  # type: ignore[arg-type]
        ltp=ltp,
        bid=ltp - 0.5,
        ask=ltp + 0.5,
        oi=1000,
        prev_oi=900,
        volume=10,
        greeks=Greeks(delta=0.5, gamma=0.001, theta=-10.0, vega=9.0, iv=iv),
    )


def _chain(spot: float = 23140.5, **over: object) -> OptionChain:
    fields: dict[str, object] = {
        "underlying_symbol": "NSE:NIFTY50-INDEX",
        "underlying_ltp": spot,
        "fetched_at": AT,
        "rows": [
            _row(23100, "CE", 140.0, 10.0),
            _row(23100, "PE", 100.0, 10.0),
            _row(23150, "CE", 119.25, 9.85),
            _row(23150, "PE", 76.75, 9.85),
        ],
        "india_vix": 12.16,
    }
    fields.update(over)
    return OptionChain(**fields)  # type: ignore[arg-type]


def test_the_at_the_money_strike_is_the_one_nearest_spot() -> None:
    got = snapshot_from(_chain(), "NSE:NIFTY50-INDEX")

    assert got is not None
    assert got.atm_strike == 23150


def test_the_straddle_is_both_legs_at_the_money() -> None:
    got = snapshot_from(_chain(), "NSE:NIFTY50-INDEX")

    assert got is not None
    assert got.straddle == pytest.approx(196.0)
    assert got.expected_move_pct == pytest.approx(0.847, abs=0.001)


def test_india_vix_is_recorded_beside_the_implied_rather_than_instead_of_it() -> None:
    """They are different figures - a thirty-day constant maturity against the
    money on the near expiry - and on this day they read 12.16 and 9.85."""
    got = snapshot_from(_chain(), "NSE:NIFTY50-INDEX")

    assert got is not None
    assert got.atm_iv == pytest.approx(9.85)
    assert got.india_vix == pytest.approx(12.16)


def test_the_day_is_the_indian_one_not_the_utc_one() -> None:
    """A reading at 15:25 in Mumbai is 09:55 UTC. Keying on the UTC date would
    be right by luck, and stops being right for anything after 05:30 local."""
    got = snapshot_from(_chain(), "NSE:NIFTY50-INDEX")

    assert got is not None
    assert got.day == AT.astimezone(ZoneInfo("Asia/Kolkata")).date()


def test_a_chain_with_no_greeks_records_nothing_rather_than_nulls() -> None:
    """A row of nulls would sit in the history looking like a day volatility was
    unknown rather than a day the fetch was broken."""
    bare = _chain(
        rows=[
            OptionChainRow(
                symbol="x", strike=23150, option_type="CE", ltp=0.0, bid=0.0,
                ask=0.0, oi=0, prev_oi=0, volume=0,
            ),
            OptionChainRow(
                symbol="y", strike=23150, option_type="PE", ltp=0.0, bid=0.0,
                ask=0.0, oi=0, prev_oi=0, volume=0,
            ),
        ]
    )

    assert snapshot_from(bare, "NSE:NIFTY50-INDEX") is None


def test_an_empty_chain_records_nothing() -> None:
    assert snapshot_from(_chain(rows=[]), "NSE:NIFTY50-INDEX") is None


def test_a_second_reading_the_same_day_replaces_the_first(conn: sqlite3.Connection) -> None:
    """The writer runs on a loop; the last reading before the close is the one
    that should survive, not the first of the morning."""
    first = snapshot_from(_chain(), "NSE:NIFTY50-INDEX")
    assert first is not None
    save_vol_snapshot(conn, first)
    save_vol_snapshot(
        conn,
        VolSnapshot(**{**first.__dict__, "atm_iv": 11.5, "straddle": 240.0}),
    )

    held = vol_history(conn, "NSE:NIFTY50-INDEX")

    assert len(held) == 1
    assert held[0].atm_iv == pytest.approx(11.5)


def test_history_comes_back_oldest_first(conn: sqlite3.Connection) -> None:
    base = snapshot_from(_chain(), "NSE:NIFTY50-INDEX")
    assert base is not None
    for day in ("2026-09-23", "2026-09-24", "2026-09-25"):
        save_vol_snapshot(
            conn, VolSnapshot(**{**base.__dict__, "day": datetime.fromisoformat(day).date()})
        )

    held = vol_history(conn, "NSE:NIFTY50-INDEX")

    assert [v.day.isoformat() for v in held] == ["2026-09-23", "2026-09-24", "2026-09-25"]


# --------------------------------------------------------------------------
# The recorder
# --------------------------------------------------------------------------


def test_nothing_is_recorded_while_the_exchange_is_shut(conn: sqlite3.Connection) -> None:
    """The chain does not move overnight, and a row written at midnight would be
    yesterday's close wearing today's date."""
    recorder = VolRecorder(
        underlyings=["NSE:NIFTY50-INDEX"],
        fetch_chain=lambda symbol, strikes: _chain(),
        open_conn=lambda: conn,
        in_session=lambda at: False,
    )

    import asyncio

    assert asyncio.run(recorder.tick()) == 0
    assert vol_history(conn, "NSE:NIFTY50-INDEX") == []


def test_a_pass_records_every_underlying(conn: sqlite3.Connection) -> None:
    recorder = VolRecorder(
        underlyings=["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"],
        fetch_chain=lambda symbol, strikes: _chain(),
        open_conn=lambda: conn,
        in_session=lambda at: True,
    )

    assert recorder._record() == 2


def test_one_symbol_failing_does_not_cost_the_pass(conn: sqlite3.Connection) -> None:
    """A broker that refuses one chain should not stop the other four being
    written, on a table that cannot be filled in afterwards."""

    def fetch(symbol: str, strikes: int) -> OptionChain:
        if symbol == "NSE:NIFTYBANK-INDEX":
            raise RuntimeError("no chain today")
        return _chain()

    recorder = VolRecorder(
        underlyings=["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "NSE:FINNIFTY-INDEX"],
        fetch_chain=fetch,
        open_conn=lambda: conn,
        in_session=lambda at: True,
    )

    assert recorder._record() == 2


def test_the_recorder_asks_for_a_handful_of_strikes_not_a_chain(
    conn: sqlite3.Connection,
) -> None:
    """Only the money is read here, and a whole chain is a much larger response
    for nothing."""
    asked: list[int] = []

    def fetch(symbol: str, strikes: int) -> OptionChain:
        asked.append(strikes)
        return _chain()

    VolRecorder(
        underlyings=["NSE:NIFTY50-INDEX"],
        fetch_chain=fetch,
        open_conn=lambda: conn,
        in_session=lambda at: True,
    )._record()

    assert asked == [2]
