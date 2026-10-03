"""Builders for the alerting tests, mirroring the frontend's test fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from alerting.models import Limits


@dataclass(frozen=True)
class FakeLeg:
    id: int = 1
    side: str = "SELL"
    strike: float = 23800.0
    option_type: str = "CE"
    is_open: bool = True
    quantity: int = 1
    entry_price: float = 100.0
    delta: float | None = 0.2
    ltp: float | None = 90.0
    ltp_change: float | None = None
    oi_change: int | None = None


@dataclass(frozen=True)
class FakeBasket:
    id: int = 1
    name: str = "Test structure"
    expiry_date: str | None = "27-10-2026"
    days_to_expiry: float | None = 30.0
    max_loss: float | None = -5000.0
    max_profit: float | None = 2000.0
    #: This structure's own levels. Unset by default, as a new basket's are.
    stop_loss: float | None = None
    profit_target: float | None = None
    delta_limit: float | None = None
    worst_case_limit: float | None = None
    short_delta_limit: float | None = None
    expiry_warn_days: float | None = None
    #: Computed server-side in the real thing; given here so the rules can be
    #: exercised without reconstructing the arithmetic in a test.
    mtm: float | None = None
    total_pnl: float | None = None
    net_delta: float | None = None
    net_delta_per_contract: float | None = None
    legs: list[FakeLeg] = field(default_factory=lambda: [FakeLeg()])


@dataclass(frozen=True)
class FakeEvent:
    day: date = date(2026, 10, 7)
    name: str = "RBI Policy Rate"
    label: str = "RBI Policy Rate"
    importance: str = "H"
    coverage: str = "india"
    country: str | None = None


@pytest.fixture
def limits() -> Limits:
    return Limits()


