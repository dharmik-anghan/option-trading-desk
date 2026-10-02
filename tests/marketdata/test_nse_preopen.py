"""Reading NSE's pre-open, from its API and from the file its page saves.

The fixtures are the same two stocks on the same morning from both, cut from
real responses: DRREDDY, which moved, and JIOFIN, which the file writes as
"-" for no change.
"""

from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import date, datetime
from pathlib import Path

import pytest

from broker.session import IST
from marketdata.nse_preopen import (
    PreOpenError,
    merge,
    parse_api,
    parse_csv,
    parse_csv_name,
    read_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"
CSV = FIXTURES / "MW-Pre-Open-Market-NIFTY 50-29-Sep-2026.csv"


@pytest.fixture
def payload() -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / "nse_preopen_nifty50.json").read_text())


def test_the_api_is_dated_by_nse_not_by_the_clock(payload: dict) -> None:  # type: ignore[type-arg]
    session = parse_api(payload)
    assert session.day == date(2026, 9, 29)
    assert session.as_of == datetime(2026, 9, 29, 9, 9, 23, tzinfo=IST)
    assert session.source == "nse"
    assert session.settled


def test_the_api_quote_and_its_book(payload: dict) -> None:  # type: ignore[type-arg]
    q = {q.symbol: q for q in parse_api(payload).quotes}["DRREDDY"]
    assert (q.prev_close, q.final_price, q.final_quantity) == (1221.0, 1243.2, 38477)
    assert (q.change, q.pct_change) == (22.2, 1.82)
    assert (q.total_buy_qty, q.total_sell_qty) == (48495, 44402)
    assert q.turnover_cr == 4.78
    assert q.ffm_cap_cr == 75572.18
    assert len(q.book) == 10
    assert [lvl.price for lvl in q.book if lvl.is_iep] == [1243.2]
    # Best bid and ask are the book's inside levels, which is how the page
    # works out the ones it shows.
    assert (q.best_bid, q.best_bid_qty, q.best_ask, q.best_ask_qty) == (1242.7, 10, 1243.2, 1286)


def test_the_index_figure_comes_with_the_nifty_list(payload: dict) -> None:  # type: ignore[type-arg]
    index = parse_api(payload).index
    assert index is not None
    assert (index.price, index.change, index.pct_change) == (22732.45, -47.8, -0.21)


def test_an_empty_answer_is_an_error_not_an_empty_day() -> None:
    with pytest.raises(PreOpenError, match="No Data Found"):
        parse_api({"data": [], "msg": "No Data Found"})


def test_the_file_and_the_api_agree_on_every_field_the_file_has(
    payload: dict,  # type: ignore[type-arg]
) -> None:
    from_api = {q.symbol: q for q in parse_api(payload).quotes}
    from_file = read_csv(CSV)
    assert from_file.day == date(2026, 9, 29)
    assert from_file.source == "csv"
    assert from_file.as_of is None
    for q in from_file.quotes:
        api = from_api[q.symbol]
        for f in fields(q):
            mine = getattr(q, f.name)
            if f.name != "book" and mine is not None:
                assert mine == getattr(api, f.name), (q.symbol, f.name)


def test_a_dash_for_change_is_no_change() -> None:
    jiofin = {q.symbol: q for q in read_csv(CSV).quotes}["JIOFIN"]
    assert (jiofin.change, jiofin.pct_change) == (0.0, 0.0)
    assert jiofin.iep is None
    assert jiofin.final_quantity == 79967


def test_a_file_without_rows_is_refused() -> None:
    header = CSV.read_text(encoding="utf-8-sig").splitlines()[0]
    with pytest.raises(PreOpenError):
        parse_csv(header + "\n", date(2026, 9, 29))


@pytest.mark.parametrize(
    ("name", "day", "copy"),
    [
        ("MW-Pre-Open-Market-NIFTY 50-29-Sep-2026.csv", date(2026, 9, 29), 0),
        ("MW-Pre-Open-Market-NIFTY 50-22-Sep-2026 (1).csv", date(2026, 9, 22), 1),
        ("MW-Pre-Open-Market-NIFTY BANK-04-Sep-2026.csv", date(2026, 9, 4), 0),
    ],
)
def test_the_date_comes_from_the_file_name(name: str, day: date, copy: int) -> None:
    parsed = parse_csv_name(name)
    assert parsed is not None
    assert (parsed.day, parsed.copy) == (day, copy)


def test_a_file_name_without_a_date_needs_one_given(tmp_path: Path) -> None:
    assert parse_csv_name("preopen.csv") is None
    renamed = tmp_path / "preopen.csv"
    renamed.write_bytes(CSV.read_bytes())
    with pytest.raises(PreOpenError, match="no date"):
        read_csv(renamed)
    assert read_csv(renamed, date(2026, 9, 29)).day == date(2026, 9, 29)


def test_lists_are_merged_with_each_stock_once(payload: dict) -> None:  # type: ignore[type-arg]
    nifty = parse_api(payload)
    fo = replace(nifty, index=None)
    merged = merge([fo, nifty])
    assert len(merged.quotes) == 2
    assert merged.index == nifty.index


def test_lists_from_different_sessions_are_not_merged(payload: dict) -> None:  # type: ignore[type-arg]
    today = parse_api(payload)
    yesterday = replace(today, day=date(2026, 9, 28))
    with pytest.raises(PreOpenError, match="different sessions"):
        merge([today, yesterday])
