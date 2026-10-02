"""Reading and writing the daily record of what options cost.

One row per underlying per day. The writer runs on a loop and replaces the day's
row each pass, so the last reading before the close is the one that survives -
which is the reading anyone comparing days would want.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from analytics.vol_snapshot import VolSnapshot


def save_vol_snapshot(conn: sqlite3.Connection, snapshot: VolSnapshot) -> None:
    """Record one reading, replacing any earlier one from the same day."""
    conn.execute(
        """
        INSERT INTO vol_snapshot (
            underlying, day, at, spot, expiry, days_to_expiry, atm_strike,
            call_iv, put_iv, atm_iv, straddle, india_vix
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(underlying, day) DO UPDATE SET
            at = excluded.at,
            spot = excluded.spot,
            expiry = excluded.expiry,
            days_to_expiry = excluded.days_to_expiry,
            atm_strike = excluded.atm_strike,
            call_iv = excluded.call_iv,
            put_iv = excluded.put_iv,
            atm_iv = excluded.atm_iv,
            straddle = excluded.straddle,
            india_vix = excluded.india_vix
        """,
        (
            snapshot.underlying,
            snapshot.day.isoformat(),
            snapshot.at.isoformat(),
            snapshot.spot,
            snapshot.expiry,
            snapshot.days_to_expiry,
            snapshot.atm_strike,
            snapshot.call_iv,
            snapshot.put_iv,
            snapshot.atm_iv,
            snapshot.straddle,
            snapshot.india_vix,
        ),
    )
    conn.commit()


def vol_history(
    conn: sqlite3.Connection, underlying: str, *, days: int = 400
) -> list[VolSnapshot]:
    """Every reading held for one underlying, oldest first."""
    rows = conn.execute(
        """
        SELECT underlying, day, at, spot, expiry, days_to_expiry, atm_strike,
               call_iv, put_iv, atm_iv, straddle, india_vix
        FROM vol_snapshot
        WHERE underlying = ?
        ORDER BY day DESC
        LIMIT ?
        """,
        (underlying, days),
    ).fetchall()
    return [
        VolSnapshot(
            underlying=row[0],
            day=date.fromisoformat(row[1]),
            at=datetime.fromisoformat(row[2]),
            spot=row[3],
            expiry=row[4],
            days_to_expiry=row[5],
            atm_strike=row[6],
            call_iv=row[7],
            put_iv=row[8],
            atm_iv=row[9],
            straddle=row[10],
            india_vix=row[11],
        )
        for row in reversed(rows)
    ]
