"""Fill the bar store with enough history to backtest on.

The desk's own sources serve a chart: Shark gives a few hundred bars, and the
read-through cache tops them up as you use it. A backtest cannot wait for that, so
this walks a series back as far as the source will go and writes it to DuckDB.

    python scripts/backfill_bars.py BTCUSDT 5m --days 1095
    python scripts/backfill_bars.py BTCUSDT 1h --days 1825
    python scripts/backfill_bars.py BTCUSDT 5m --days 1095 --dry-run

Binance only, for now: it is the one source with the depth, and gold and oil have
no free equivalent. Safe to interrupt and safe to repeat - every page is written
before the next is asked for, and the store replaces a bar rather than doubling it.

Writes to `data/bars.duckdb`, which DuckDB allows one process to open. Stop the app
before running this.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from marketdata.backfill import backfill, resume_from  # noqa: E402
from marketdata.binance import BinanceBars, binance_symbol  # noqa: E402
from marketdata.models import Interval, Series  # noqa: E402
from marketdata.store import BarStore  # noqa: E402

DEFAULT_STORE = REPO_ROOT / "data" / "bars.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="as the desk spells it, e.g. BTCUSDT")
    parser.add_argument("interval", help="one of 1m 5m 15m 30m 1h 4h 1d")
    parser.add_argument("--days", type=int, default=1095, help="how far back (default 3 years)")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument(
        "--restart",
        action="store_true",
        help="fetch the whole window again instead of continuing from what is held",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="say what would be fetched and how many requests it takes, then stop",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        interval = Interval(args.interval)
    except ValueError:
        print(f"{args.interval} is not a bar size. One of: {', '.join(Interval)}")
        return 2

    mapped = binance_symbol(args.symbol)
    if mapped is None:
        print(f"Binance is not mapped for {args.symbol} (see marketdata/binance.py)")
        return 2

    series = Series(source="binance", symbol=args.symbol, interval=interval)
    wanted_start = datetime.now(UTC) - timedelta(days=args.days)

    try:
        store = BarStore(args.store)
    except duckdb.IOException:
        # DuckDB allows one process to write a database file, so this is what a
        # running desk looks like from here. Worth naming, because the raw error
        # is four frames of stack ending in "conflicting lock".
        print(f"{args.store.name} is open in another process - stop the desk first.")
        return 1

    try:
        held = store.count(series)
        start = wanted_start if args.restart else resume_from(store, series, wanted_start)
        bars_wanted = int((datetime.now(UTC) - start).total_seconds() / interval.seconds)
        pages = max(1, bars_wanted // 1000 + 1)

        print(f"{series.symbol} {interval} from {start:%Y-%m-%d %H:%M} UTC")
        print(f"  held already: {held:,} bars")
        print(f"  to fetch:     about {bars_wanted:,} bars in {pages:,} requests")
        if args.dry_run:
            return 0

        for progress in backfill(store, BinanceBars(), series, mapped, start):
            print(
                f"  page {progress.page:>4}/{pages:<4} "
                f"{progress.written:>9,} bars  through {progress.through:%Y-%m-%d %H:%M}",
                flush=True,
            )

        span = store.span(series)
        total = store.count(series)
        print(f"\n{total:,} bars held", end="")
        print(f", {span[0]:%Y-%m-%d} to {span[1]:%Y-%m-%d}" if span else "")
    except KeyboardInterrupt:
        # Not a failure. Every page fetched is already on disk, and the next run
        # carries on from it.
        print("\nStopped. What was fetched is kept; run again to continue.")
        return 130
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
