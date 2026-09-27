"""What each venue lists, where the instruments do not share one calendar.

The options desk needs no such list: every NSE index keeps exchange hours, so the
venue's session answers for all of them. This exists for everything else an
instrument carries that its venue cannot say on its behalf - what to call it, and
how finely it prices and sizes.

On hours specifically: the USDT perpetuals all run continuously, gold and oil
included. That was worth checking rather than assuming, because the underlying
futures do stand down at the weekend and it would be reasonable to expect the
perpetual to follow. It does not - a stream open on a Saturday delivered XAUUSDT
and CLUSDT ticks less than three seconds old, alongside BTCUSDT.

Only instruments the desk actually trades need an entry; the venue lists 343.
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


#: The index underlyings the options desk trades, as Fyers spells them.
#:
#: Written here because the backend needed a list and only the frontend had one:
#: anything server-side that wants "the underlyings" - the volatility recorder,
#: for one - had nowhere to read them from, and a second copy in Python would be
#: a second copy to keep in step.
#:
#: They are indices rather than instruments in the sense above, which is why they
#: carry no precision: nothing places an order in an index, only in options on
#: one. The names match the desk's own labels.
OPTION_UNDERLYINGS: tuple[tuple[str, str], ...] = (
    ("NSE:NIFTY50-INDEX", "NIFTY 50"),
    ("NSE:NIFTYBANK-INDEX", "BANK NIFTY"),
    ("NSE:FINNIFTY-INDEX", "FIN NIFTY"),
    ("NSE:MIDCPNIFTY-INDEX", "MIDCAP NIFTY"),
    ("BSE:SENSEX-INDEX", "SENSEX"),
)


def option_underlyings() -> tuple[str, ...]:
    """Just the symbols, for anything iterating over them."""
    return tuple(symbol for symbol, _ in OPTION_UNDERLYINGS)


#: The three the desk starts with. Their names come from the venue's own
#: exchangeInfo: XAU is "Gold Derivatives", CL is "Crude Oil Futures" - the
#: WTI contract, which is what "USOIL" means elsewhere.
SHARK_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument(
        symbol="BTCUSDT",
        name="Bitcoin",
        venue_id="shark",
        session=Session.ALWAYS,
        quote_asset="USDT",
        price_dp=1,
        quantity_dp=3,
    ),
    Instrument(
        symbol="XAUUSDT",
        name="Gold",
        venue_id="shark",
        # Continuous, confirmed against a live weekend stream - the perpetual
        # keeps trading even though the underlying futures do not.
        session=Session.ALWAYS,
        quote_asset="USDT",
        price_dp=2,
        quantity_dp=3,
    ),
    Instrument(
        symbol="CLUSDT",
        name="Crude oil",
        venue_id="shark",
        session=Session.ALWAYS,
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
