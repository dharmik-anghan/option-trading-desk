"""How a bar source fails, whichever source it is.

Every source - Yahoo, Binance, a broker's history - raises these, so the
service and the backfill can tell "asked too often, try later" from "cannot
answer" without knowing which source they were talking to.
"""

from __future__ import annotations


class RateLimited(Exception):
    """The source refused for being asked too often. Expected, not exceptional."""


class Unavailable(Exception):
    """The source could not answer, or answered with something unreadable."""
