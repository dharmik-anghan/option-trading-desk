"""Running a rule over history, without flattering it.

A backtest is easy to write and easy to write wrongly, and the wrong ones do not
look wrong - they look excellent. Everything here is arranged around the handful
of mistakes that produce a beautiful curve from an idea that loses money.

    resample.py  a higher timeframe, defined as the bars underneath it
    view.py      what was knowable at an instant, and nothing later
    market.py    what an instrument is worth and what it costs
    models.py    a rule's intent, and a closed trade
    engine.py    the loop: decide on a close, fill on the next open
    metrics.py   the figures, including the two that stop self-deception

The engine is instrument-agnostic on purpose. Perpetuals are what it was built
for and what it is validated against, but nothing in the loop knows that: an
option's lot size, its expiry and the securities transaction tax that falls on
one leg only, or a share's borrow cost, all belong behind `Market`.
"""

from backtest.engine import Execution, Result, Rule, run
from backtest.market import Costs, FundingSchedule, Market, PerpetualMarket, Side
from backtest.metrics import Metrics, measure
from backtest.models import Action, Exit, Intent, Position, Trade
from backtest.view import Frame, View

__all__ = [
    "Action",
    "Costs",
    "Execution",
    "Exit",
    "Frame",
    "FundingSchedule",
    "Intent",
    "Market",
    "Metrics",
    "PerpetualMarket",
    "Position",
    "Result",
    "Rule",
    "Side",
    "Trade",
    "View",
    "measure",
    "run",
]
