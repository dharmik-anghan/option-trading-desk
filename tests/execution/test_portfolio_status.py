from __future__ import annotations

import pytest

from broker.fake import FakeBroker
from broker.models import Position
from execution.portfolio_status import get_portfolio_status


def _position(symbol: str, unrealized_pnl: float) -> Position:
    return Position(
        symbol=symbol,
        net_quantity=-50,
        average_price=40.5,
        ltp=38.0,
        unrealized_pnl=unrealized_pnl,
        product_type="MARGIN",
    )


def test_total_pnl_combines_realized_and_unrealized() -> None:
    broker = FakeBroker(
        positions=[_position("A", 125.0), _position("B", -40.0)], realized_pnl=300.0
    )

    status = get_portfolio_status(broker)

    assert status.realized_pnl == pytest.approx(300.0)
    assert status.unrealized_pnl == pytest.approx(85.0)
    assert status.total_pnl == pytest.approx(385.0)
    assert len(status.positions) == 2


def test_no_open_positions_gives_zero_unrealized() -> None:
    broker = FakeBroker(positions=[], realized_pnl=150.0)

    status = get_portfolio_status(broker)

    assert status.unrealized_pnl == pytest.approx(0.0)
    assert status.total_pnl == pytest.approx(150.0)


def test_booked_comes_from_positions_when_the_venue_reports_it_there() -> None:
    from broker.fake import FakeBroker
    from execution.portfolio_status import get_portfolio_status

    class FundsLag(FakeBroker):
        """Funds still saying 0, as Fyers' did mid-day on 29 Sep 2026."""

        def get_booked_pnl(self) -> float:
            return 4881.5

    status = get_portfolio_status(FundsLag(realized_pnl=0.0))
    assert status.realized_pnl == 4881.5
