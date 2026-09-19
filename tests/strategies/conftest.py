"""Shared fixture: a small, fully controlled option chain with hand-picked
deltas, so strategy strike-selection is deterministic and easy to
hand-verify rather than depending on real, time-varying market data.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from broker.models import Greeks, OptionChain, OptionChainRow, OptionType

# strike, option_type, ltp, delta
_ROWS: list[tuple[float, OptionType, float, float]] = [
    (100, "CE", 40.0, 0.50),
    (110, "CE", 25.0, 0.30),
    (120, "CE", 12.0, 0.16),
    (130, "CE", 6.0, 0.08),
    (100, "PE", 40.0, -0.50),
    (90, "PE", 25.0, -0.30),
    (80, "PE", 12.0, -0.16),
    (70, "PE", 6.0, -0.08),
]


@pytest.fixture
def sample_chain() -> OptionChain:
    rows = [
        OptionChainRow(
            symbol=f"TEST-{int(strike)}-{option_type}",
            strike=strike,
            option_type=option_type,
            ltp=ltp,
            bid=ltp - 0.5,
            ask=ltp + 0.5,
            oi=1000,
            prev_oi=900,
            volume=5000,
            greeks=Greeks(delta=delta, gamma=0.01, theta=-1.0, vega=1.0, iv=15.0),
        )
        for strike, option_type, ltp, delta in _ROWS
    ]
    return OptionChain(
        underlying_symbol="TEST:UNDERLYING",
        underlying_ltp=100.0,
        fetched_at=datetime(2026, 9, 19, 10, 0, tzinfo=UTC),
        rows=rows,
    )
