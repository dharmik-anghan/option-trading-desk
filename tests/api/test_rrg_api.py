"""The rotation graph endpoint, against a store of its own."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app
from marketdata import BarService, BarStore, Interval, Series
from marketdata.models import Bar

START = datetime(2026, 1, 1, tzinfo=UTC)


def _walk(n: int, drift: float, seed: int) -> list[Bar]:
    import random

    rng = random.Random(seed)
    price = 100.0
    out: list[Bar] = []
    for i in range(n):
        price *= 1 + drift + rng.gauss(0, 0.005)
        out.append(
            Bar(
                ts=START + timedelta(days=i),
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1.0,
            )
        )
    return out


@pytest.fixture
def stocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A store holding the benchmark and two sectors, one strong, one weak."""
    store = BarStore()
    store.write(Series("fyers", "NSE:NIFTY50-INDEX", Interval.D1), _walk(400, 0.0, 1))
    store.write(Series("fyers", "NSE:NIFTYIT-INDEX", Interval.D1), _walk(400, 0.002, 2))
    store.write(Series("fyers", "NSE:NIFTYPHARMA-INDEX", Interval.D1), _walk(400, -0.002, 3))
    store.write(Series("fyers", "NSE:RELIANCE-EQ", Interval.D1), _walk(400, 0.001, 4))

    # Its own constituent file, so these never depend on what NSE published today.
    members = tmp_path / "constituents.json"
    members.write_text(
        json.dumps(
            {
                "NIFTY50": {
                    "at": "2026-01-01T00:00:00+00:00",
                    "members": [
                        {"symbol": "RELIANCE", "name": "Reliance Ltd.", "industry": "Oil"},
                        {"symbol": "NOBARS", "name": "Never Traded Ltd.", "industry": "None"},
                    ],
                }
            }
        )
    )
    import universe.nse as nse
    from api.routers import rrg as router

    monkeypatch.setattr(nse, "DEFAULT_PATH", members)
    monkeypatch.setattr(router, "constituents", lambda i: nse.constituents(i, members))

    with TestClient(app) as client:
        previous = getattr(app.state, "bar_service", None)
        app.state.bar_service = BarService(store, now=lambda: START + timedelta(days=400))
        try:
            yield client
        finally:
            app.state.bar_service = previous
    store.close()


def test_the_options_list_what_can_be_plotted(stocked: TestClient) -> None:
    body = stocked.get("/api/rrg/options").json()

    assert body["timeframes"] == ["daily", "weekly"]
    assert body["indices"][0]["id"] == "SECTORS"
    assert body["indices"][0]["plots"] > 5


def test_all_sectors_plots_the_sector_indices(stocked: TestClient) -> None:
    body = stocked.get("/api/rrg/snapshot", params={"index_id": "SECTORS"}).json()

    plotted = {s["label"] for s in body["series"]}
    assert {"IT", "Pharma"} <= plotted
    assert body["benchmark"] == "NIFTY50"


def test_a_strong_sector_sits_right_and_a_weak_one_left(stocked: TestClient) -> None:
    """The only thing the graph has to get right."""
    body = stocked.get("/api/rrg/snapshot", params={"index_id": "SECTORS"}).json()
    by_label = {s["label"]: s for s in body["series"]}

    assert by_label["IT"]["path"][-1]["ratio"] > 100
    assert by_label["Pharma"]["path"][-1]["ratio"] < 100
    assert by_label["IT"]["quadrant"] in ("leading", "weakening")
    assert by_label["Pharma"]["quadrant"] in ("lagging", "improving")


def test_a_path_comes_back_rather_than_a_point(stocked: TestClient) -> None:
    """The rotation is the information, and it is also what makes replay free:
    the client holds the history, so stepping back is a slider."""
    body = stocked.get("/api/rrg/snapshot", params={"index_id": "SECTORS"}).json()

    path = body["series"][0]["path"]
    assert len(path) > 20
    assert [p["at"] for p in path] == sorted(p["at"] for p in path)


def test_weekly_has_fewer_points_than_daily(stocked: TestClient) -> None:
    daily = stocked.get("/api/rrg/snapshot", params={"index_id": "SECTORS"}).json()
    weekly = stocked.get(
        "/api/rrg/snapshot", params={"index_id": "SECTORS", "timeframe": "weekly"}
    ).json()

    assert weekly["timeframe"] == "weekly"
    assert len(weekly["series"][0]["path"]) < len(daily["series"][0]["path"])


def test_an_index_plots_its_constituents(stocked: TestClient) -> None:
    body = stocked.get("/api/rrg/snapshot", params={"index_id": "NIFTY50"}).json()

    assert [s["label"] for s in body["series"]] == ["RELIANCE"]
    assert body["members_as_at"].startswith("2026-01-01")


def test_a_member_with_no_stored_history_is_named_rather_than_dropped(
    stocked: TestClient,
) -> None:
    """A missing dot is otherwise indistinguishable from one hidden under another."""
    body = stocked.get("/api/rrg/snapshot", params={"index_id": "NIFTY50"}).json()

    assert body["missing"] == ["NOBARS"]
    assert any("could not be placed" in c for c in body["caveats"])


def test_a_constituent_graph_says_how_old_its_membership_is(stocked: TestClient) -> None:
    """Indices drop what has done badly, so an old list flatters."""
    body = stocked.get("/api/rrg/snapshot", params={"index_id": "NIFTY50"}).json()

    assert any("Membership changes" in c for c in body["caveats"])


def test_an_index_with_no_published_list_is_refused_with_the_reason(
    stocked: TestClient,
) -> None:
    response = stocked.get("/api/rrg/snapshot", params={"index_id": "SENSEX"})

    assert response.status_code == 400
    assert "publishes no constituent list" in response.json()["detail"]


def test_an_unknown_index(stocked: TestClient) -> None:
    response = stocked.get("/api/rrg/snapshot", params={"index_id": "NIFTYIMAGINARY"})

    assert response.status_code == 400


def test_a_bad_timeframe(stocked: TestClient) -> None:
    response = stocked.get(
        "/api/rrg/snapshot", params={"index_id": "SECTORS", "timeframe": "hourly"}
    )

    assert response.status_code == 400
    assert "daily or weekly" in response.json()["detail"]


def test_a_benchmark_without_enough_history_says_what_to_run(
    stocked: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = stocked.get(
        "/api/rrg/snapshot", params={"index_id": "SECTORS", "benchmark": "MIDCPNIFTY"}
    )

    assert response.status_code == 404
    assert "backfill_nse.py" in response.json()["detail"]
