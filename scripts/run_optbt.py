"""Run an options backtest from the command line.

    python scripts/run_optbt.py straddle --from 2025-01-01 --to 2025-12-31
    python scripts/run_optbt.py straddle --stop 0.3 --trail --show 5

Reads `data/options.duckdb`, read-only - but DuckDB will not open a file another
process is writing, so while a backfill runs, point --store at a copy.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import paths  # noqa: E402
from optbt.engine import Engine  # noqa: E402
from optbt.market import History  # noqa: E402
from optbt.results import report, summarise  # noqa: E402
from optbt.strategies.straddle import Straddle, StraddleConfig  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("strategy", choices=["straddle"])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2022, 1, 1))
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--store", type=Path, default=paths.options_store_path())
    parser.add_argument("--entry", type=time.fromisoformat, default=time(9, 20))
    parser.add_argument("--exit", type=time.fromisoformat, default=time(15, 15))
    parser.add_argument("--stop", type=float, default=0.25, help="per-leg stop, 0.25 = 25%%")
    parser.add_argument("--lots", type=int, default=1)
    parser.add_argument("--trail", action="store_true", help="move the other leg's stop to cost")
    parser.add_argument("--show", type=int, default=0, help="print the replay of the last N trades")
    args = parser.parse_args()

    history = History.open(args.store)
    config = StraddleConfig(
        entry=args.entry,
        exit=args.exit,
        stop_pct=args.stop,
        lots=args.lots,
        trail_to_cost=args.trail,
    )
    result = Engine(history, Straddle(config)).run(args.start, args.end)
    print(f"{args.strategy} {config}\n{result.days} trading days\n")
    print(report(summarise(result)))
    if result.skipped:
        print("not traded    " + ", ".join(f"{n} days: {why}" for why, n in result.skipped.items()))
    for trade in result.trades[-args.show :] if args.show else []:
        print(f"\ntrade {trade.id}: net {trade.net:+,.0f}")
        for event in trade.events:
            print(f"  {event}")
    history.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
