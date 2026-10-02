"""Assembling what the alert watcher judges.

The seam between the watcher (which knows nothing about brokers or HTTP) and the
rest of the app. It reads exactly what the baskets endpoint reads, through the
same pricing helpers, so the desk and the watcher cannot come to disagree about
what a position is worth - one of them warning about a worst case the other does
not show would be worse than no warning.

Blocking on purpose: broker calls and SQLite are synchronous, and the watcher
runs this on a worker thread rather than pretending otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from alerting.models import PositionView, WatchKind
from api.basket_view import basket_view, rows_for_basket, spot_for
from api.pricing import basket_live_curve
from api.store import open_db
from broker.base import OptionsBroker, PerpetualsData
from broker.contracts import ContractCodec
from broker.models import OptionChain
from execution.basket_status import get_basket_payoff
from execution.portfolio_status import get_portfolio_status
from feeds.fetch import Feeds
from jobs.alert_watcher import Inputs
from storage.alert_repo import list_watches
from storage.basket_repo import list_baskets as repo_list_baskets
from streaming import TickHub
from venues.instruments import instrument

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PositionView:
    """A leveraged position, flattened to what the rules read.

    A small copy rather than passing the adapter's model through, because the rules
    want a liquidation *distance* and the model offers a method that needs a price.
    Working that out once here is better than the rules knowing where prices live.
    """

    symbol: str
    name: str
    side: str
    quantity: float
    leverage: float
    liquidation_distance: float | None
    protected: bool
    position_id: str


def gather(
    db_path: Path,
    broker: OptionsBroker,
    feeds: Feeds,
    hub: TickHub | None = None,
    perps: object | None = None,
    *,
    codec: ContractCodec,
) -> Inputs:
    """One pass's worth of data, with partial failure preferred over none.

    The P&L and the structures are fetched separately: if the broker refuses one
    the other is still worth judging, because an expiry warning does not need a
    live price. What is *not* done is passing an absent source off as an empty
    one - `events_loaded` carries that distinction, since the two mean opposite
    things to the engine.
    """
    total_pnl: float | None = None
    try:
        status = get_portfolio_status(broker)
        total_pnl = status.total_pnl
    except Exception:  # noqa: BLE001 - one unavailable source must not stop the pass
        log.warning("alert pass could not read the portfolio", exc_info=True)

    views = []
    conn = open_db(db_path)
    try:
        baskets = repo_list_baskets(conn)
        watched = sorted(
            {
                w.symbol
                for w in list_watches(conn, enabled_only=True)
                if w.kind is WatchKind.PRICE and w.symbol
            }
        )
    finally:
        conn.close()

    # Each symbol to the venue that actually lists it. Asking the options broker
    # for BTCUSDT does not come back empty - it raises KeyError on a response
    # shape it cannot read - and because its quotes are one batched call, a single
    # crypto symbol in the list stopped every price watch working, index ones
    # included. The alerts panel offers crypto symbols on the crypto desk, so
    # that was a watch anybody would have created.
    quotes: dict[str, float] = {}
    streamed = [s for s in watched if instrument(s) is not None]
    polled = [s for s in watched if instrument(s) is None]

    if streamed and hub is not None:
        # Free and already live: the stream delivered these, so there is nothing
        # to request. A symbol the stream has not carried yet is absent rather
        # than zero, which is what keeps a "below" watch from firing on startup.
        # Named `mark` rather than `p`: a walrus here once shadowed a loop
        # variable further down and mypy caught it as a float with no position id.
        quotes.update({s: mark for s in streamed if (mark := hub.price(s)) is not None})

    # Only what something is actually watching. A quote request costs budget, and
    # watching nothing should cost nothing.
    if polled:
        try:
            quotes.update({sym: q.ltp for sym, q in broker.get_quote(polled).items()})
        except Exception:  # noqa: BLE001 - a missed quote is a late alert, not a failed pass
            log.warning("alert pass could not read quotes for %s", polled, exc_info=True)

    chains: dict[tuple[str, str], OptionChain] = {}
    for basket in baskets:
        payoff = get_basket_payoff(basket)
        try:
            valued = basket_live_curve(basket, payoff, broker, codec, chains)
        except Exception:  # noqa: BLE001 - an unpriced basket is still worth judging
            valued = ([], None, None)
        views.append(
            basket_view(
                basket, codec,
                valued,
                rows_for_basket(basket, chains),
                spot_for(basket, chains),
            )
        )

    # Leveraged positions, if that venue is configured. Priced from the stream,
    # because the venue's position payload carries no mark price and the hub
    # already has one.
    positions: list[PositionView] = []
    positions_loaded = perps is None
    if isinstance(perps, PerpetualsData):
        try:
            for held in perps.get_perp_positions():
                listed = instrument(held.symbol)
                mark = held.mark_price or (
                    hub.price(held.symbol) if hub is not None else None
                )
                positions.append(
                    _PositionView(
                        symbol=held.symbol,
                        name=listed.name if listed else held.symbol,
                        side=held.side,
                        quantity=held.quantity,
                        leverage=held.leverage,
                        liquidation_distance=held.liquidation_distance(mark),
                        protected=held.is_protected,
                        position_id=held.position_id,
                    )
                )
            positions_loaded = True
        except Exception:  # noqa: BLE001 - one unavailable venue must not stop the pass
            log.warning("alert pass could not read perpetual positions", exc_info=True)

    cached = feeds.events()
    # Answered at all? A calendar that has never been fetched, or whose last
    # fetch failed, cannot be judged - see Inputs.events_loaded.
    events_loaded = cached.fetched_at is not None and cached.error is None

    return Inputs(
        total_pnl=total_pnl,
        baskets=views,
        events=cached.events,
        events_loaded=events_loaded,
        quotes=quotes,
        positions=positions,
        positions_loaded=positions_loaded,
    )
