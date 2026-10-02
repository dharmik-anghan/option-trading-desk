"""The venue catalogue over HTTP, and that the wiring behind it is complete.

The two "agrees with" tests are the point: a venue in the registry with no
adapter factory is a 500 waiting to happen, and a capability the frontend is
told about but the adapter cannot serve is a panel that never fills.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from broker.factory import FACTORIES
from venues import Capability, listed
from venues.registry import VENUES


def test_lists_every_venue(client: TestClient) -> None:
    body = client.get("/api/venues").json()
    assert [v["id"] for v in body] == [spec.id for spec in listed()]


def test_describes_a_venue_fully(client: TestClient) -> None:
    fyers = next(v for v in client.get("/api/venues").json() if v["id"] == "fyers")
    assert fyers["asset_class"] == "index_options"
    assert fyers["quote_currency"] == "INR"
    assert fyers["session"] == "nse_fo"
    assert Capability.OPTION_CHAIN in fyers["capabilities"]


def test_capabilities_are_sorted_so_the_response_is_stable(client: TestClient) -> None:
    for venue in client.get("/api/venues").json():
        assert venue["capabilities"] == sorted(venue["capabilities"])


def test_every_listed_venue_can_actually_be_built() -> None:
    assert set(VENUES) == set(FACTORIES), (
        "every venue in the catalogue needs an adapter factory in broker/factory.py"
    )


def test_the_routers_are_all_registered(client: TestClient) -> None:
    # A router that is written but never included is invisible until someone
    # notices a 404, and the include list is easy to forget when adding one.
    for path in (
        "/api/health",
        "/api/venues",
        "/api/portfolio",
        "/api/news",
        "/api/baskets",
    ):
        assert client.get(path).status_code == 200, path


def test_cors_allows_every_method_the_app_actually_routes(client: TestClient) -> None:
    """The middleware's method list has to keep up with the routes.

    It did not: the alert thresholds are edited with a PUT and PUT was missing,
    which is invisible from the server's side. The endpoint works, curl works,
    and only a browser fails - in the preflight, before the request is made, so
    nothing appears in the server log and the page sees "Failed to fetch".
    """
    from api.app import app

    routed = {
        method
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
        if method not in {"HEAD", "OPTIONS"}
    }
    # Routers are nested in this FastAPI version, so walk into them too.
    for route in app.routes:
        for nested in getattr(route, "routes", []) or []:
            routed |= {
                m for m in (getattr(nested, "methods", None) or set())
                if m not in {"HEAD", "OPTIONS"}
            }

    allowed: set[str] = set()
    for method in sorted(routed):
        response = client.options(
            "/api/alerts",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": method,
            },
        )
        if response.status_code == 200:
            allowed.add(method)
    assert routed <= allowed, f"CORS blocks {sorted(routed - allowed)}, which the app routes"
