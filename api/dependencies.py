"""FastAPI dependency providers.

Kept as thin functions (not constructed at import time) so tests can
override them via `app.dependency_overrides` with a `FakeBroker`/temp DB
path, without ever needing real Fyers credentials or touching the real
`data/trading.db` file.
"""

from __future__ import annotations

from pathlib import Path

from broker.base import Broker
from broker.fyers import FyersBroker
from settings import load_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "trading.db"


def get_broker() -> Broker:
    settings = load_settings()
    return FyersBroker(client_id=settings.fyers_client_id, access_token=settings.fyers_access_token)


def get_db_path() -> Path:
    return DEFAULT_DB_PATH
