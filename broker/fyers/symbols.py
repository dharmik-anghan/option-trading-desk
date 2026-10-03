"""Reading Fyers' contract symbols, which follow the exchange's convention.

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

from broker.models import Expiry, OptionType

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


_CONTRACT = re.compile(
    r"^(?P<series>[A-Z]+:[A-Z&]+\d{2}(?:[A-Z]{3}|[1-9OND]\d{2}))"
    r"(?P<strike>\d+(?:\.\d+)?)(?P<kind>CE|PE)$"
)


def parse_contract(symbol: str) -> tuple[str, float, OptionType] | None:
    """An option symbol's series, strike and type, or None if it is not one.

    "NSE:NIFTY26OCT23100CE" -> ("NSE:NIFTY26OCT", 23100.0, "CE"), and a weekly
    "NSE:NIFTY2692225000CE" -> ("NSE:NIFTY26922", 25000.0, "CE"). The expiry is
    five characters in both spellings - YYMON, or YY, a month digit or O/N/D,
    and DD - so the strike is whatever follows it. For reading a fill that
    arrives with nothing but its symbol.
    """
    match = _CONTRACT.match(symbol)
    if match is None:
        return None
    kind: OptionType = "CE" if match["kind"] == "CE" else "PE"
    return match["series"], float(match["strike"]), kind


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


def futures_symbol(option_symbol: str, strike: float) -> str | None:
    """The futures contract matching an option's underlying and expiry.

    Built from the option's own symbol - "NSE:NIFTY26OCT23100PE" becomes
    "NSE:NIFTY26OCTFUT" - rather than assembled from an index name, because
    the index and its derivatives are not named alike: NIFTYBANK-INDEX trades
    options as BANKNIFTY. Taking the prefix the exchange already used avoids
    having to know that.
    """
    prefix = series_prefix(option_symbol, strike)
    return None if prefix is None else prefix + "FUT"


_MONTHLY_FUTURE = re.compile(r"^(?P<ex>[A-Z]+):(?P<name>[A-Z]+)(?P<yy>\d{2})(?P<mon>[A-Z]{3})FUT$")


def later_future(future: str, months: int) -> str | None:
    """The monthly future `months` after this one: "NSE:NIFTY26OCTFUT", 1 ->
    "NSE:NIFTY26NOVFUT", across a year end too. None for a symbol that is not
    a monthly future."""
    m = _MONTHLY_FUTURE.match(future)
    if m is None or m["mon"] not in _MONTH_ABBR:
        return None
    index = _MONTH_ABBR.index(m["mon"]) + months
    year = int(m["yy"]) + index // 12
    return f"{m['ex']}:{m['name']}{year % 100:02d}{_MONTH_ABBR[index % 12]}FUT"


class FyersSymbols:
    """This module as a `broker.contracts.ContractCodec`."""

    def parse_contract(self, symbol: str) -> tuple[str, float, OptionType] | None:
        return parse_contract(symbol)

    def series_prefix(self, symbol: str, strike: float) -> str | None:
        return series_prefix(symbol, strike)

    def expiry_for_symbol(self, symbol: str, expiries: list[Expiry]) -> Expiry | None:
        return expiry_for_symbol(symbol, expiries)

    def futures_symbol(self, option_symbol: str, strike: float) -> str | None:
        return futures_symbol(option_symbol, strike)


SYMBOLS = FyersSymbols()
