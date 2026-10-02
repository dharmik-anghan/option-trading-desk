"""NSE's pre-open auction, into the desk's database.

    python scripts/preopen.py fetch                 # the latest session, from NSE
    python scripts/preopen.py fetch --key "NIFTY BANK" --key FO
    python scripts/preopen.py import ~/Downloads     # every pre-open CSV in a folder
    python scripts/preopen.py import FILE.csv --day 2026-09-22 --replace
    python scripts/preopen.py list

The desk records each session by itself while it is running (see
`storage/preopen_recorder.py`); this is for a day it was not running, and for
files downloaded from the page before any of this existed. NSE serves only the
latest session, so no older day can be fetched - a CSV is the only way back.

Safe to repeat. A file already imported is `unchanged`, one that is another
day's rows under the wrong date is `duplicate`, and one that disagrees with a
stored day is a `conflict` and left alone unless `--replace`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
import paths  # noqa: E402
from marketdata.nse_preopen import (  # noqa: E402
    DEFAULT_KEYS,
    KEYS,
    PreOpenError,
    fetch,
    parse_csv_name,
    read_csv,
)
from storage.db import connect, init_schema  # noqa: E402
from storage.preopen_repo import recorded_days, save_day  # noqa: E402


def _files(paths: list[Path]) -> list[Path]:
    """Every CSV named, and every pre-open CSV in every folder named, oldest day
    first and each original before the browser's `(1)` copy of it."""
    found: list[Path] = []
    for path in paths:
        path = path.expanduser()
        if path.is_dir():
            found.extend(path.glob("MW-Pre-Open-Market-*.csv"))
        else:
            found.append(path)

    def order(p: Path) -> tuple[date, int, str]:
        name = parse_csv_name(p.name)
        return (name.day, name.copy, p.name) if name else (date.max, 0, p.name)

    return sorted(set(found), key=order)


def _import(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    files = _files(args.paths)
    if not files:
        print("No pre-open CSVs found.")
        return 1
    failed = 0
    for path in files:
        try:
            session = read_csv(path, args.day)
        except (PreOpenError, OSError, ValueError) as exc:
            print(f"  {'error':10} {path.name}: {exc}")
            failed += 1
            continue
        result = save_day(conn, session, replace=args.replace)
        note = f"  ({result.detail})" if result.detail else ""
        print(f"  {result.outcome:10} {result.day}  {len(session.quotes):>4} rows  "
              f"{path.name}{note}")
    return 1 if failed else 0


def _fetch(conn: sqlite3.Connection, keys: tuple[str, ...]) -> int:
    try:
        session = fetch(keys)
    except PreOpenError as exc:
        print(f"NSE has no pre-open data for {', '.join(keys)}: {exc}")
        return 1
    result = save_day(conn, session)
    note = f"  ({result.detail})" if result.detail else ""
    print(f"{result.outcome}: {result.day}, {len(session.quotes)} stocks, "
          f"as of {session.as_of:%H:%M:%S}{note}")
    if session.index is not None:
        i = session.index
        print(f"{i.name} set to open at {i.price:,.2f} ({i.change:+,.2f}, {i.pct_change:+.2f}%)")
    return 0 if result.outcome != "unsettled" else 1


def _list(conn: sqlite3.Connection) -> int:
    days = recorded_days(conn)
    if not days:
        print("Nothing recorded yet.")
        return 0
    for d in days:
        as_of = f"{d.as_of:%H:%M:%S}" if d.as_of else "-"
        gap = f"  NIFTY {d.index.pct_change:+.2f}%" if d.index else ""
        print(f"  {d.day}  {d.day:%a}  {d.source:4}  {d.rows:>4} stocks  as of {as_of}{gap}")
    print(f"{len(days)} days")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path,
                        default=paths.db_path())
    sub = parser.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="the latest session, from NSE")
    f.add_argument("--key", dest="keys", action="append", choices=KEYS,
                   help=f"one of NSE's lists, repeatable (default: {', '.join(DEFAULT_KEYS)})")

    i = sub.add_parser("import", help="CSVs downloaded from the pre-open page")
    i.add_argument("paths", nargs="+", type=Path, help="files, or folders to search")
    i.add_argument("--day", type=date.fromisoformat,
                   help="the session date, when the file name does not have it")
    i.add_argument("--replace", action="store_true",
                   help="overwrite a stored day the file disagrees with")

    sub.add_parser("list", help="the days recorded")

    args = parser.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(args.db))
    init_schema(conn)
    try:
        if args.command == "fetch":
            return _fetch(conn, tuple(args.keys or DEFAULT_KEYS))
        if args.command == "import":
            return _import(conn, args)
        return _list(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
