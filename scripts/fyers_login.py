"""Fyers login: TOTP auto-login when configured, manual browser flow
otherwise.

Normally you should not need to run this: `broker/token_store.py` refreshes
an expired token on demand whenever the app asks for one. Run it by hand to
prove the credentials work, or when auto-login is not configured and the
manual browser flow is the only way in.

Manual flow: Fyers uses an OAuth-style flow: we send you to a login URL, you
authenticate in the browser, Fyers redirects to `FYERS_REDIRECT_URI` with an
`auth_code` query param, you paste that code (or the whole redirected URL)
back here, and we exchange it for an access token that gets written to
`.env`.

Auto-login: if FYERS_USERNAME, FYERS_TOTP_KEY, and FYERS_PIN are all set in
`.env`, this replicates the same login steps without a browser (see
broker/fyers_auth.py for why, and the risk tradeoff of storing those two
extra secrets). Falls back to the manual flow if auto-login fails, since
these are undocumented endpoints Fyers could change at any time.

This is also the Phase 0 checkpoint: if this script succeeds, real Fyers
connectivity is proven before any broker/market-data code is written.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fyers_apiv3 import fyersModel

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import paths  # noqa: E402
from broker.fyers_auth import AutoLoginError, auto_login  # noqa: E402
from broker.token_store import jwt_subject, persist_token  # noqa: E402
from settings import Settings, load_settings  # noqa: E402

ENV_PATH = paths.ENV_FILE


def extract_auth_code(raw: str) -> str:
    """Accept either a bare auth code or the full redirected URL."""
    raw = raw.strip()
    if raw.startswith("http"):
        query = parse_qs(urlparse(raw).query)
        codes = query.get("auth_code")
        if not codes:
            raise ValueError("No 'auth_code' query param found in the pasted URL.")
        return codes[0]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        raise ValueError("That doesn't look like a valid auth code or URL.")
    return raw


def try_auto_login(settings: Settings) -> str | None:
    if not settings.has_auto_login_credentials:
        return None
    print("FYERS_USERNAME/TOTP_KEY/PIN found - attempting TOTP auto-login...")
    try:
        token = auto_login(
            client_id=settings.fyers_client_id,
            secret_key=settings.fyers_secret_key,
            redirect_uri=settings.fyers_redirect_uri,
            fy_id=settings.fyers_username,
            totp_key=settings.fyers_totp_key,
            pin=settings.fyers_pin,
        )
        print("Auto-login succeeded.")
        return token
    except AutoLoginError as exc:
        print(f"Auto-login failed ({exc}); falling back to manual login.", file=sys.stderr)
        return None


def manual_login(settings: Settings) -> str | None:
    session = fyersModel.SessionModel(
        client_id=settings.fyers_client_id,
        secret_key=settings.fyers_secret_key,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        grant_type="authorization_code",
    )

    print("1. Open this URL, log in, and approve access:")
    print(f"   {session.generate_authcode()}")
    print()
    print("2. You'll be redirected to a URL that fails to load — that's expected.")
    print("   Paste that full URL (or just the auth_code param) below.")
    pasted = input("Redirected URL or auth_code: ")

    try:
        auth_code = extract_auth_code(pasted)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None

    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") != "ok":
        print(f"Login failed. Fyers response: {response}", file=sys.stderr)
        return None

    access_token = response.get("access_token")
    if not access_token:
        print(f"Login failed - no access_token in response: {response}", file=sys.stderr)
        return None
    return str(access_token)


def main() -> int:
    settings = load_settings()

    access_token = try_auto_login(settings) or manual_login(settings)
    if not access_token:
        return 1

    subject = jwt_subject(access_token)
    if subject != "access_token":
        print(
            "Login appeared to succeed but the returned token doesn't look like a real "
            f"access token (sub={subject!r}). Not saving it.",
            file=sys.stderr,
        )
        return 1

    persist_token(access_token, env_path=ENV_PATH)
    print(f"Login succeeded. Access token saved to {ENV_PATH}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
