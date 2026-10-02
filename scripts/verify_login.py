"""Phase 0 checkpoint: confirm the stored Fyers access token actually works.

Run after `fyers_login.py`. Calls Fyers' profile endpoint and prints the
result — a real network round trip against the live API, not a mock.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fyers_apiv3 import fyersModel

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from broker.fyers.token_store import token_expiry  # noqa: E402
from settings import load_settings  # noqa: E402


def main() -> int:
    settings = load_settings()
    if not settings.fyers_access_token:
        print("No FYERS_ACCESS_TOKEN found. Run scripts/fyers_login.py first.", file=sys.stderr)
        return 1

    client = fyersModel.FyersModel(
        client_id=settings.fyers_client_id,
        token=settings.fyers_access_token,
        is_async=False,
    )
    response = client.get_profile()

    if response.get("s") != "ok":
        print(f"Login check failed. Fyers response: {response}", file=sys.stderr)
        return 1

    profile = response.get("data", {})
    print(f"Login OK. Logged in as: {profile.get('name')} ({profile.get('fy_id')})")
    expiry = token_expiry(settings.fyers_access_token)
    if expiry is not None:
        local = expiry.astimezone()
        print(f"Token expires at {local:%Y-%m-%d %H:%M %Z}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
