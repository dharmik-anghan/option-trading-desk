"""Ties Phase 4's pure kill-switch check to Phase 6's real portfolio P&L."""

from __future__ import annotations

from broker.fake import FakeBroker
from broker.models import Position
from execution.portfolio_status import get_portfolio_status
from risk.limits import check_daily_kill_switch


def _losing_position() -> Position:
    return Position(
        symbol="X",
        net_quantity=50,
        average_price=100,
        ltp=150,
        unrealized_pnl=-6000,
        product_type="MARGIN",
    )


def test_kill_switch_engages_from_real_portfolio_loss() -> None:
    broker = FakeBroker(positions=[_losing_position()], realized_pnl=0.0)
    status = get_portfolio_status(broker)

    result = check_daily_kill_switch(status.total_pnl, daily_loss_limit=5000)

    assert result.passed is False


def test_kill_switch_stays_off_within_limit() -> None:
    broker = FakeBroker(positions=[], realized_pnl=-1000.0)
    status = get_portfolio_status(broker)

    result = check_daily_kill_switch(status.total_pnl, daily_loss_limit=5000)

    assert result.passed is True
