"""The pre-open page's two reads."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from api.store import open_db
from marketdata.nse_preopen import parse_api, read_csv
from storage.preopen_repo import save_day

FIXTURES = Path(__file__).parent.parent / "marketdata" / "fixtures"


def _seed(db_path: Path) -> None:
    conn = open_db(db_path)
    api = parse_api(json.loads((FIXTURES / "nse_preopen_nifty50.json").read_text()))
    save_day(conn, api)
    csv = read_csv(FIXTURES / "MW-Pre-Open-Market-NIFTY 50-29-Sep-2026.csv")
    moved = tuple(replace(q, prev_close=q.final_price) for q in csv.quotes)
    save_day(conn, replace(csv, day=date(2026, 9, 25), quotes=moved))
    conn.close()


def test_days_are_newest_first_with_breadth_and_the_index(
    client: TestClient, db_path: Path
) -> None:
    _seed(db_path)
    body = client.get("/api/preopen/days").json()
    assert [d["day"] for d in body["days"]] == ["2026-09-29", "2026-09-25"]
    today = body["days"][0]
    assert today["source"] == "nse"
    assert today["index"]["pct_change"] == -0.21
    # DRREDDY up, JIOFIN unchanged.
    assert (today["advances"], today["declines"], today["unchanged"]) == (1, 0, 1)
    assert body["days"][1]["index"] is None
    # No lifespan in these tests, so no recorder - and the page should say so.
    assert body["recorder"]["running"] is False


def test_one_day_with_books(client: TestClient, db_path: Path) -> None:
    _seed(db_path)
    body = client.get("/api/preopen/days/2026-09-29").json()
    quotes = {q["symbol"]: q for q in body["quotes"]}
    assert set(quotes) == {"DRREDDY", "JIOFIN"}
    assert len(quotes["DRREDDY"]["book"]) == 10
    assert quotes["DRREDDY"]["total_buy_qty"] == 48495


def test_a_day_from_a_file_has_no_book(client: TestClient, db_path: Path) -> None:
    _seed(db_path)
    body = client.get("/api/preopen/days/2026-09-25").json()
    assert all(q["book"] == [] for q in body["quotes"])
    assert all(q["total_buy_qty"] is None for q in body["quotes"])


def test_an_unrecorded_day_is_404(client: TestClient, db_path: Path) -> None:
    _seed(db_path)
    assert client.get("/api/preopen/days/2026-01-01").status_code == 404
