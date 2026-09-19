"""Phase 0 checkpoint: confirm the stored Fyers access token actually works.

Run after `fyers_login.py`. Calls Fyers' profile endpoint and prints the
result — a real network round trip against the live API, not a mock.
"""

from __future__ import annotations

import sys

from fyers_apiv3 import fyersModel

from settings import load_settings


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
