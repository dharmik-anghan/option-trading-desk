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

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.errors import broker_error_handler
from api.routers import baskets, feeds, market, orders, portfolio, strategies, system
from broker.errors import BrokerError

app = FastAPI(title="Option Strategy Dashboard API")

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
