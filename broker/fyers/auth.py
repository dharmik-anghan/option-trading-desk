"""TOTP-based Fyers auto-login — no manual browser step.

This replicates the exact steps a human does when logging in via browser
(OTP -> TOTP verify -> PIN verify -> get auth code -> exchange for token),
using Fyers' own undocumented mobile/web login endpoints. Ported and
re-audited from the (small, anonymous, unmaintained) `multi-broker-sdk`
PyPI package rather than taken on as a dependency — same technique, code we
own and can read, no third-party supply-chain exposure for a flow that
handles your TOTP secret and PIN.

These are undocumented endpoints Fyers could change at any time. If this
breaks, fall back to the manual OAuth flow in `scripts/fyers_login.py`
(`fyersModel.SessionModel` + browser login) — that one is Fyers' official,
documented flow and won't be affected by this breaking.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from fyers_apiv3 import fyersModel

BASE_URL = "https://api-t2.fyers.in"
TOKEN_URL = "https://api-t1.fyers.in/api/v3/token"
DIRECT_LOGIN_URL = "https://api-t1.fyers.in/api/v3/direct-login"


class AutoLoginError(RuntimeError):
    """Raised when any step of the TOTP auto-login flow fails."""


class HttpResponse(Protocol):
    def json(self) -> dict[str, Any]: ...
    def raise_for_status(self) -> None: ...


class HttpSession(Protocol):
    def post(
        self,
        url: str,
        json: dict[str, Any],
        timeout: int,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse: ...
    def close(self) -> None: ...


def compute_totp(totp_key: str, at: datetime | None = None) -> str:
    totp = pyotp.TOTP(totp_key)
    return totp.at(at) if at is not None else totp.now()


def _post(
    session: HttpSession, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = session.post(url, json=payload, timeout=10, headers=headers)
    try:
        response.raise_for_status()
    except Exception as exc:
        raise AutoLoginError(f"HTTP error calling {url}: {exc}") from exc
    return response.json()


def _send_login_otp(session: HttpSession, fy_id: str) -> str:
    data = _post(
        session,
        f"{BASE_URL}/vagator/v2/send_login_otp_v2",
        {"fy_id": base64.b64encode(fy_id.encode()).decode(), "app_id": "2"},
    )
    try:
        return str(data["request_key"])
    except KeyError as exc:
        raise AutoLoginError(f"Unexpected response from send_login_otp: {data}") from exc


def _verify_otp(session: HttpSession, request_key: str, totp_key: str) -> str:
    data = _post(
        session,
        f"{BASE_URL}/vagator/v2/verify_otp",
        {"request_key": request_key, "otp": compute_totp(totp_key)},
    )
    try:
        return str(data["request_key"])
    except KeyError as exc:
        raise AutoLoginError(f"Unexpected response from verify_otp: {data}") from exc


def _verify_pin(session: HttpSession, request_key: str, pin: str) -> str:
    data = _post(
        session,
        f"{BASE_URL}/vagator/v2/verify_pin_v2",
        {
            "request_key": request_key,
            "identity_type": "pin",
            "identifier": base64.b64encode(pin.encode()).decode(),
        },
    )
    try:
        return str(data["data"]["access_token"])
    except KeyError as exc:
        raise AutoLoginError(f"Unexpected response from verify_pin: {data}") from exc


def _get_auth_code(
    session: HttpSession, temp_access_token: str, client_id: str, redirect_uri: str, fy_id: str
) -> str:
    headers = {
        "authorization": f"Bearer {temp_access_token}",
        "content-type": "application/json; charset=UTF-8",
    }
    data = _post(
        session,
        TOKEN_URL,
        {
            "fyers_id": fy_id,
            "app_id": client_id[:-4],
            "redirect_uri": redirect_uri,
            "appType": "100",
            "response_type": "code",
            "state": "autologin",
            "create_cookie": True,
        },
        headers=headers,
    )

    if "Url" in data:
        redirect_url = data["Url"]
    else:
        inner = data.get("data", {})
        try:
            confirm_payload = {
                "app_id": inner["app_id"],
                "auth": inner["auth"],
                "nonce": "",
                "redirect_uri": inner["redirectUrl"],
                "response_type": "code",
                "scope": "",
                "state": "None",
                "user_id": inner["user_id"],
            }
        except KeyError as exc:
            raise AutoLoginError(f"Unexpected response from token endpoint: {data}") from exc
        confirm_data = _post(session, DIRECT_LOGIN_URL, confirm_payload, headers=headers)
        try:
            redirect_url = confirm_data["Url"]
        except KeyError as exc:
            raise AutoLoginError(f"Unexpected response from direct-login: {confirm_data}") from exc

    query = parse_qs(urlparse(str(redirect_url)).query)
    try:
        return str(query["auth_code"][0])
    except KeyError as exc:
        raise AutoLoginError(f"No auth_code in redirect URL: {redirect_url}") from exc


def _default_exchange_token(
    auth_code: str, client_id: str, secret_key: str, redirect_uri: str
) -> str:
    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    response = session.generate_token()
    if response.get("s") != "ok" or "access_token" not in response:
        raise AutoLoginError(f"Token exchange failed: {response}")
    return str(response["access_token"])


def auto_login(
    *,
    client_id: str,
    secret_key: str,
    redirect_uri: str,
    fy_id: str,
    totp_key: str,
    pin: str,
    session: HttpSession | None = None,
    exchange_token: Callable[[str], str] | None = None,
) -> str:
    """Run the full TOTP auto-login flow and return a real access token.

    `session` and `exchange_token` are injectable purely for testing without
    a real Fyers account or network access - real callers should omit both.
    """
    owns_session = session is None
    http: HttpSession = session or requests.Session()
    try:
        request_key = _send_login_otp(http, fy_id)
        request_key = _verify_otp(http, request_key, totp_key)
        temp_token = _verify_pin(http, request_key, pin)
        auth_code = _get_auth_code(http, temp_token, client_id, redirect_uri, fy_id)
        exchange = exchange_token or (
            lambda code: _default_exchange_token(code, client_id, secret_key, redirect_uri)
        )
        return exchange(auth_code)
    finally:
        if owns_session:
            http.close()
