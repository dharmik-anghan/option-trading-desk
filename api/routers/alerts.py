"""The alert log, the thresholds, and how the watcher is doing.

The engine runs in the backend now (`alerting/`), so these endpoints read state
rather than compute it. That is the point: a log the server owns is one that
survives a closed tab, can be delivered to Telegram, and is the same for every
browser that opens the desk.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from alerting.models import Direction, Limits, Watch, WatchKind
from api.deps import DbPathDep
from api.store import open_db
from storage.alert_repo import (
    add_watch,
    clear_log,
    delete_watch,
    list_watches,
    load_active,
    load_limits,
    load_log_with_delivery,
    save_limits,
    set_watch_enabled,
)

router = APIRouter(tags=["alerts"])


class AlertResponse(BaseModel):
    key: str
    severity: str
    subject: str | None
    message: str
    #: Epoch milliseconds, the same clock the frontend uses.
    at: int
    #: When this was delivered off-screen, or null if it has not been.
    #:
    #: Reported rather than hidden so the desk can show which alerts actually
    #: left the building. Null with Telegram configured means a send failed and
    #: it is still queued; null with Telegram off means nowhere to send it.
    notified_at: int | None


class LimitsResponse(BaseModel):
    max_loss: float
    short_delta: float
    expiry_days: float


class WatcherStatus(BaseModel):
    """Whether anything is actually watching.

    Worth reporting plainly: an empty alert list means "nothing is wrong" only if
    something is looking. If the watcher is not running, or its last pass failed,
    the desk should say so rather than imply calm.
    """

    running: bool
    last_run_at: int | None
    last_error: str | None
    #: Whether alerts are being delivered anywhere off-screen.
    telegram: bool


class WatchResponse(BaseModel):
    id: int
    kind: str
    symbol: str | None
    direction: str
    level: float
    note: str
    enabled: bool


class WatchRequest(BaseModel):
    kind: Literal["price", "pnl"]
    direction: Literal["above", "below"]
    level: float
    #: Required for a price watch; ignored for a P&L one.
    symbol: str | None = None
    note: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def _price_needs_a_symbol(self) -> WatchRequest:
        if self.kind == "price" and not (self.symbol or "").strip():
            raise ValueError("a price watch needs a symbol")
        return self


class AlertsResponse(BaseModel):
    alerts: list[AlertResponse]
    #: Conditions true right now, whether or not they fired this pass.
    active: list[str]
    limits: LimitsResponse
    watches: list[WatchResponse]
    watcher: WatcherStatus


class LimitsRequest(BaseModel):
    # Bounds are sanity, not policy: a negative threshold would silently never
    # fire, and a short delta above 1 is not reachable.
    max_loss: float = Field(gt=0)
    short_delta: float = Field(gt=0, le=1)
    expiry_days: float = Field(ge=0)


def _limits_response(limits: Limits) -> LimitsResponse:
    return LimitsResponse(
        max_loss=limits.max_loss,
        short_delta=limits.short_delta,
        expiry_days=limits.expiry_days,
    )


def _status(request: Request) -> WatcherStatus:
    watcher = getattr(request.app.state, "alert_watcher", None)
    notifier = getattr(request.app.state, "alert_notifier", None)
    return WatcherStatus(
        running=watcher is not None,
        last_run_at=getattr(watcher, "last_run_at", None),
        last_error=getattr(watcher, "last_error", None),
        telegram=bool(notifier is not None and notifier.configured),
    )


@router.get("/api/alerts", response_model=AlertsResponse)
def alerts(request: Request, db_path: DbPathDep, limit: int = 200) -> AlertsResponse:
    conn = open_db(db_path)
    try:
        return AlertsResponse(
            alerts=[
                AlertResponse(
                    key=a.key,
                    severity=str(a.severity),
                    subject=a.subject,
                    message=a.message,
                    at=a.at,
                    notified_at=notified_at,
                )
                for a, notified_at in load_log_with_delivery(conn, limit)
            ],
            active=sorted(load_active(conn)),
            limits=_limits_response(load_limits(conn)),
            watches=[_watch_response(w) for w in list_watches(conn)],
            watcher=_status(request),
        )
    finally:
        conn.close()


@router.post("/api/alerts/clear", status_code=204)
def clear(db_path: DbPathDep) -> None:
    """Empty the log and forget which conditions were already announced.

    Both, deliberately: keeping the active set would leave every still-true
    condition marked as already-alerted, so nothing would re-fire and the panel
    would sit empty while several were live. Clearing means "show me where things
    stand", so what is true now is written again on the next pass.
    """
    conn = open_db(db_path)
    try:
        clear_log(conn)
    finally:
        conn.close()


@router.put("/api/alerts/limits", response_model=LimitsResponse)
def put_limits(request_body: LimitsRequest, db_path: DbPathDep) -> LimitsResponse:
    limits = Limits(
        max_loss=request_body.max_loss,
        short_delta=request_body.short_delta,
        expiry_days=request_body.expiry_days,
    )
    conn = open_db(db_path)
    try:
        save_limits(conn, limits)
    finally:
        conn.close()
    return _limits_response(limits)


def _watch_response(watch: Watch) -> WatchResponse:
    return WatchResponse(
        id=watch.id,
        kind=str(watch.kind),
        symbol=watch.symbol,
        direction=str(watch.direction),
        level=watch.level,
        note=watch.note,
        enabled=watch.enabled,
    )


@router.get("/api/alerts/watches", response_model=list[WatchResponse])
def get_watches(db_path: DbPathDep) -> list[WatchResponse]:
    conn = open_db(db_path)
    try:
        return [_watch_response(w) for w in list_watches(conn)]
    finally:
        conn.close()


@router.post("/api/alerts/watches", response_model=WatchResponse, status_code=201)
def post_watch(body: WatchRequest, db_path: DbPathDep) -> WatchResponse:
    """Ask to be told when a price or the book's P&L goes through a level."""
    conn = open_db(db_path)
    try:
        watch = add_watch(
            conn,
            kind=WatchKind(body.kind),
            direction=Direction(body.direction),
            level=body.level,
            symbol=(body.symbol or "").strip() or None if body.kind == "price" else None,
            note=body.note.strip(),
            created_at=datetime.now(UTC).isoformat(),
        )
        return _watch_response(watch)
    finally:
        conn.close()


@router.post("/api/alerts/watches/{watch_id}/enabled", response_model=list[WatchResponse])
def toggle_watch(watch_id: int, enabled: bool, db_path: DbPathDep) -> list[WatchResponse]:
    """Turn one off without losing it - a level worth watching again later."""
    conn = open_db(db_path)
    try:
        if not set_watch_enabled(conn, watch_id, enabled):
            raise HTTPException(status_code=404, detail="no such watch")
        return [_watch_response(w) for w in list_watches(conn)]
    finally:
        conn.close()


@router.delete("/api/alerts/watches/{watch_id}", status_code=204)
def remove_watch(watch_id: int, db_path: DbPathDep) -> None:
    conn = open_db(db_path)
    try:
        if not delete_watch(conn, watch_id):
            raise HTTPException(status_code=404, detail="no such watch")
    finally:
        conn.close()
