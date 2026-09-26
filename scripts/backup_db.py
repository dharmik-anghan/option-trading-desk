"""Periodic backups of the SQLite database.

Uses SQLite's own online backup, which is why this can run against a database
the desk is actively writing to: it copies pages under the engine's lock and
produces a consistent file, where `cp` on a live database can capture a torn
write or miss the write-ahead log.

Standard library only, and no scheduler: a loop with a sleep is enough for
something that runs every few hours, and it keeps the image free of another
dependency.

    python scripts/backup_db.py             # one backup, then exit
    python scripts/backup_db.py --loop      # forever, on BACKUP_EVERY_HOURS
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data" / "trading.db"
DEFAULT_DEST = REPO_ROOT / "data" / "backups"


def backup_once(db_path: Path, dest_dir: Path, keep: int) -> Path | None:
    """Write one timestamped copy and prune the oldest. Returns the new file.

    Returns None when there is no database yet, which is the normal state
    before the first trade is recorded and not worth failing over.
    """
    if not db_path.exists():
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = dest_dir / f"trading-{stamp}.db"

    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        written = sqlite3.connect(target)
        try:
            source.backup(written)
        finally:
            written.close()
    finally:
        source.close()

    _prune(dest_dir, keep)
    return target


def _prune(dest_dir: Path, keep: int) -> None:
    """Keep the newest `keep` backups. Named by UTC timestamp, so sorting by
    name is sorting by age."""
    if keep <= 0:
        return
    existing = sorted(dest_dir.glob("trading-*.db"))
    for stale in existing[:-keep]:
        stale.unlink(missing_ok=True)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("DB_PATH", DEFAULT_DB)))
    parser.add_argument(
        "--dest", type=Path, default=Path(os.environ.get("BACKUP_DIR", DEFAULT_DEST))
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=int(os.environ.get("BACKUP_KEEP", 24)),
        help="how many backups to retain (default 24)",
    )
    parser.add_argument("--loop", action="store_true", help="keep running")
    parser.add_argument(
        "--every-hours",
        type=float,
        default=_env_float("BACKUP_EVERY_HOURS", 4.0),
        help="hours between backups when looping (default 4)",
    )
    args = parser.parse_args()

    while True:
        written = backup_once(args.db, args.dest, args.keep)
        when = datetime.now(UTC).isoformat(timespec="seconds")
        if written is None:
            print(f"{when} no database at {args.db} yet, nothing to back up", flush=True)
        else:
            print(f"{when} backed up to {written} ({written.stat().st_size} bytes)", flush=True)
        if not args.loop:
            return 0
        time.sleep(max(60.0, args.every_hours * 3600))


if __name__ == "__main__":
    sys.exit(main())
