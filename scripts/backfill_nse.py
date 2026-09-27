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
from universe.nse import INDICES, fyers_symbol, load  # noqa: E402

DEFAULT_STORE = REPO_ROOT / "data" / "bars.duckdb"
SOURCE = "fyers"

#: Days per request. Fyers serves a year of daily bars at a time; asking for
#: more returns an error rather than a truncated answer.
WINDOW_DAYS = 360

#: Between requests. The published budget is ten a second and two hundred a
#: minute, and this is nowhere near either - it is about not being the reason
#: the desk beside it gets rate limited.
BETWEEN = 0.2


def wanted() -> list[tuple[str, str]]:
    """Every series to fetch, as (label, symbol), indices first.

    Indices first because they are the benchmarks: a run interrupted early
    leaves the graph able to draw sectors against Nifty even with no stocks.
    """
    out: list[tuple[str, str]] = [(spec.id, spec.symbol) for spec in INDICES]
    seen = {symbol for _, symbol in out}
    for membership in load().values():
        for member in membership.members:
            symbol = fyers_symbol(member)
            if symbol not in seen:
                seen.add(symbol)
                out.append((member.symbol, symbol))
    return out


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="a single symbol, for checking one")
    args = parser.parse_args()

    series = wanted()
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
