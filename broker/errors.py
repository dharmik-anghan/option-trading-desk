"""Broker failures, told apart by what the caller should do about them.

One `FyersApiError` for everything meant every failure reached the desk as an
opaque 500 and the panels simply stopped changing. Being rate-limited, being
offline, and having an expired token all need different responses - wait,
retry, re-authenticate - and a trader watching stale numbers deserves to know
which it is rather than having to read a log file.

Kept broker-agnostic: each adapter classifies its own wire errors into these,
the same way it translates its wire format into `broker/models.py`.
"""

from __future__ import annotations


class BrokerError(Exception):
    """Base for anything that went wrong talking to a broker."""

    #: Short, stable token the API and UI can branch on.
    code = "broker_error"
    #: One sentence, safe to show a user as-is.
    message = "The broker request failed."

    def __init__(self, message: str | None = None) -> None:
        if message:
            self.message = message
        super().__init__(self.message)


class RateLimited(BrokerError):
    """Too many requests. Transient: the same call will work shortly."""

    code = "rate_limited"
    message = "The broker is rate limiting us. Figures may be a few seconds behind."


class BrokerUnreachable(BrokerError):
    """No answer at all - DNS, connection refused, reset, timeout."""

    code = "broker_unreachable"
    message = "Cannot reach the broker. Check your connection."


class AuthFailed(BrokerError):
    """The token was rejected. Needs a fresh login, not a retry."""

    code = "auth_failed"
    message = "The broker rejected the login. Run scripts/fyers_login.py."


def classify_status(code: int | None, message: str) -> type[BrokerError]:
    """Pick the error class for a broker status code and message.

    Fyers reports rate limiting as 429 and as -429, and an unusable token as
    -16, so both the sign and the text are worth checking.
    """
    text = message.lower()
    if code in {429, -429} or "request limit" in text or "rate limit" in text:
        return RateLimited
    if code in {-16, -17, 401, 403} or "authenticate" in text or "invalid token" in text:
        return AuthFailed
    return BrokerError
