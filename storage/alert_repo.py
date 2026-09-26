"""Persisting the alert engine's state.

Three things outlive a restart, and each for a different reason:

- the log, because it is what the desk shows;
- the active key set, because without it every still-true condition reads as a
  fresh transition and the whole board re-announces itself on every restart;
- whether an alert has been delivered, because a restart must not re-send a
  Telegram message that already went out.
"""

from __future__ import annotations

import sqlite3

from alerting.models import Alert, Direction, Limits, Severity, Watch, WatchKind


def load_log(conn: sqlite3.Connection, limit: int = 200) -> list[Alert]:
    """The most recent alerts, newest first."""
    rows = conn.execute(
        "SELECT key, severity, subject, message, at FROM alert_log "
        "ORDER BY at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        Alert(key=k, severity=Severity(sev), subject=subject, message=msg, at=at)
        for k, sev, subject, msg, at in rows
    ]


def append(conn: sqlite3.Connection, alerts: list[Alert]) -> None:
    if not alerts:
        return
    conn.executemany(
        "INSERT INTO alert_log (key, severity, subject, message, at) VALUES (?, ?, ?, ?, ?)",
        [(a.key, str(a.severity), a.subject, a.message, a.at) for a in alerts],
    )
    conn.commit()


def undelivered(conn: sqlite3.Connection) -> list[tuple[int, Alert]]:
    """Alerts that have not been sent anywhere yet, oldest first.

    Oldest first so a batch reads in the order things happened.
    """
    rows = conn.execute(
        "SELECT id, key, severity, subject, message, at FROM alert_log "
        "WHERE notified_at IS NULL ORDER BY at ASC, id ASC"
    ).fetchall()
    return [
        (row_id, Alert(key=k, severity=Severity(sev), subject=subject, message=msg, at=at))
        for row_id, k, sev, subject, msg, at in rows
    ]


def mark_delivered(conn: sqlite3.Connection, row_ids: list[int], at: int) -> None:
    if not row_ids:
        return
    conn.executemany(
        "UPDATE alert_log SET notified_at = ? WHERE id = ?",
        [(at, row_id) for row_id in row_ids],
    )
    conn.commit()


def clear_log(conn: sqlite3.Connection) -> None:
    """Empty the log, and forget which conditions were already announced.

    Both, deliberately. Keeping the active set would leave every still-true
    condition marked as already-alerted, so nothing would re-fire and the panel
    would sit empty while several were live - which is exactly the bug the
    browser version had. Clearing means "show me where things stand".
    """
    conn.execute("DELETE FROM alert_log")
    conn.execute("DELETE FROM alert_active")
    conn.commit()


def load_active(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(row[0] for row in conn.execute("SELECT key FROM alert_active"))


def save_active(conn: sqlite3.Connection, keys: frozenset[str], now: int) -> None:
    """Replace the active set, keeping `since` for keys that were already there."""
    existing = {row[0]: row[1] for row in conn.execute("SELECT key, since FROM alert_active")}
    conn.execute("DELETE FROM alert_active")
    conn.executemany(
        "INSERT INTO alert_active (key, since) VALUES (?, ?)",
        [(key, existing.get(key, now)) for key in sorted(keys)],
    )
    conn.commit()


def load_limits(conn: sqlite3.Connection) -> Limits:
    """The stored thresholds, or the defaults if none have been set."""
    row = conn.execute(
        "SELECT max_loss, short_delta, expiry_days FROM alert_limits WHERE id = 1"
    ).fetchone()
    if row is None:
        return Limits()
    return Limits(max_loss=row[0], short_delta=row[1], expiry_days=row[2])


def save_limits(conn: sqlite3.Connection, limits: Limits) -> None:
    # The target and daily_loss columns are still in the table and no longer read.
    # They are written as zero to satisfy their NOT NULL, rather than dropped: a
    # migration to delete two unused numbers buys nothing and rewrites a table
    # holding real history.
    conn.execute(
        "INSERT INTO alert_limits (id, target, daily_loss, max_loss, short_delta, expiry_days) "
        "VALUES (1, 0, 0, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET max_loss = excluded.max_loss, "
        "short_delta = excluded.short_delta, expiry_days = excluded.expiry_days",
        (limits.max_loss, limits.short_delta, limits.expiry_days),
    )
    conn.commit()


def list_watches(conn: sqlite3.Connection, enabled_only: bool = False) -> list[Watch]:
    """Every level you asked about, newest last."""
    sql = (
        "SELECT id, kind, symbol, direction, level, note, enabled FROM alert_watch"
        + (" WHERE enabled = 1" if enabled_only else "")
        + " ORDER BY id"
    )
    return [
        Watch(
            id=row[0],
            kind=WatchKind(row[1]),
            symbol=row[2],
            direction=Direction(row[3]),
            level=row[4],
            note=row[5],
            enabled=bool(row[6]),
        )
        for row in conn.execute(sql)
    ]


def add_watch(
    conn: sqlite3.Connection,
    kind: WatchKind,
    direction: Direction,
    level: float,
    symbol: str | None,
    note: str,
    created_at: str,
) -> Watch:
    cursor = conn.execute(
        "INSERT INTO alert_watch (kind, symbol, direction, level, note, enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (str(kind), symbol, str(direction), level, note, created_at),
    )
    conn.commit()
    watch_id = cursor.lastrowid
    assert watch_id is not None
    return Watch(
        id=watch_id, kind=kind, symbol=symbol, direction=direction, level=level, note=note
    )


def set_watch_enabled(conn: sqlite3.Connection, watch_id: int, enabled: bool) -> bool:
    """Turn one on or off. False if there is no such watch."""
    cursor = conn.execute(
        "UPDATE alert_watch SET enabled = ? WHERE id = ?", (1 if enabled else 0, watch_id)
    )
    conn.commit()
    return cursor.rowcount > 0


def delete_watch(conn: sqlite3.Connection, watch_id: int) -> bool:
    """Remove one. False if there is no such watch."""
    cursor = conn.execute("DELETE FROM alert_watch WHERE id = ?", (watch_id,))
    conn.commit()
    return cursor.rowcount > 0


def load_log_with_delivery(
    conn: sqlite3.Connection, limit: int = 200
) -> list[tuple[Alert, int | None]]:
    """The log, each alert paired with when it was delivered (or None).

    Held apart from `load_log` because the engine has no business knowing about
    delivery - that is the notifier's concern - while the desk does want to show
    which alerts actually left the building.
    """
    rows = conn.execute(
        "SELECT key, severity, subject, message, at, notified_at FROM alert_log "
        "ORDER BY at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        (Alert(key=k, severity=Severity(sev), subject=subject, message=msg, at=at), notified)
        for k, sev, subject, msg, at, notified in rows
    ]
