"""SQLite connection + schema management.

One file, no server — matches the "lightweight" constraint from the stack
decision. `connect(":memory:")` is used in tests; real runs point at a path
like `data/trading.db` (gitignored, see .gitignore's `*.db`).
"""

from __future__ import annotations

import sqlite3

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
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
