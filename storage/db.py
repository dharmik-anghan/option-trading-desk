"""SQLite connection + schema management.

One file, no server — matches the "lightweight" constraint from the stack
decision. `connect(":memory:")` is used in tests; real runs point at a path
like `data/trading.db` (gitignored, see .gitignore's `*.db`).
"""

from __future__ import annotations

import sqlite3

from storage.migrations import migrate

SCHEMA = """
CREATE TABLE IF NOT EXISTS option_chain_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying_symbol TEXT NOT NULL,
    underlying_ltp REAL NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS option_chain_row (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES option_chain_snapshot(id),
    symbol TEXT NOT NULL,
    strike REAL NOT NULL,
    option_type TEXT NOT NULL,
    ltp REAL NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    oi INTEGER NOT NULL,
    prev_oi INTEGER NOT NULL,
    volume INTEGER NOT NULL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    iv REAL
);

CREATE INDEX IF NOT EXISTS idx_snapshot_symbol_time
    ON option_chain_snapshot(underlying_symbol, fetched_at);

CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_position (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES portfolio_snapshot(id),
    symbol TEXT NOT NULL,
    net_quantity INTEGER NOT NULL,
    average_price REAL NOT NULL,
    ltp REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    product_type TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshot_time
    ON portfolio_snapshot(fetched_at);

CREATE TABLE IF NOT EXISTS basket (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    strategy TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    stop_loss REAL
);

CREATE TABLE IF NOT EXISTS basket_leg (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    basket_id INTEGER NOT NULL REFERENCES basket(id),
    symbol TEXT NOT NULL,
    option_type TEXT NOT NULL,
    strike REAL NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    entry_at TEXT NOT NULL,
    exit_price REAL,
    exit_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_basket_leg_basket ON basket_leg(basket_id);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create anything missing, then apply any schema changes still pending.

    Both halves, because `IF NOT EXISTS` brings a new database up to date but
    cannot alter one that already exists - see `storage/migrations.py`. Running
    them together means every entry point that opens the database gets a
    current schema without having to remember a second call.
    """
    conn.executescript(SCHEMA)
    conn.commit()
    migrate(conn)
