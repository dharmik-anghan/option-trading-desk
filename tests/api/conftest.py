"""Fixtures shared by the API tests.

Moved out of `test_app.py` when the endpoints were split into routers: each
router gets its own test module, and they all need the same client wired to a
fake broker and a temporary database.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app
from api.dependencies import get_broker, get_db_path
from broker.fake import FakeBroker
from broker.models import Position


@pytest.fixture
def fake_broker() -> FakeBroker:
    return FakeBroker(
        underlying_ltp=100.0,
        positions=[
            Position(
                symbol="X-100-CE",
                net_quantity=-1,
                average_price=5.0,
                ltp=4.0,
                unrealized_pnl=100.0,
                product_type="MARGIN",
            )
        ],
        realized_pnl=50.0,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def client(fake_broker: FakeBroker, db_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_broker] = lambda: fake_broker
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield TestClient(app)
    app.dependency_overrides.clear()
