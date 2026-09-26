"""Reading the exchange's contract-symbol convention.

A basket records the contracts it holds, not which expiry they belong to -
and the expiry is what a live (pre-expiry) valuation needs, to know how much
time is left. It is recoverable from the symbols themselves, because NSE
encodes the expiry into every derivative symbol:

    NSE:NIFTY26SEP23100CE    monthly -> two-digit year + three-letter month
    NSE:NIFTY26O0623100PE    weekly  -> two-digit year + month letter + day

The month letter runs "1".."9" for January to September and then "O", "N",
"D" - so October cannot be "10", which is why weeklies are not simply the
monthly form with a day glued on.

Rather than parse a symbol and hope, this goes the other way: the broker
tells us which expiries exist and on what dates, and we ask which of those
dates would have produced the symbol in hand. A date the broker never listed
can therefore never be inferred.
"""

from __future__ import annotations

import re

from broker.models import Expiry

_MONTH_ABBR = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)
# September is "9"; October onward cannot be two digits inside a symbol.
_MONTH_LETTER = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "O", "N", "D")


def _parse_listed_date(date: str) -> tuple[int, int, int] | None:
    """`"27-10-2026"` -> `(2026, 10, 27)`, or None if it isn't that shape."""
    parts = date.split("-")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(p) for p in parts)
    except ValueError:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def expiry_infix(date: str, *, weekly: bool) -> str | None:
    """The one symbol fragment an expiry on `date` produces, or None.

    Exactly one form, chosen by the broker's own weekly/monthly flag. An
    earlier version returned both forms as a hedge, which backfired: a
    monthly symbol ("...26OCT...") matched a *weekly* expiry's monthly
    fallback and the basket was attributed to the wrong date. The flag is
    authoritative - a last-weekly-of-the-month is flagged monthly by the
    broker and spelled that way too.
    """
    parsed = _parse_listed_date(date)
    if parsed is None:
        return None
    year, month, day = parsed
    yy = f"{year % 100:02d}"
    if weekly:
        return f"{yy}{_MONTH_LETTER[month - 1]}{day:02d}"
    return f"{yy}{_MONTH_ABBR[month - 1]}"


def series_prefix(symbol: str, strike: float) -> str | None:
    """The part of a contract symbol naming its underlying and expiry.

    "NSE:NIFTY26OCT23100CE" -> "NSE:NIFTY26OCT". Two legs whose prefixes differ
    are in different expiries, which is all a calendar or a diagonal is - and
    it can be told without asking the broker what expiries exist.

    The strike is passed in rather than guessed at, for the same reason as in
    `expiry_infix`: a weekly's expiry ends in digits too.
    """
    for suffix in ("CE", "PE"):
        if not symbol.endswith(suffix):
            continue
        head = symbol[: -len(suffix)]
        for text in _strike_spellings(strike):
            if head.endswith(text) and len(head) > len(text):
                return head[: -len(text)]
    return None


def _strike_spellings(strike: float) -> list[str]:
    """How a strike might appear in a symbol, most likely first."""
    out = []
    if strike == int(strike):
        out.append(str(int(strike)))
    text = f"{strike:g}"
    if text not in out:
        out.append(text)
    return out


def expiry_for_symbol(symbol: str, expiries: list[Expiry]) -> Expiry | None:
    """Which listed expiry `symbol` belongs to, or None if none of them fit.

    The fragment is anchored rather than merely searched for. January to
    September weeklies are all digits ("26507" for 7 May), which can occur
    inside a strike price too - "NIFTY26O0626507CE" contains "26507" without
    being a May contract at all. Requiring a letter before the fragment (the
    end of the underlying's name) and strike digits plus CE/PE after it
    removes that whole class of false match.
    """
    upper = symbol.upper()
    for expiry in expiries:
        infix = expiry_infix(expiry.date, weekly=expiry.weekly)
        if infix and re.search(rf"[A-Z]{re.escape(infix)}\d+(?:CE|PE)$", upper):
            return expiry
    return None


def common_expiry(symbols: list[str], expiries: list[Expiry]) -> Expiry | None:
    """The single expiry every symbol shares, or None.

    None when the symbols straddle expiries - a calendar spread has no one
    time-to-expiry, so a single pre-expiry curve would be a fiction.
    """
    if not symbols:
        return None
    matched = [expiry_for_symbol(s, expiries) for s in symbols]
    if any(m is None for m in matched):
        return None
    tokens = {m.token for m in matched if m is not None}
    if len(tokens) != 1:
        return None
    return matched[0]
