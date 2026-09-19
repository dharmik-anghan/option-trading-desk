"""One-time-per-day interactive Fyers login.

Fyers uses an OAuth-style flow: we send you to a login URL, you authenticate
in the browser, Fyers redirects to `FYERS_REDIRECT_URI` with an `auth_code`
query param, you paste that code (or the whole redirected URL) back here, and
we exchange it for an access token that gets written to `.env`.

This is the Phase 0 checkpoint: if this script succeeds, real Fyers
connectivity is proven before any broker/market-data code is written.
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import set_key
from fyers_apiv3 import fyersModel

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from settings import load_settings  # noqa: E402

ENV_PATH = REPO_ROOT / ".env"


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


def jwt_subject(token: str) -> str | None:
    """Best-effort peek at a Fyers JWT's `sub` claim, without verifying it.

    Fyers issues JWTs at two stages: the auth_code (sub="auth_code") and the
    real access_token (sub="access_token"). Used here only to catch, with a
    clear error, the case where the exchange step didn't actually happen and
    we're about to save the unexchanged auth_code as if it were the token.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, UnicodeDecodeError):
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def main() -> int:
    settings = load_settings()

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
        return 1

    session.set_token(auth_code)
    response = session.generate_token()

    if response.get("s") != "ok":
        print(f"Login failed. Fyers response: {response}", file=sys.stderr)
        return 1

    access_token = response.get("access_token")
    if not access_token:
        print(f"Login failed - no access_token in response: {response}", file=sys.stderr)
        return 1

    subject = jwt_subject(access_token)
    if subject != "access_token":
        print(
            "Login appeared to succeed but the returned token doesn't look like a real "
            f"access token (sub={subject!r}). Not saving it. Full response: {response}",
            file=sys.stderr,
        )
        return 1

    set_key(str(ENV_PATH), "FYERS_ACCESS_TOKEN", access_token)
    print(f"Login succeeded. Access token saved to {ENV_PATH}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
