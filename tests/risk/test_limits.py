from __future__ import annotations

import math

from risk.limits import check_daily_kill_switch, check_max_loss_limit


def test_max_loss_within_limit_passes() -> None:
    result = check_max_loss_limit(max_loss=-1000, limit=2000)

    assert result.passed is True


def test_max_loss_exceeding_limit_fails() -> None:
    result = check_max_loss_limit(max_loss=-3000, limit=2000)

    assert result.passed is False


def test_max_loss_at_exact_limit_passes() -> None:
    result = check_max_loss_limit(max_loss=-2000, limit=2000)

    assert result.passed is True


def test_unbounded_max_loss_always_fails_a_finite_limit() -> None:
    result = check_max_loss_limit(max_loss=-math.inf, limit=2000)

    assert result.passed is False
    assert "unbounded" in result.reason.lower()


def test_kill_switch_engages_when_daily_loss_exceeds_limit() -> None:
    result = check_daily_kill_switch(realized_and_unrealized_pnl_today=-5500, daily_loss_limit=5000)

    assert result.passed is False


def test_kill_switch_off_when_within_limit() -> None:
    result = check_daily_kill_switch(realized_and_unrealized_pnl_today=-1000, daily_loss_limit=5000)

    assert result.passed is True


def test_kill_switch_off_when_in_profit() -> None:
    result = check_daily_kill_switch(realized_and_unrealized_pnl_today=3000, daily_loss_limit=5000)

    assert result.passed is True
