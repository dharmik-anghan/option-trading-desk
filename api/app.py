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

from alerting.watcher import Watcher
from api.alert_inputs import gather
from api.dependencies import get_broker, get_db_path, get_feeds
from api.errors import broker_error_handler
from api.routers import alerts, baskets, feeds, market, orders, portfolio, strategies, system
from api.store import open_db
from broker.errors import BrokerError
from notify import Telegram, TelegramConfig
from settings import load_settings

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
    watcher = Watcher(
        gather=lambda: gather(db_path, get_broker(), feeds_cache),
        open_conn=lambda: open_db(db_path),
        notifier=notifier,
    )
    application.state.alert_watcher = watcher
    application.state.alert_notifier = notifier

    task = asyncio.create_task(watcher.run_forever(), name="alert-watcher")
    log.info("alert watcher started (telegram=%s)", notifier is not None)
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        log.info("alert watcher stopped")


app = FastAPI(title="Option Strategy Dashboard API", lifespan=lifespan)

# Local dev only: the Vite dev server runs on a different port than uvicorn.
# Tighten this (or drop it behind a reverse proxy) before exposing this
# beyond localhost - see docs/SETUP.md.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["GET", "POST", "DELETE"],
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
    portfolio.router,
    market.router,
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
if _UI_DIR.is_dir():
    # html=True serves index.html for unknown paths, which a single-page app
    # needs to survive a reload on any route.
    app.mount("/", StaticFiles(directory=_UI_DIR, html=True), name="ui")
