"""Phase 1 checkpoint: fetch a real option chain from Fyers and store it.

Run: uv run python scripts/fetch_option_chain.py [SYMBOL]
Defaults to NSE:NIFTY50-INDEX.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import paths  # noqa: E402
from broker.fyers import FyersBroker  # noqa: E402
from broker.token_store import get_access_token  # noqa: E402
from settings import load_settings  # noqa: E402
from storage.db import connect, init_schema  # noqa: E402
from storage.option_chain_repo import save_snapshot, snapshots_for_symbol  # noqa: E402

DB_PATH = paths.db_path()


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "NSE:NIFTY50-INDEX"

    settings = load_settings()
    broker = FyersBroker(
        client_id=settings.fyers_client_id, access_token=get_access_token(settings)
    )

    print(f"Fetching option chain for {symbol}...")
    chain = broker.get_option_chain(symbol, strike_count=10)
    print(f"Underlying LTP: {chain.underlying_ltp}, {len(chain.rows)} strikes fetched.")

    DB_PATH.parent.mkdir(exist_ok=True)
    conn = connect(str(DB_PATH))
    init_schema(conn)
    snapshot_id = save_snapshot(conn, chain)
    print(f"Saved as snapshot #{snapshot_id} to {DB_PATH}.")

    history = snapshots_for_symbol(conn, symbol)
    print(f"Total snapshots stored for {symbol}: {len(history)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
