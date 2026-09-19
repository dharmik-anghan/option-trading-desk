"""Persistence for option-chain snapshots.

This is what will make "compare today's straddle premium to last week's"
possible later — every fetch gets a timestamped snapshot, queryable by
symbol and time range.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from broker.models import Greeks, OptionChain, OptionChainRow


def save_snapshot(conn: sqlite3.Connection, chain: OptionChain) -> int:
    cursor = conn.execute(
        "INSERT INTO option_chain_snapshot (underlying_symbol, underlying_ltp, fetched_at) "
        "VALUES (?, ?, ?)",
        (chain.underlying_symbol, chain.underlying_ltp, chain.fetched_at.isoformat()),
    )
    snapshot_id = cursor.lastrowid
    assert snapshot_id is not None

    conn.executemany(
        "INSERT INTO option_chain_row "
        "(snapshot_id, symbol, strike, option_type, ltp, bid, ask, oi, prev_oi, volume, "
        " delta, gamma, theta, vega, iv) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                snapshot_id,
                row.symbol,
                row.strike,
                row.option_type,
                row.ltp,
                row.bid,
                row.ask,
                row.oi,
                row.prev_oi,
                row.volume,
                row.greeks.delta if row.greeks else None,
                row.greeks.gamma if row.greeks else None,
                row.greeks.theta if row.greeks else None,
                row.greeks.vega if row.greeks else None,
                row.greeks.iv if row.greeks else None,
            )
            for row in chain.rows
        ],
    )
    conn.commit()
    return snapshot_id


def snapshots_for_symbol(conn: sqlite3.Connection, underlying_symbol: str) -> list[OptionChain]:
    snapshot_rows = conn.execute(
        "SELECT id, underlying_symbol, underlying_ltp, fetched_at "
        "FROM option_chain_snapshot WHERE underlying_symbol = ? ORDER BY fetched_at ASC",
        (underlying_symbol,),
    ).fetchall()

    results: list[OptionChain] = []
    for snapshot_id, symbol, underlying_ltp, fetched_at in snapshot_rows:
        option_rows = conn.execute(
            "SELECT symbol, strike, option_type, ltp, bid, ask, oi, prev_oi, volume, "
            "       delta, gamma, theta, vega, iv "
            "FROM option_chain_row WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()

        rows = [
            OptionChainRow(
                symbol=r[0],
                strike=r[1],
                option_type=r[2],
                ltp=r[3],
                bid=r[4],
                ask=r[5],
                oi=r[6],
                prev_oi=r[7],
                volume=r[8],
                greeks=(
                    Greeks(delta=r[9], gamma=r[10], theta=r[11], vega=r[12], iv=r[13])
                    if r[9] is not None
                    else None
                ),
            )
            for r in option_rows
        ]

        results.append(
            OptionChain(
                underlying_symbol=symbol,
                underlying_ltp=underlying_ltp,
                fetched_at=datetime.fromisoformat(fetched_at),
                rows=rows,
            )
        )
    return results
