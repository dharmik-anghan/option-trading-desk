"""The venue catalogue over HTTP, and that the wiring behind it is complete.

The two "agrees with" tests are the point: a venue in the registry with no
adapter factory is a 500 waiting to happen, and a capability the frontend is
told about but the adapter cannot serve is a panel that never fills.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.dependencies import BROKER_FACTORIES
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
    assert set(VENUES) == set(BROKER_FACTORIES), (
        "every venue in the catalogue needs an adapter factory in api/dependencies.py"
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
