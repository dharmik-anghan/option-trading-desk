from __future__ import annotations

import pytest

from broker.errors import AuthFailed, BrokerError, BrokerUnreachable, RateLimited, classify_status
from broker.fyers import FyersApiError, _check_ok


def test_an_ok_response_raises_nothing() -> None:
    _check_ok({"s": "ok", "code": 200})
    _check_ok({"s": "ok"})


@pytest.mark.parametrize(
    "raw",
    [
        # the two shapes Fyers actually returned in the log
        {"s": "error", "code": 429, "message": "request limit reached"},
        {"s": "error", "Error": {"code": -429, "message": "Request limit reached, retry after"}},
        {"s": "error", "code": -429, "message": "Request limit reached, retry after few mins"},
    ],
)
def test_rate_limits_are_recognised_in_every_shape(raw: dict[str, object]) -> None:
    with pytest.raises(RateLimited):
        _check_ok(raw)


def test_a_dead_connection_is_unreachable_not_a_generic_failure() -> None:
    # the SDK reports transport failures in the message rather than raising
    raw = {
        "s": "error",
        "code": -1,
        "message": (
            "HTTPSConnectionPool(host='api-t1.fyers.in', port=443): Max retries exceeded "
            "with url: /api/v3/positions (Caused by NameResolutionError(...))"
        ),
    }
    with pytest.raises(BrokerUnreachable):
        _check_ok(raw)


def test_a_reset_connection_is_unreachable() -> None:
    raw = {"s": "error", "code": -1, "message": "('Connection aborted.', ConnectionResetError(54"}
    with pytest.raises(BrokerUnreachable):
        _check_ok(raw)


def test_a_rejected_token_needs_a_login_not_a_retry() -> None:
    with pytest.raises(AuthFailed):
        _check_ok({"s": "error", "code": -16, "message": "Could not authenticate the user"})


def test_anything_else_stays_this_adapter_s_own_error() -> None:
    with pytest.raises(FyersApiError):
        _check_ok({"s": "error", "code": -99, "message": "something odd"})


def test_every_error_carries_a_code_and_a_sentence_for_a_user() -> None:
    for cls in (BrokerError, RateLimited, BrokerUnreachable, AuthFailed):
        assert cls.code
        assert cls.message.endswith((".", "!"))


def test_classify_reads_the_message_when_the_code_is_missing() -> None:
    assert classify_status(None, "request limit reached") is RateLimited
    assert classify_status(None, "could not authenticate the user") is AuthFailed
    assert classify_status(None, "who knows") is BrokerError
