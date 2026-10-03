"""Fyers' order socket: word that the account changed.

Orders, trades and positions are pushed the moment they change. They are used
here as a signal rather than as data: the desk already knows how to read the
account, and what it lacked was knowing *when* - so it polled every eight
seconds for something that changes only on a fill. An event says "read it
again"; the figures still come from the one place they always did, and the
prices between fills come from the data socket.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from broker.fyers.socket import CHECK_EVERY, SdkSocket, Supervised, prepare_sdk
from paths import LOGS_DIR

log = logging.getLogger(__name__)

#: What the desk subscribes to. "OnGeneral" (price alerts, eDIS, logins) is left
#: out: none of it changes a position.
SUBSCRIBED = "OnOrders,OnTrades,OnPositions"


@dataclass(frozen=True)
class AccountEvent:
    """Something in the account changed."""

    #: "orders", "trades" or "positions".
    kind: str
    at: datetime


class OrderSocket(SdkSocket, Protocol):
    def subscribe(self, data_type: str) -> None: ...


#: Builds a socket: (client_id:token, on_open, on_event(kind, message), on_error, on_close).
OrderSocketFactory = Callable[
    [
        str,
        Callable[[], None],
        Callable[[str, Any], None],
        Callable[[Any], None],
        Callable[[Any], None],
    ],
    OrderSocket,
]


def _fyers_order_socket(
    login: str,
    on_open: Callable[[], None],
    on_event: Callable[[str, Any], None],
    on_error: Callable[[Any], None],
    on_close: Callable[[Any], None],
) -> OrderSocket:
    from fyers_apiv3.FyersWebsocket import order_ws

    prepare_sdk()
    LOGS_DIR.mkdir(exist_ok=True)
    # The SDK's order socket is a singleton - constructing another returns the
    # first and re-runs its __init__ - so the supervisor's close-then-build on a
    # reopen is the only order that works.
    socket: OrderSocket = order_ws.FyersOrderSocket(
        access_token=login,
        write_to_file=False,
        log_path=f"{LOGS_DIR}/",
        on_orders=lambda m: on_event("orders", m),
        on_trades=lambda m: on_event("trades", m),
        on_positions=lambda m: on_event("positions", m),
        on_general=lambda m: None,
        on_error=on_error,
        on_connect=on_open,
        on_close=on_close,
        reconnect=True,
    )
    # A daemon thread, as for the data socket: otherwise it outlives shutdown.
    socket.background_flag = True  # type: ignore[attr-defined]
    return socket


class FyersAccountStream(Supervised):
    """Order, trade and position changes, delivered on the event loop."""

    name = "fyers account stream"

    def __init__(
        self,
        socket_factory: OrderSocketFactory = _fyers_order_socket,
        token: Callable[[], tuple[str, str]] | None = None,
        check_every: float = CHECK_EVERY,
    ) -> None:
        super().__init__(token, check_every)
        self._make = socket_factory
        self._on_event: Callable[[AccountEvent], None] | None = None
        self.events_received = 0

    async def start(self, on_event: Callable[[AccountEvent], None]) -> None:
        """Connect and subscribe. Returns once the socket is open."""
        self._on_event = on_event
        await self._begin()

    def _build(self, login: str) -> OrderSocket:
        return self._make(login, self._opened, self._event, self._error, self._closed)

    def _opened(self) -> None:
        socket = self._really_open()
        if socket is None:
            return
        socket.subscribe(data_type=SUBSCRIBED)  # type: ignore[attr-defined]
        log.info("fyers account stream subscribed")

    def _event(self, kind: str, message: Any) -> None:
        self._on_loop(self._deliver, AccountEvent(kind=kind, at=datetime.now(UTC)))

    def _deliver(self, event: AccountEvent) -> None:
        self.events_received += 1
        if self._on_event is not None:
            self._on_event(event)
