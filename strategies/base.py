"""The `Strategy` abstraction.

Scope for Phase 3 is deliberately just "given a chain, which legs does this
strategy trade" (`build_legs`). Entry-condition evaluation (e.g. "only enter
if today's straddle premium is elevated vs. its 20-day average") depends on
historical analytics queries that aren't built yet, and exit/adjustment
rules depend on live position state — both land in Phase 4/5 once that state
exists, rather than as unused hooks here now.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from analytics.payoff import Leg
from broker.models import OptionChain


class Strategy(ABC):
    name: str

    @abstractmethod
    def build_legs(self, chain: OptionChain) -> list[Leg]:
        """Select strikes from `chain` and return the legs this strategy trades."""
        ...
