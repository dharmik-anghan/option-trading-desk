"""Guards that apply to every test.

One job: no test may reach a real venue. This is not belt-and-braces - it already
happened. When dry-run was removed from the order path, a test posting a sound
order built a real Shark adapter from the credentials in `.env` and attempted to
place a live order. The venue refused it for an unrelated reason, so nothing was
opened, and the only thing standing between that test and a real position was
luck.

The API tests override `get_broker`, which covers the options desk. They do not
override `broker_for`, which is how the perpetuals endpoints resolve a venue - and
that is the gap. Rather than ask every test to remember, the adapter factory is
replaced here for the whole suite, so reaching the venue is impossible by
construction and a test that needs a broker has to say so.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


class VenueReachedInTest(AssertionError):
    """A test tried to build a live venue adapter."""


@pytest.fixture(autouse=True)
def _no_live_venues(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace every real adapter factory with one that refuses.

    A test wanting broker behaviour stubs `broker_for` or `get_broker` itself,
    which is explicit and local. A test that forgets gets a loud failure naming
    this file rather than a silent request to a live account.
    """
    import api.dependencies as dependencies

    def refuse(name: str) -> object:
        def factory() -> object:
            raise VenueReachedInTest(
                f"A test tried to build the live {name} adapter. Stub broker_for or "
                f"get_broker in the test instead - see tests/conftest.py."
            )

        return factory

    monkeypatch.setitem(dependencies.BROKER_FACTORIES, "shark", refuse("Shark"))
    monkeypatch.setattr(dependencies, "_build_shark", refuse("Shark"))
    yield


class NseReachedInTest(AssertionError):
    """A test tried to fetch from NSE."""


@pytest.fixture(autouse=True)
def _no_nse(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """NSE's pre-open, refused, and the app's own database moved aside.

    The same accident as above in a quieter form: a test entering `TestClient`
    runs the app's lifespan, which starts the pre-open recorder, which fetched
    NSE for real and wrote the answer into `data/trading.db`. The dependency
    override on `get_db_path` does not reach the lifespan - it calls the function
    directly - so `DB_PATH` points it at a scratch file instead.
    """
    import tempfile

    import marketdata.nse_preopen as nse_preopen

    def refuse(*args: object, **kwargs: object) -> object:
        raise NseReachedInTest("A test tried to fetch from NSE. Stub nse_preopen.fetch.")

    monkeypatch.setattr(nse_preopen, "fetch", refuse)
    with tempfile.TemporaryDirectory() as scratch:
        monkeypatch.setenv("DB_PATH", f"{scratch}/trading.db")
        yield
