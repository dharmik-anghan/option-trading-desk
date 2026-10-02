"""Fetch every settled option contract's one-minute history from Fyers.

    python scripts/backfill_options.py                          # NIFTY, last 4 years, +-10%
    python scripts/backfill_options.py --underlying SENSEX
    python scripts/backfill_options.py --band 0                 # every strike
    python scripts/backfill_options.py --since 2025-01-01 --dry-run

Safe to interrupt and safe to repeat: a contract is ledgered in the same
transaction as its bars, and a ledgered contract is never asked for again. Rerun it
after an expiry and it fetches that expiry and nothing else.

Writes to `data/options.duckdb`, not the desk's `bars.duckdb`, so the app can keep
running. See optbt/data/ for what is stored and why.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fyers_apiv3 import fyersModel  # noqa: E402

import paths  # noqa: E402
from broker.fyers.expired import FyersExpired  # noqa: E402
from broker.fyers.token_store import get_access_token  # noqa: E402
from optbt.data.backfill import backfill  # noqa: E402
from optbt.data.store import OptionStore  # noqa: E402
from settings import load_settings  # noqa: E402
from venues.instruments import OPTION_SERIES  # noqa: E402

DEFAULT_STORE = paths.options_store_path()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--underlying", default="NIFTY", choices=sorted(OPTION_SERIES))
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=date.today() - timedelta(days=4 * 365),
        help="earliest expiry to fetch (default: four years ago)",
    )
    parser.add_argument(
        "--band",
        type=float,
        default=0.10,
        help="keep strikes within this fraction of the index's range; 0 keeps all",
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument(
        "--dry-run", action="store_true", help="list what would be fetched, fetch no bars"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    settings = load_settings()
    log_dir = paths.LOGS_DIR
    log_dir.mkdir(exist_ok=True)

    def connect(force: bool = False) -> Any:
        return fyersModel.FyersModel(
            client_id=settings.fyers_client_id,
            token=get_access_token(settings, force=force),
            is_async=False,
            log_path=str(log_dir),
        )

    source = FyersExpired(connect(), renew=lambda: connect(force=True))
    store = OptionStore(args.store)

    started = time.monotonic()
    total_bars = total_contracts = pending = 0
    failed: list[str] = []
    try:
        for report in backfill(
            store,
            source,
            args.underlying,
            since=args.since,
            until=date.today(),
            band=args.band,
            dry_run=args.dry_run,
        ):
            todo = report.wanted - report.already_held
            pending += todo
            total_contracts += report.fetched
            total_bars += report.bars
            failed.extend(report.failed)
            elapsed = time.monotonic() - started
            print(
                f"{report.expiry}  listed {report.listed:4d}  in band {report.wanted:4d}  "
                f"held {report.already_held:4d}  "
                + (
                    f"to fetch {todo:4d}"
                    if args.dry_run
                    else f"fetched {report.fetched:4d}  bars {report.bars:>9,}"
                    + (f"  FAILED {len(report.failed)}" if report.failed else "")
                )
                + f"   [{source.requests:,} requests, {elapsed / 60:.1f} min]",
                flush=True,
            )
    except KeyboardInterrupt:
        print("\ninterrupted - everything fetched so far is kept; rerun to continue")
    finally:
        if args.dry_run:
            # About one request per contract: a weekly's life fits in one window.
            print(f"\n{pending:,} contracts to fetch, about {pending / 180:.0f} minutes")
        else:
            print(f"\n{total_contracts:,} contracts, {total_bars:,} bars written")
            if failed:
                print(f"{len(failed)} failed and will be retried next run, e.g. {failed[:3]}")
        for underlying, contracts, bars, expiries in store.summary():
            print(f"store: {underlying} {contracts:,} contracts, {bars:,} bars, "
                  f"{expiries} expiries")
        store.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
