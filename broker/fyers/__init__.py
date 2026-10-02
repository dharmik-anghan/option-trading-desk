"""Fyers: NSE and BSE index options.

    adapter.py      the broker, and the pure parsers it reads responses with
    auth.py         TOTP auto-login, for the daily token
    token_store.py  where the token is kept, and refreshing it
    symbols.py      reading Fyers' contract symbols

The expired F&O client, for option history, is `optbt/data/fyers.py`: it
answers the backtester's own interface, so it lives beside it.
"""

from broker.fyers.adapter import FyersBroker
from broker.fyers.symbols import SYMBOLS

__all__ = ["SYMBOLS", "FyersBroker"]
