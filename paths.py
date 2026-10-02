"""Where the desk keeps its files.

One place, because the database path was written out in five modules and the
stores in four, and a script that disagrees with the app about where the data
lives looks like it worked while writing somewhere nobody reads.

Plain functions over the environment rather than fields on `Settings`, so a
script that only needs a path does not need broker credentials to get one.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
LOGS_DIR = REPO_ROOT / "logs"
BACKUPS_DIR = DATA_DIR / "backups"
CONSTITUENTS = DATA_DIR / "constituents.json"
ENV_FILE = REPO_ROOT / ".env"


def db_path() -> Path:
    """The desk's SQLite database. `DB_PATH` moves it - a copy, or a test's scratch file."""
    return Path(os.environ.get("DB_PATH", DATA_DIR / "trading.db"))


def bars_path() -> Path:
    """The bar store, beside the database so moving one moves both."""
    return db_path().parent / "bars.duckdb"


def options_store_path() -> Path:
    """The option history store. `OPTBT_STORE` points at a different file - a copy
    taken while a backfill holds the real one."""
    return Path(os.environ.get("OPTBT_STORE", DATA_DIR / "options.duckdb"))
