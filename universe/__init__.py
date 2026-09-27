"""What is in an index, and which indices there are.

A rotation graph needs a benchmark and a list of things to measure against it.
Neither is derivable from prices, so both live here: the sector indices the
market is usually cut into, the broad indices, and who belongs to each.

Membership is fetched from NSE's own published lists rather than written down,
because a list in a repository goes stale silently - a stock leaves the Nifty 50
and nothing breaks, it just quietly measures the wrong universe for months.
"""

from universe.nse import (
    INDICES,
    IndexSpec,
    Membership,
    constituents,
    index,
    sectors,
)

__all__ = [
    "INDICES",
    "IndexSpec",
    "Membership",
    "constituents",
    "index",
    "sectors",
]
