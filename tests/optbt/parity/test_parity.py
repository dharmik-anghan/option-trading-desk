"""Every parity case reproduces its recorded result exactly.

These are the reference any second engine has to match - the Python one first,
since it is what the expected results were recorded from. A failure here after
a deliberate change to the engine means: read the diff, then rewrite with
`python -m tests.optbt.parity.run --write` and commit the new expected/ with it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from optbt.data.store import SCHEMA
from tests.optbt.parity.market import build, export_parquet
from tests.optbt.parity.run import CASES, expected_path, run_case


@pytest.fixture(scope="module")
def market() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = build()
    yield conn
    conn.close()


@pytest.mark.parametrize("case", CASES, ids=[c.stem for c in CASES])
def test_the_engine_reproduces_the_recorded_result(
    market: duckdb.DuckDBPyConnection, case: Path
) -> None:
    expected = json.loads(expected_path(case).read_text())
    assert run_case(market, case) == expected


def test_there_is_a_recorded_result_for_every_case_and_nothing_else() -> None:
    recorded = {p.name for p in (Path(__file__).parent / "expected").glob("*.json")}
    assert recorded == {c.name for c in CASES}


def test_the_parquet_export_is_the_whole_market(
    market: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """What another engine is handed carries everything: a store rebuilt from the
    files alone gives the same result as the one they came from."""
    export_parquet(market, tmp_path)
    rebuilt = duckdb.connect()
    rebuilt.execute(SCHEMA)
    for table in ("expiry", "index_bar", "option_bar"):
        rebuilt.execute(f"INSERT INTO {table} SELECT * FROM '{tmp_path / table}.parquet'")
    case = CASES[0]
    assert run_case(rebuilt, case) == json.loads(expected_path(case).read_text())
