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


#: Ordered, append-only. Never edit a step that has shipped.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, reason="baseline: the schema init_schema creates", apply=_noop),
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
