"""The alert endpoints.

These read state the watcher writes, so the tests write that state directly
rather than running a watcher - what is under test is the wire shape and the
clear/limits behaviour, not the engine.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from alerting.models import Alert, Limits, Severity
from api.store import open_db
from storage.alert_repo import append, load_limits, save_active


def _seed(db_path: Path) -> None:
    conn = open_db(db_path)
    try:
        append(
            conn,
            [
                Alert(key="daily-loss", severity=Severity.RISK, message="breached", at=2000),
                Alert(
                    key="tested:19",
                    severity=Severity.WARN,
                    message="Short 22,900 PE tested",
                    at=1000,
                    subject="27 Oct - Iron Condor",
                ),
            ],
        )
        save_active(conn, frozenset({"daily-loss", "tested:19"}), now=2000)
    finally:
        conn.close()


class TestReading:
    def test_an_empty_desk_reads_as_empty(self, client: TestClient) -> None:
        body = client.get("/api/alerts").json()
        assert body["alerts"] == []
        assert body["active"] == []

    def test_alerts_come_back_newest_first(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        body = client.get("/api/alerts").json()
        assert [a["key"] for a in body["alerts"]] == ["daily-loss", "tested:19"]

    def test_an_alert_keeps_its_parts_separate(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        body = client.get("/api/alerts").json()
        tested = next(a for a in body["alerts"] if a["key"] == "tested:19")
        assert tested["subject"] == "27 Oct - Iron Condor"
        assert tested["message"] == "Short 22,900 PE tested"
        assert tested["severity"] == "warn"
        assert tested["at"] == 1000

    def test_active_conditions_are_reported(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        assert client.get("/api/alerts").json()["active"] == ["daily-loss", "tested:19"]

    def test_the_limits_come_with_it(self, client: TestClient) -> None:
        limits = client.get("/api/alerts").json()["limits"]
        assert limits["max_loss"] == Limits().max_loss
        assert limits["short_delta"] == Limits().short_delta

    def test_it_says_whether_anything_is_watching(self, client: TestClient) -> None:
        # An empty list means "nothing is wrong" only if something is looking.
        watcher = client.get("/api/alerts").json()["watcher"]
        assert watcher["running"] is False  # no lifespan in these tests
        assert watcher["telegram"] is False
        assert watcher["last_error"] is None


class TestClearing:
    def test_clearing_empties_the_log(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        assert client.post("/api/alerts/clear").status_code == 204
        assert client.get("/api/alerts").json()["alerts"] == []

    def test_clearing_also_forgets_what_was_announced(
        self, client: TestClient, db_path: Path
    ) -> None:
        # Otherwise every still-true condition stays marked as already-alerted,
        # nothing re-fires, and the panel sits empty while several are live.
        _seed(db_path)
        client.post("/api/alerts/clear")
        assert client.get("/api/alerts").json()["active"] == []


class TestLimits:
    def test_limits_can_be_changed_and_stick(self, client: TestClient, db_path: Path) -> None:
        payload = {
            "max_loss": 30000,
            "short_delta": 0.25,
            "expiry_days": 5,
        }
        assert client.put("/api/alerts/limits", json=payload).status_code == 200
        assert client.get("/api/alerts").json()["limits"]["max_loss"] == 30000
        conn = open_db(db_path)
        try:
            assert load_limits(conn).short_delta == 0.25
        finally:
            conn.close()

    def test_a_nonsense_threshold_is_refused(self, client: TestClient) -> None:
        payload = {"max_loss": -1, "short_delta": 0.25, "expiry_days": 5}  # never fires
        assert client.put("/api/alerts/limits", json=payload).status_code == 422

    def test_a_delta_above_one_is_refused(self, client: TestClient) -> None:
        payload = {
            "max_loss": 30000,
            "short_delta": 1.5,  # unreachable
            "expiry_days": 5,
        }
        assert client.put("/api/alerts/limits", json=payload).status_code == 422


class TestWatches:
    """Levels you ask about, over HTTP."""

    PRICE = {
        "kind": "price",
        "direction": "above",
        "level": 24000,
        "symbol": "NSE:NIFTY50-INDEX",
        "note": "Nifty breakout",
    }
    PNL = {"kind": "pnl", "direction": "below", "level": -5000}

    def test_none_to_begin_with(self, client: TestClient) -> None:
        assert client.get("/api/alerts/watches").json() == []

    def test_adding_a_price_level(self, client: TestClient) -> None:
        created = client.post("/api/alerts/watches", json=self.PRICE)
        assert created.status_code == 201
        body = created.json()
        assert body["symbol"] == "NSE:NIFTY50-INDEX"
        assert body["level"] == 24000
        assert body["note"] == "Nifty breakout"
        assert body["enabled"] is True

    def test_adding_a_pnl_level_needs_no_symbol(self, client: TestClient) -> None:
        body = client.post("/api/alerts/watches", json=self.PNL).json()
        assert body["symbol"] is None
        assert body["kind"] == "pnl"

    def test_a_price_level_without_a_symbol_is_refused(self, client: TestClient) -> None:
        bad = {"kind": "price", "direction": "above", "level": 24000}
        assert client.post("/api/alerts/watches", json=bad).status_code == 422

    def test_a_nonsense_direction_is_refused(self, client: TestClient) -> None:
        bad = {**self.PRICE, "direction": "sideways"}
        assert client.post("/api/alerts/watches", json=bad).status_code == 422

    def test_they_come_back_with_the_alerts(self, client: TestClient) -> None:
        client.post("/api/alerts/watches", json=self.PRICE)
        assert len(client.get("/api/alerts").json()["watches"]) == 1

    def test_turning_one_off_keeps_it(self, client: TestClient) -> None:
        watch_id = client.post("/api/alerts/watches", json=self.PRICE).json()["id"]
        rows = client.post(f"/api/alerts/watches/{watch_id}/enabled?enabled=false").json()
        assert rows[0]["enabled"] is False
        assert len(client.get("/api/alerts/watches").json()) == 1

    def test_deleting_one_removes_it(self, client: TestClient) -> None:
        watch_id = client.post("/api/alerts/watches", json=self.PRICE).json()["id"]
        assert client.delete(f"/api/alerts/watches/{watch_id}").status_code == 204
        assert client.get("/api/alerts/watches").json() == []

    def test_deleting_something_absent_is_a_404(self, client: TestClient) -> None:
        assert client.delete("/api/alerts/watches/999").status_code == 404

    def test_toggling_something_absent_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/alerts/watches/999/enabled?enabled=false").status_code == 404


class TestDeliveryIsVisible:
    def test_an_undelivered_alert_says_so(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        assert all(a["notified_at"] is None for a in client.get("/api/alerts").json()["alerts"])

    def test_a_delivered_alert_carries_when(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        conn = open_db(db_path)
        try:
            from storage.alert_repo import mark_delivered, undelivered

            mark_delivered(conn, [row_id for row_id, _a in undelivered(conn)], at=7777)
        finally:
            conn.close()
        assert all(a["notified_at"] == 7777 for a in client.get("/api/alerts").json()["alerts"])
