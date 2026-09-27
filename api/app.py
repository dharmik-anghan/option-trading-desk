"""Dashboard API: composition only.

This module used to hold every endpoint and every helper they shared, which was
manageable while the desk had one venue and one asset class. It no longer is, so
the endpoints live in `api/routers/`, grouped by what they are about, and what
is left here is assembly: the app, its middleware, the broker error handler, the
routers, and the built frontend.

Where the safety model lives, since it is easy to lose in a split: the review
step (`GET /api/strategies/{name}`) returns the same pre-trade checks that
`POST /api/orders/place` re-runs and enforces server-side. Placement is refused
with a 400 when they fail - never merely hidden behind a disabled button, since
a client-side-only gate is trivially bypassable. The deliberate-click part of
that model is the frontend's review screen.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from alerting.watcher import Watcher
from api.alert_inputs import gather
from api.dependencies import broker_for, get_broker, get_db_path, get_feeds, get_holidays
from api.errors import broker_error_handler
from api.routers import (
    alerts,
    backtest,
    bars,
    baskets,
    feeds,
    market,
    orders,
    perps,
    portfolio,
    rrg,
    strategies,
    system,
)
from api.store import open_db
from broker.errors import BrokerError
from broker.session import in_session
from broker.shark.stream import SharkStream
from marketdata import BarService
from marketdata.holder import BarStoreHolder
from marketdata.venue import VenueBars
from notify import Telegram, TelegramConfig
from settings import load_settings
from storage.vol_recorder import VolRecorder
from streaming import TickHub
from venues import for_venue, option_underlyings
from venues import get as get_venue

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Run the alert watcher for as long as the app is up.

    Alerts used to be computed in the browser, so nothing was watching when the
    tab was closed. This is the task that fixes that, and it is the reason the
    app has a lifespan at all - the websocket work that comes next will hang off
    the same hook.

    Everything is best-effort: if the watcher cannot be built, the desk still
    serves. A trading screen that refuses to start because a notifier is
    misconfigured is worse than one that starts and says alerts are off, which is
    what `/api/alerts` reports.
    """
    settings = load_settings()
    notifier = (
        Telegram(
            TelegramConfig(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
        )
        if settings.has_telegram
        else None
    )
    db_path = get_db_path()
    # Make sure the schema is current before the watcher's first pass, which runs
    # on a worker thread and would otherwise race the first request to do it.
    open_db(db_path).close()

    feeds_cache = get_feeds()
    # Built before the watcher so price watches on streamed instruments read from
    # it rather than asking a broker that does not list them.
    hub = TickHub()

    # Set when the app is going down, so a long-lived response can end itself. An
    # SSE stream loops until its reader leaves, and on shutdown the reader has not
    # left - uvicorn waits for the response to finish while the response waits for
    # the reader. That hung a reload with a desk open, and would hang `docker stop`.
    shutting_down = asyncio.Event()
    application.state.shutting_down = shutting_down

    # The bar store, and the venue registered as a source under its own name. The
    # venue's candles are what a trading chart shows - the instrument an order would
    # be in - and storing them is what turns a few hundred bars into a history.
    #
    # Opened lazily and retried, not once here. DuckDB permits one writer, so a
    # copy of this app still shutting down or a backfill just finishing holds the
    # file for a few seconds - and a desk that tried once at boot answered "the
    # bar store is not open" for the rest of its life, with the file unlocked the
    # whole time. See `marketdata/holder.py`.
    def _register(service: BarService) -> None:
        if not settings.has_shark:
            return
        shark = broker_for(get_venue("shark"))
        service.register(
            "shark",
            VenueBars(shark),
            {i.symbol: i.symbol for i in for_venue("shark")},
        )

    bars = BarStoreHolder(get_db_path().parent / "bars.duckdb", on_open=_register)
    application.state.bars = bars

    def perps_broker() -> object | None:
        """The perpetuals adapter, or None when that venue is not configured.

        Built per pass rather than held, so a rotated key is picked up, and
        returning None keeps the watcher working on an options-only setup.
        """
        if not settings.has_shark:
            return None
        try:
            return broker_for(get_venue("shark"))
        except Exception:  # noqa: BLE001 - a venue that cannot be built is not watched
            log.warning("could not build the perpetuals adapter for the alert pass")
            return None

    watcher = Watcher(
        gather=lambda: gather(db_path, get_broker(), feeds_cache, hub, perps_broker()),
        open_conn=lambda: open_db(db_path),
        notifier=notifier,
    )
    application.state.alert_watcher = watcher
    application.state.alert_notifier = notifier

    task = asyncio.create_task(watcher.run_forever(), name="alert-watcher")
    log.info("alert watcher started (telegram=%s)", notifier is not None)

    # What options cost, written down each session. The one piece of market data
    # on this desk that cannot be fetched again: a price history can be
    # backfilled from any source years later, and what the market was charging
    # for a straddle on a Tuesday afternoon is gone when the session ends. Its
    # own task, because it has to run with the tab closed.
    recorder = VolRecorder(
        underlyings=option_underlyings(),
        fetch_chain=lambda symbol, strikes: get_broker().get_option_chain(
            symbol, strike_count=strikes
        ),
        open_conn=lambda: open_db(db_path),
        in_session=lambda at: in_session(at, get_holidays().dates()),
    )
    application.state.vol_recorder = recorder
    vol_task = asyncio.create_task(recorder.run_forever(), name="vol-recorder")
    log.info("volatility recorder started for %d underlyings", len(option_underlyings()))

    # The perpetuals venue pushes prices rather than being polled for them, which
    # is not a nicety: its budget is 60 requests a minute against Fyers' ~200, and
    # three instruments across several panels would spend it on nothing. The hub
    # holds the latest so everything else reads from memory.
    stream: SharkStream | None = None
    application.state.tick_hub = hub
    application.state.tick_stream = None
    if settings.has_shark:
        stream = SharkStream()
        try:
            await stream.start([i.symbol for i in for_venue("shark")], hub.publish)
            application.state.tick_stream = stream
            log.info("shark tick stream connected")
        except Exception:  # noqa: BLE001 - a desk that will not start is worse
            log.warning("shark tick stream could not connect", exc_info=True)
            await stream.stop()
            stream = None

    try:
        yield
    finally:
        shutting_down.set()
        task.cancel()
        vol_task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(asyncio.CancelledError):
            await vol_task
        if stream is not None:
            await stream.stop()
        bars.close()
        log.info("alert watcher stopped")


app = FastAPI(title="Option Strategy Dashboard API", lifespan=lifespan)

# Local dev only: the Vite dev server runs on a different port than uvicorn.
# Tighten this (or drop it behind a reverse proxy) before exposing this
# beyond localhost - see docs/SETUP.md.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    # PUT included because the alert thresholds are edited with one. Its absence
    # was invisible from the server's side - the endpoint worked, and every
    # request from the browser died in the preflight instead, which reaches the
    # page as "Failed to fetch" with nothing in the server log.
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Broker failures become statuses a client can act on, with a stable `code`,
# instead of an opaque 500 that leaves the desk frozen with no explanation.
app.add_exception_handler(BrokerError, broker_error_handler)

# One router per area of the desk. Order is presentational only - every path is
# distinct - except that all of them must be registered before the mount below.
for _router in (
    system.router,
    alerts.router,
    backtest.router,
    bars.router,
    portfolio.router,
    market.router,
    perps.router,
    rrg.router,
    feeds.router,
    strategies.router,
    orders.router,
    baskets.router,
):
    app.include_router(_router)

# ---------------------------------------------------------------------------
# The built frontend, when there is one.
#
# Mounted last so every /api route above is matched first; a mount at "/" would
# otherwise swallow them. Absent in development, where Vite serves the frontend
# on its own port and CORS above lets it through - so this is a no-op then, and
# the two setups need no switch between them.
_UI_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class SinglePage(StaticFiles):
    """Static files, with the app's own index.html for any path that is not a file.

    Needed because `html=True` alone does not do this: it serves index.html for a
    directory and 404s for everything else, so /options and /crypto answered 404 on
    a reload even though the app knows both routes. A single-page app has no files
    at its routes by definition, so the fallback has to be here.

    Only for reads: a POST to a path that does not exist is a mistake worth
    reporting, not a page to render.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as missing:
            wants_a_file = "." in path.rsplit("/", 1)[-1]
            if missing.status_code != 404 or wants_a_file:
                # A missing script or stylesheet is a broken build, and answering
                # it with a page of HTML turns that into a baffling parse error
                # in the console instead of an honest 404.
                raise
            return await super().get_response("index.html", scope)


if _UI_DIR.is_dir():
    # Mounted last so every /api route above is matched first; a mount at "/"
    # would otherwise swallow them.
    app.mount("/", SinglePage(directory=_UI_DIR, html=True), name="ui")
