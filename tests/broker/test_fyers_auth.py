"""Tests for the TOTP-based auto-login flow.

This flow calls undocumented Fyers endpoints (send_login_otp, verify_otp,
verify_pin, token) in sequence. There's no way to capture a real fixture
without actually logging in (which would burn the day's real login and
require checking in real credentials), so instead these tests inject a fake
HTTP session and assert the request sequence and shape is correct - the
same dependency-injection approach used elsewhere in this codebase for
untestable I/O boundaries (e.g. `execution.manager.ExecutionManager` takes
a `Broker`, tested here against a fake).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pyotp
import pytest

from broker.fyers.auth import AutoLoginError, auto_login, compute_totp

TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # well-known RFC 6238 example secret


@dataclass
class FakeResponse:
    _json: dict[str, Any]
    status_code: int = 200

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@dataclass
class FakeSession:
    """Routes each POST by a substring of its URL to a canned response."""

    responses_by_url_fragment: dict[str, FakeResponse]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def post(
        self,
        url: str,
        json: dict[str, Any],
        timeout: int,
        headers: dict[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append((url, json))
        for fragment, response in self.responses_by_url_fragment.items():
            if fragment in url:
                return response
        raise AssertionError(f"No canned response for URL: {url}")

    def close(self) -> None:
        pass


def _happy_path_session() -> FakeSession:
    return FakeSession(
        responses_by_url_fragment={
            "send_login_otp": FakeResponse({"request_key": "rk1"}),
            "verify_otp": FakeResponse({"request_key": "rk2"}),
            "verify_pin": FakeResponse({"data": {"access_token": "temp-token"}}),
            "v3/token": FakeResponse(
                {"Url": "https://x/?auth_code=abc123&state=autologin"}
            ),
        }
    )


def test_compute_totp_matches_pyotp_at_a_fixed_time() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)

    code = compute_totp(TOTP_SECRET, at=at)

    assert code == pyotp.TOTP(TOTP_SECRET).at(at)
    assert len(code) == 6
    assert code.isdigit()


def test_auto_login_happy_path_returns_final_token() -> None:
    session = _happy_path_session()

    def fake_exchange(auth_code: str) -> str:
        assert auth_code == "abc123"
        return "final-access-token"

    token = auto_login(
        client_id="ABC123-100",
        secret_key="secret",
        redirect_uri="https://127.0.0.1",
        fy_id="XY12345",
        totp_key=TOTP_SECRET,
        pin="1234",
        session=session,
        exchange_token=fake_exchange,
    )

    assert token == "final-access-token"


def test_auto_login_calls_steps_in_order_with_expected_fields() -> None:
    session = _happy_path_session()

    auto_login(
        client_id="ABC123-100",
        secret_key="secret",
        redirect_uri="https://127.0.0.1",
        fy_id="XY12345",
        totp_key=TOTP_SECRET,
        pin="1234",
        session=session,
        exchange_token=lambda code: "token",
    )

    urls = [url for url, _ in session.calls]
    assert "send_login_otp" in urls[0]
    assert "verify_otp" in urls[1]
    assert "verify_pin" in urls[2]
    assert "v3/token" in urls[3]

    _, otp_payload = session.calls[1]
    assert otp_payload["request_key"] == "rk1"
    assert otp_payload["otp"] == compute_totp(TOTP_SECRET)

    _, pin_payload = session.calls[2]
    assert pin_payload["request_key"] == "rk2"


def test_auto_login_raises_clear_error_on_missing_request_key() -> None:
    session = FakeSession(responses_by_url_fragment={"send_login_otp": FakeResponse({})})

    with pytest.raises(AutoLoginError):
        auto_login(
            client_id="ABC123-100",
            secret_key="secret",
            redirect_uri="https://127.0.0.1",
            fy_id="XY12345",
            totp_key=TOTP_SECRET,
            pin="1234",
            session=session,
            exchange_token=lambda code: "unused",
        )


def test_auto_login_raises_on_http_error() -> None:
    session = FakeSession(
        responses_by_url_fragment={"send_login_otp": FakeResponse({}, status_code=500)}
    )

    with pytest.raises(AutoLoginError):
        auto_login(
            client_id="ABC123-100",
            secret_key="secret",
            redirect_uri="https://127.0.0.1",
            fy_id="XY12345",
            totp_key=TOTP_SECRET,
            pin="1234",
            session=session,
            exchange_token=lambda code: "unused",
        )
