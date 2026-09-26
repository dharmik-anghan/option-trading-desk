"""Broker failures as HTTP statuses.

Registered on the app in `app.py`: an exception handler belongs to the
application rather than to any one router.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from broker.errors import AuthFailed, BrokerError, BrokerUnreachable, RateLimited

_BROKER_STATUS: dict[type[BrokerError], int] = {
    RateLimited: 429,
    BrokerUnreachable: 503,
    AuthFailed: 401,
}

def broker_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Registered on the app, so the signature is FastAPI's (`Exception`).

    Only `BrokerError` is routed here; anything else is a programming error and
    should not be quietly relabelled as a broker problem.
    """
    if not isinstance(exc, BrokerError):
        raise exc
    status = next(
        (code for cls, code in _BROKER_STATUS.items() if isinstance(exc, cls)),
        502,
    )
    return JSONResponse(
        status_code=status,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )
