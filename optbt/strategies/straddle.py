"""The intraday short straddle: the reference strategy, as two legs.

Sell the ATM call and put of the nearest expiry at the entry time, each with its
own stop; buy back whatever is open at the exit time. Kept as its own name
because it is the strategy every other result is compared against, but it is
the leg builder underneath - one path through the engine, not two.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from optbt.engine import Level
from optbt.strategies.legs import (
    ExpiryChoice,
    LegsConfig,
    LegStrategy,
    atm_strike,
    straddle,
)

__all__ = ["Straddle", "StraddleConfig", "atm_strike"]


@dataclass(frozen=True)
class StraddleConfig:
    entry: time = time(9, 20)
    exit: time = time(15, 15)
    #: Per-leg stop, as a fraction of the leg's fill: 0.25 buys back at 1.25x.
    stop_pct: float = 0.25
    lots: int = 1
    #: 0 is the nearest expiry, 1 the one after.
    expiry_offset: int = 0
    #: After one leg stops out, move the other's stop to its own entry price.
    trail_to_cost: bool = False


def Straddle(config: StraddleConfig | None = None) -> LegStrategy:  # noqa: N802 - reads as a class
    cfg = config or StraddleConfig()
    expiry = ExpiryChoice(nth=cfg.expiry_offset + 1)
    legs = tuple(
        leg.__class__(
            side=leg.side,
            kind=leg.kind,
            lots=cfg.lots,
            expiry=expiry,
            strike=leg.strike,
            stop=leg.stop,
        )
        for leg in straddle(Level("pct", cfg.stop_pct))
    )
    return LegStrategy(
        LegsConfig(
            legs=legs,
            entry=cfg.entry,
            exit=cfg.exit,
            trail_to_cost=cfg.trail_to_cost,
        )
    )
