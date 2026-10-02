"""The options backtest API over a small store, end to end through FastAPI."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.routers.optbt import router
from optbt.data.store import SCHEMA

DAY = date(2026, 9, 21)
EXPIRY = date(2026, 9, 22)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    path = tmp_path / "options.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(SCHEMA)
    conn.execute("INSERT INTO expiry VALUES ('NIFTY', ?, 'options')", [EXPIRY])
    start = datetime.combine(DAY, time(9, 15))
    minutes = [start + timedelta(minutes=i) for i in range(375)]
    conn.execute(
        "INSERT INTO index_bar VALUES "
        + ",".join(f"('NSE:NIFTY50-INDEX','1',TIMESTAMP '{m}',23450,23450,23450,23450,0)"
                   for m in minutes)
    )
    for kind in ("CE", "PE"):
        conn.execute(
            "INSERT INTO contract VALUES (?, 'NIFTY', ?, ?, 23450, 375, NULL, NULL, ?)",
            [f"X{kind}", EXPIRY, kind, datetime(2026, 9, 28)],
        )
        # 100 all day, 60 from 15:15: each short leg makes 40 points.
        conn.execute(
            "INSERT INTO option_bar VALUES "
            + ",".join(
                f"('NIFTY', DATE '{EXPIRY}', '{kind}', 23450, TIMESTAMP '{m}', "
                f"{p}, {p}, {p}, {p}, {65 * (i % 5 + 1)}, {65 * (900 + i)})"
                for i, m in enumerate(minutes)
                for p in [60.0 if m.time() >= time(15, 15) else 100.0]
            )
        )
    conn.close()
    monkeypatch.setenv("OPTBT_STORE", str(path))
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_coverage_says_what_is_held(client: TestClient) -> None:
    body = client.get("/api/optbt/coverage").json()
    assert body["first_day"] == "2026-09-21"
    assert (body["expiries_held"], body["contracts"]) == (1, 2)


def test_a_run_returns_the_trade_its_legs_and_the_curve(client: TestClient) -> None:
    body = client.post(
        "/api/optbt/run",
        json={
            "start": "2026-09-21",
            "end": "2026-09-21",
            "slippage": 0,
            "min_slip": 0,
            "legs": [
                {"side": "sell", "kind": "CE", "stop": {"kind": "pct", "value": 0.25}},
                {"side": "sell", "kind": "PE", "stop": {"kind": "pct", "value": 0.25}},
            ],
        },
    ).json()
    (trade,) = body["trades"]
    assert [leg["strike"] for leg in trade["legs"]] == [23450, 23450]
    # 40 points x 65 x 2 legs = 5,200 gross.
    assert trade["gross"] == pytest.approx(5_200)
    assert body["summary"]["net"] == pytest.approx(5_200 - body["charges"]["total"])
    assert body["equity"][0][0] == "2026-09-21"
    assert body["by_month"] == {"2026-09": pytest.approx(body["summary"]["net"])}


def test_a_backwards_window_is_refused(client: TestClient) -> None:
    leg = {"side": "sell", "kind": "CE"}
    r = client.post(
        "/api/optbt/run", json={"start": "2026-09-22", "end": "2026-09-21", "legs": [leg]}
    )
    assert r.status_code == 422


def test_a_run_needs_at_least_one_leg(client: TestClient) -> None:
    r = client.post("/api/optbt/run", json={"start": "2026-09-21", "end": "2026-09-21"})
    assert r.status_code == 422


def test_replay_returns_spot_and_each_legs_minutes(client: TestClient) -> None:
    body = client.post(
        "/api/optbt/replay",
        json={"start": "2026-09-21", "end": "2026-09-21",
              "legs": [{"expiry": "2026-09-22", "strike": 23450, "kind": "CE"}]},
    ).json()
    assert len(body["spot"]["points"]) == 375
    assert body["legs"][0]["points"][-1][1] == 60.0


def test_a_missing_store_says_how_to_make_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPTBT_STORE", str(tmp_path / "nothing.duckdb"))
    r = client.get("/api/optbt/coverage")
    assert r.status_code == 404
    assert "backfill_options" in r.json()["detail"]


def test_a_field_the_backend_does_not_know_is_refused_not_ignored(client: TestClient) -> None:
    # A newer page sending a condition an older backend dropped silently made
    # every run identical. Now the mismatch is an error you can see.
    r = client.post(
        "/api/optbt/run",
        json={"start": "2026-09-21", "end": "2026-09-21", "legs": [{"side": "sell", "kind": "CE"}],
              "some_new_rule": True},
    )
    assert r.status_code == 422


def test_the_underlyings_the_store_holds_are_listed(client: TestClient) -> None:
    body = client.get("/api/optbt/underlyings").json()
    assert [u["underlying"] for u in body] == ["NIFTY"]
    assert body[0]["first_day"] == "2026-09-21"
