"""Versioned schema changes.

`init_schema` creates tables with `IF NOT EXISTS`, which is enough to bring a
new database up to date and useless for changing one that already holds data:
adding a column, relaxing a constraint or renaming a table all need a
statement that runs exactly once, in order, against a database that may be
several versions behind.

The version lives in SQLite's own `user_version` pragma rather than a table of
our own. It costs no schema, it is already atomic, and it cannot drift out of
step with the file it describes.

Each migration is a numbered step with a short reason. Steps only ever get
appended - editing one that has already run somewhere means databases disagree
about what version 3 was, which is the failure mode this design exists to
avoid. To change something, add another step.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    """One irreversible step from `version - 1` to `version`."""

    version: int
    #: Why it exists, for whoever reads the list in two years.
    reason: str
    apply: Callable[[sqlite3.Connection], None]


def _noop(conn: sqlite3.Connection) -> None:
    """Baseline. Everything `init_schema` creates is version 1 by definition."""


def _alert_state(conn: sqlite3.Connection) -> None:
    """Somewhere for the alert engine to keep its log and its active keys.

    The engine ran in the browser and kept both in `localStorage`, which is per
    origin, dies with the tab, and cannot be read by anything that sends a
    Telegram message. Moving it here is what lets alerts fire while nothing is
    open - which is the only way a 24/7 market can be watched at all.

    `alert_active` is the set of conditions currently true. It is not a log and
    has no history: it exists so that a restart does not read every still-true
    condition as a fresh transition and re-announce all of them.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            severity TEXT NOT NULL,
            subject TEXT,
            message TEXT NOT NULL,
            -- epoch milliseconds, matching the frontend's clock so one log can
            -- hold entries written by either engine
            at INTEGER NOT NULL,
            -- whether this one has been delivered, so a restart does not send
            -- the same Telegram message twice
            notified_at INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_alert_log_at ON alert_log(at);
        CREATE INDEX IF NOT EXISTS idx_alert_log_key_at ON alert_log(key, at);

        CREATE TABLE IF NOT EXISTS alert_active (
            key TEXT PRIMARY KEY,
            since INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alert_limits (
            -- one row, so the thresholds are a value rather than a history
            id INTEGER PRIMARY KEY CHECK (id = 1),
            target REAL NOT NULL,
            daily_loss REAL NOT NULL,
            max_loss REAL NOT NULL,
            short_delta REAL NOT NULL,
            expiry_days REAL NOT NULL
        );
    """)


def _alert_watches(conn: sqlite3.Connection) -> None:
    """Levels you asked to be told about.

    Everything the engine raised until now was derived - a delta crossing a
    threshold, an event inside an expiry. These are the opposite: an arbitrary
    line you drew yourself, on a price or on the book's P&L, which nothing in the
    data suggests on its own.

    The level is part of the alert's key rather than just a column, so moving a
    line makes a new condition that can fire again. Editing 24,000 to 24,500 and
    having it stay quiet because "that alert already fired" would be wrong.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS alert_watch (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- 'price' (needs a symbol) or 'pnl' (the book as a whole)
            kind TEXT NOT NULL CHECK (kind IN ('price', 'pnl')),
            symbol TEXT,
            direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
            level REAL NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            -- a price watch without a symbol has nothing to watch
            CHECK (kind = 'pnl' OR symbol IS NOT NULL)
        );

        CREATE INDEX IF NOT EXISTS idx_alert_watch_enabled ON alert_watch(enabled);
    """)


def _basket_alert_levels(conn: sqlite3.Connection) -> None:
    """Alert levels that belong to one structure rather than to the account.

    Three of the five thresholds were account-wide settings applied to every
    structure, which is the wrong shape: a condor's acceptable delta is not a
    calendar's, and "worst case past your limit" means a different number for
    each. Worse, one of them - a profit target - only ever measured the whole
    Fyers account, so there was no way to ask about the profit on one basket.

    `stop_loss` already existed on `basket` for exactly this purpose and nothing
    ever alerted on it. These two join it, and all three are nullable: null means
    no level set, not a level of zero.
    """
    conn.executescript("""
        ALTER TABLE basket ADD COLUMN profit_target REAL;
        ALTER TABLE basket ADD COLUMN delta_limit REAL;
    """)


def _basket_thresholds(conn: sqlite3.Connection) -> None:
    """The last three thresholds move onto the structure too.

    A worst case, the delta a short counts as tested at, and how many days before
    expiry to warn were left as account-wide numbers when the rest moved - and
    then, when the account panel went, as numbers nothing could edit. Both are
    wrong for the same reason: a condor's tested-short delta is not a strangle's,
    and a warning three days before expiry suits a weekly and not a quarterly.

    Nullable, and null means "use the default". A structure recorded before this
    existed keeps behaving exactly as it did, which is the point of an override
    rather than a required field.
    """
    conn.executescript("""
        ALTER TABLE basket ADD COLUMN worst_case_limit REAL;
        ALTER TABLE basket ADD COLUMN short_delta_limit REAL;
        ALTER TABLE basket ADD COLUMN expiry_warn_days REAL;
    """)


#: Ordered, append-only. Never edit a step that has shipped.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, reason="baseline: the schema init_schema creates", apply=_noop),
    Migration(version=2, reason="alert log, active keys and limits move server-side",
              apply=_alert_state),
    Migration(version=3, reason="price and P&L levels you ask to be told about",
              apply=_alert_watches),
    Migration(version=4, reason="per-structure profit target and delta limit",
              apply=_basket_alert_levels),
    Migration(version=5, reason="per-structure worst case, short delta and expiry warning",
              apply=_basket_thresholds),
)


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def latest_version() -> int:
    return max((m.version for m in MIGRATIONS), default=0)


def pending(conn: sqlite3.Connection) -> list[Migration]:
    """Steps this database has not run yet, in order."""
    at = schema_version(conn)
    return sorted((m for m in MIGRATIONS if m.version > at), key=lambda m: m.version)


def migrate(conn: sqlite3.Connection) -> list[Migration]:
    """Bring the database up to date, returning what ran.

    Each step and the version bump commit together. A step that raises leaves
    the version where it was, so the next run retries that step rather than
    skipping it and reporting a version the file does not actually have.

    `user_version` takes no parameters - it is a pragma, not a statement - so
    the number is formatted in. It comes from our own migration list, never
    from a caller.
    """
    ran: list[Migration] = []
    for step in pending(conn):
        try:
            step.apply(conn)
            conn.execute(f"PRAGMA user_version = {int(step.version)}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        ran.append(step)
    return ran
