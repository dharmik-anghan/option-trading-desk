"""The alert log, the thresholds, and how the watcher is doing.

The engine runs in the backend now (`alerting/`), so these endpoints read state
rather than compute it. That is the point: a log the server owns is one that
survives a closed tab, can be delivered to Telegram, and is the same for every
browser that opens the desk.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from alerting.models import Limits
from api.deps import DbPathDep
from api.store import open_db
from storage.alert_repo import clear_log, load_active, load_limits, load_log, save_limits

router = APIRouter(tags=["alerts"])


class AlertResponse(BaseModel):
    key: str
    severity: str
    subject: str | None
    message: str
    #: Epoch milliseconds, the same clock the frontend uses.
    at: int


class LimitsResponse(BaseModel):
    target: float
    daily_loss: float
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


class AlertsResponse(BaseModel):
    alerts: list[AlertResponse]
    #: Conditions true right now, whether or not they fired this pass.
    active: list[str]
    limits: LimitsResponse
    watcher: WatcherStatus


class LimitsRequest(BaseModel):
    # Bounds are sanity, not policy: a negative threshold would silently never
    # fire, and a short delta above 1 is not reachable.
    target: float = Field(gt=0)
    daily_loss: float = Field(gt=0)
    max_loss: float = Field(gt=0)
    short_delta: float = Field(gt=0, le=1)
    expiry_days: float = Field(ge=0)


def _limits_response(limits: Limits) -> LimitsResponse:
    return LimitsResponse(
        target=limits.target,
        daily_loss=limits.daily_loss,
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
                )
                for a in load_log(conn, limit)
            ],
            active=sorted(load_active(conn)),
            limits=_limits_response(load_limits(conn)),
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
        target=request_body.target,
        daily_loss=request_body.daily_loss,
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
