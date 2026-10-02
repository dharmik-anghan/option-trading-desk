"""Phase 6 checkpoint: live portfolio P&L status, the daily kill-switch
check against real numbers, and a persisted historical snapshot.

This is the "minimal dashboard" for now — a CLI view, not a web UI. Per the
earlier frontend discussion, a real dashboard stays a deliberate later
decision once the CLI flow is proven and there's an actual felt need for
one (see docs/ARCHITECTURE.md).

Run: uv run python scripts/portfolio_status.py [DAILY_LOSS_LIMIT]
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import paths  # noqa: E402
from broker.factory import options_broker  # noqa: E402
from execution.portfolio_status import get_portfolio_status  # noqa: E402
from risk.limits import check_daily_kill_switch  # noqa: E402
from storage.db import connect, init_schema  # noqa: E402
from storage.portfolio_repo import save_portfolio_snapshot  # noqa: E402

DB_PATH = paths.db_path()
DEFAULT_DAILY_LOSS_LIMIT = 5000.0


def main() -> int:
    daily_loss_limit = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DAILY_LOSS_LIMIT

    broker = options_broker()

    status = get_portfolio_status(broker)

    print(f"Open positions: {len(status.positions)}")
    for position in status.positions:
        print(
            f"  {position.symbol}: net_qty={position.net_quantity} "
            f"avg={position.average_price} ltp={position.ltp} "
            f"unrealized={position.unrealized_pnl}"
        )
    print(f"\nRealized P&L today: {status.realized_pnl}")
    print(f"Unrealized P&L:      {status.unrealized_pnl}")
    print(f"Total P&L today:     {status.total_pnl}")

    kill_switch = check_daily_kill_switch(status.total_pnl, daily_loss_limit)
    label = "OK" if kill_switch.passed else "TRIGGERED"
    print(f"\nDaily kill-switch (limit {daily_loss_limit}): [{label}] {kill_switch.reason}")

    DB_PATH.parent.mkdir(exist_ok=True)
    conn = connect(str(DB_PATH))
    init_schema(conn)
    fetched_at = datetime.now(UTC)
    save_portfolio_snapshot(
        conn, status.positions, status.realized_pnl, status.unrealized_pnl, fetched_at=fetched_at
    )
    print(f"\nSnapshot saved to {DB_PATH} at {fetched_at.isoformat()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
