"""What each venue lists, where the instruments do not share one calendar.

The options desk needs no such list: every NSE index keeps exchange hours, so the
venue's session answers for all of them. A crypto futures venue does not work
that way - BTCUSDT never closes, while the gold and oil perpetuals beside it
track underlying futures that stand down at the weekend. Same exchange, same API,
different weeks.

Only instruments that differ from their venue's default need an entry. The rest
inherit it, so this stays a list of exceptions rather than a catalogue to keep in
step with 343 contracts.
"""

from __future__ import annotations

from dataclasses import dataclass

from venues.calendar import Session


@dataclass(frozen=True)
class Instrument:
    """One tradable contract."""

    symbol: str
    #: What to call it on screen. "XAUUSDT" is the ticker, "Gold" is the thing.
    name: str
    venue_id: str
    session: Session
    #: What a price is quoted in.
    quote_asset: str
    #: Smallest price step the venue accepts, for display and for rounding an
    #: order's price. From the venue's own pricePrecision.
    price_dp: int
    quantity_dp: int


#: The three the desk starts with. Their names come from the venue's own
#: exchangeInfo: XAU is "Gold Derivatives", CL is "Crude Oil Futures" - the
#: WTI contract, which is what "USOIL" means elsewhere.
SHARK_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument(
        symbol="BTCUSDT",
        name="Bitcoin",
        venue_id="shark",
        # Crypto: no close, no holidays.
        session=Session.ALWAYS,
        quote_asset="USDT",
        price_dp=1,
        quantity_dp=3,
    ),
    Instrument(
        symbol="XAUUSDT",
        name="Gold",
        venue_id="shark",
        # Round the clock on weekdays, shut at the weekend: the underlying
        # futures do not trade Saturday or Sunday, whatever the exchange does.
        session=Session.WEEKDAYS_24H,
        quote_asset="USDT",
        price_dp=2,
        quantity_dp=3,
    ),
    Instrument(
        symbol="CLUSDT",
        name="Crude oil",
        venue_id="shark",
        session=Session.WEEKDAYS_24H,
        quote_asset="USDT",
        price_dp=2,
        quantity_dp=3,
    ),
)

_BY_SYMBOL = {i.symbol: i for i in SHARK_INSTRUMENTS}


def instrument(symbol: str) -> Instrument | None:
    """The instrument, or None if the desk does not list it."""
    return _BY_SYMBOL.get(symbol)


def for_venue(venue_id: str) -> list[Instrument]:
    return [i for i in SHARK_INSTRUMENTS if i.venue_id == venue_id]
