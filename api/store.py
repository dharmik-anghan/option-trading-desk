"""Opening the database.

One place, because every router that touches storage needs the same three steps
- make the directory, connect, bring the schema up to date - and one that
forgets the third works fine until the day a migration is added.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from storage.db import connect, init_schema


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    init_schema(conn)
    return conn
