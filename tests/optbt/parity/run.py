"""Run a parity case, and regenerate the expected results when behaviour changes on purpose.

    uv run python -m tests.optbt.parity.run            # check every case
    uv run python -m tests.optbt.parity.run --write    # rewrite expected/ from this engine
    uv run python -m tests.optbt.parity.run --parquet DIR   # the market, for another engine

Rewriting is a decision, not a fix: the diff in expected/ is the behaviour change,
and it should be read before it is committed.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from optbt.data.history import History
from optbt.engine import Engine
from optbt.spec import from_dict
from optbt.strategies.legs import LegStrategy
from tests.optbt.parity.canonical import canonical
from tests.optbt.parity.market import build, export_parquet

HERE = Path(__file__).parent
CASES = sorted((HERE / "cases").glob("*.json"))


def run_case(conn: duckdb.DuckDBPyConnection, case: Path) -> dict[str, Any]:
    """One case through the Python engine, as canonical data."""
    raw = json.loads(case.read_text())
    strategy = LegStrategy(from_dict(raw["spec"]))
    result = Engine(History(conn), strategy).run(
        date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"])
    )
    # Through JSON, so what is compared is exactly what is written.
    loaded: dict[str, Any] = json.loads(json.dumps(canonical(result)))
    return loaded


def expected_path(case: Path) -> Path:
    return HERE / "expected" / case.name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--parquet", type=Path)
    args = parser.parse_args()
    conn = build()
    if args.parquet:
        export_parquet(conn, args.parquet)
        print(f"market written to {args.parquet}")
        return 0
    failed = 0
    for case in CASES:
        got = run_case(conn, case)
        target = expected_path(case)
        if args.write:
            target.write_text(json.dumps(got, indent=1) + "\n")
            print(f"{case.stem}: {len(got['trades'])} trades, skipped {got['skipped']}")
        elif json.loads(target.read_text()) != got:
            failed += 1
            print(f"{case.stem}: DIFFERS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
