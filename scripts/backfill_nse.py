"""Daily bars for the Indian indices and their constituents.

    python scripts/backfill_nse.py --dry-run
    python scripts/backfill_nse.py --years 3

What a rotation graph needs: every index in `universe/`, and every stock in
every index's constituent list. Run `scripts/fetch_constituents.py` first, or
this has only the indices to fetch.

Stored under the source "fyers", keyed by the venue's own symbol, so these sit
beside the crypto series in the same store and nothing has to know they are
different. Fyers is the desk's Indian source and the instrument the options desk
trades, which is the same reason the crypto chart uses Shark's own candles.

Safe to interrupt and safe to repeat: each symbol is written as it arrives, and
the store replaces a bar rather than doubling it.

Writes to `data/bars.duckdb`, which DuckDB allows one process to open. Stop the
desk before running this.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import duckdb  # noqa: E402

from broker.errors import BrokerError  # noqa: E402
from broker.fyers import FyersBroker  # noqa: E402
from broker.token_store import get_access_token  # noqa: E402
from marketdata.models import Bar, Interval, Series  # noqa: E402
from marketdata.store import BarStore  # noqa: E402
from settings import load_settings  # noqa: E402
from universe.nse import daily_series  # noqa: E402
from venues import OPTION_UNDERLYINGS  # noqa: E402

DEFAULT_STORE = REPO_ROOT / "data" / "bars.duckdb"
SOURCE = "fyers"

#: Days per request. Fyers serves a year of daily bars at a time; asking for
#: more returns an error rather than a truncated answer.
WINDOW_DAYS = 360

#: Between requests. The published budget is ten a second and two hundred a
#: minute, and this is nowhere near either - it is about not being the reason
#: the desk beside it gets rate limited.
BETWEEN = 0.2


def fetch_symbol(broker: FyersBroker, symbol: str, years: int) -> list[Bar]:
    """A symbol's daily bars, a year of window at a time, oldest first."""
    end = date.today()
    start = end - timedelta(days=365 * years)
    bars: list[Bar] = []
    at = start
    while at < end:
        until = min(at + timedelta(days=WINDOW_DAYS), end)
        try:
            rows = broker.get_history(symbol, "D", at, until)
        except BrokerError:
            # One window missing costs that window, not the symbol.
            rows = []
        bars.extend(
            Bar(ts=r.timestamp, open=r.open, high=r.high, low=r.low, close=r.close,
                volume=r.volume)
            for r in rows
        )
        at = until + timedelta(days=1)
        time.sleep(BETWEEN)
    return bars


#: Days of intraday history per request. Fyers serves a hundred at a time for
#: any intraday resolution and answers an error rather than a short window if
#: you ask for more.
INTRADAY_WINDOW = 100


def _intraday(store_path: Path, years: int, *, dry_run: bool) -> int:
    """Fifteen-minute bars for the option underlyings.

    Only the five indices, and only the smallest size: everything above it -
    the hour, the four hour - is resampled from these, for the same reason
    every other higher timeframe here is. Two fetched series can disagree about
    a boundary and nothing would show it.

    A year is about 17,000 fifteen-minute bars and a hundred days a request, so
    this is a handful of calls rather than the thousand the daily backfill made.
    """
    days = min(365 * years, 400)
    requests = (days // INTRADAY_WINDOW + 1) * len(OPTION_UNDERLYINGS)
    print(
        f"{len(OPTION_UNDERLYINGS)} underlyings, {days} days of 15m, "
        f"about {requests} requests"
    )
    if dry_run:
        for symbol, name in OPTION_UNDERLYINGS:
            print(f"  {name:16} {symbol}")
        return 0

    try:
        store = BarStore(store_path)
    except duckdb.IOException:
        print(f"{store_path.name} is open in another process - stop the desk first.")
        return 1

    settings = load_settings()
    broker = FyersBroker(
        client_id=settings.fyers_client_id, access_token=get_access_token(settings)
    )
    written = 0
    try:
        for n, (symbol, name) in enumerate(OPTION_UNDERLYINGS, start=1):
            bars: list[Bar] = []
            end = date.today()
            while end > date.today() - timedelta(days=days):
                earliest = date.today() - timedelta(days=days)
                start = max(end - timedelta(days=INTRADAY_WINDOW), earliest)
                try:
                    rows = broker.get_history(symbol, "15", start, end)
                except BrokerError:
                    rows = []
                bars.extend(
                    Bar(ts=r.timestamp, open=r.open, high=r.high, low=r.low, close=r.close,
                        volume=r.volume)
                    for r in rows
                )
                end = start - timedelta(days=1)
                time.sleep(BETWEEN)
            if bars:
                written += store.write(Series(SOURCE, symbol, Interval.M15), bars)
            print(f"  {n}/{len(OPTION_UNDERLYINGS)}  {name:16} {len(bars):>6,} bars", flush=True)
    except KeyboardInterrupt:
        print("\nStopped. What was fetched is kept; run again to continue.")
        return 130
    finally:
        store.close()
    print(f"\n{written:,} fifteen-minute bars stored")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="a single symbol, for checking one")
    parser.add_argument(
        "--intraday",
        action="store_true",
        help="fifteen-minute bars for the option underlyings instead of daily ones",
    )
    args = parser.parse_args()

    if args.intraday:
        return _intraday(args.store, args.years, dry_run=args.dry_run)

    series = daily_series()
    if args.only:
        series = [(label, symbol) for label, symbol in series if args.only in symbol]

    windows = 365 * args.years // WINDOW_DAYS + 1
    print(f"{len(series)} series, {args.years} years, about {len(series) * windows} requests")
    if args.dry_run:
        for label, symbol in series[:5]:
            print(f"  {label:18} {symbol}")
        print(f"  ... and {max(0, len(series) - 5)} more")
        return 0

    try:
        store = BarStore(args.store)
    except duckdb.IOException:
        print(f"{args.store.name} is open in another process - stop the desk first.")
        return 1

    settings = load_settings()
    broker = FyersBroker(
        client_id=settings.fyers_client_id, access_token=get_access_token(settings)
    )
    written = 0
    empty: list[str] = []
    try:
        for n, (label, symbol) in enumerate(series, start=1):
            bars = fetch_symbol(broker, symbol, args.years)
            if not bars:
                empty.append(symbol)
            else:
                written += store.write(Series(SOURCE, symbol, Interval.D1), bars)
            print(
                f"  {n:>3}/{len(series)}  {label:18} {len(bars):>5} bars  "
                f"{written:>8,} total",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\nStopped. What was fetched is kept; run again to continue.")
        return 130
    finally:
        store.close()

    print(f"\n{written:,} bars stored across {len(series) - len(empty)} series")
    if empty:
        print(f"No data for {len(empty)}: {', '.join(empty[:10])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
