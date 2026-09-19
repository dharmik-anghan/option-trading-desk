"""In-memory `Broker` test double.

Used by contract tests (so the contract is checkable without hitting a real
broker) and by anything above `broker/` that wants to test against a broker
without network calls.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from broker.base import Broker
from broker.models import Candle, Greeks, OptionChain, OptionChainRow, Quote


class FakeBroker(Broker):
    def __init__(self, underlying_ltp: float = 23350.0) -> None:
        self.underlying_ltp = underlying_ltp

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        now = datetime.now(UTC)
        return {
            symbol: Quote(
                symbol=symbol,
                ltp=self.underlying_ltp,
                open=self.underlying_ltp,
                high=self.underlying_ltp,
                low=self.underlying_ltp,
                prev_close=self.underlying_ltp,
                volume=0,
                bid=self.underlying_ltp - 0.5,
                ask=self.underlying_ltp + 0.5,
                timestamp=now,
            )
            for symbol in symbols
        }

    def get_option_chain(self, symbol: str, strike_count: int = 10) -> OptionChain:
        atm = round(self.underlying_ltp / 50) * 50
        rows: list[OptionChainRow] = []
        for i in range(-strike_count, strike_count + 1):
            strike = atm + i * 50
            for option_type in ("CE", "PE"):
                rows.append(
                    OptionChainRow(
                        symbol=f"{symbol}-{int(strike)}-{option_type}",
                        strike=strike,
                        option_type=option_type,
                        ltp=max(1.0, abs(atm - strike) * 0.1),
                        bid=0.0,
                        ask=0.0,
                        oi=0,
                        prev_oi=0,
                        volume=0,
                        greeks=Greeks(delta=0.5, gamma=0.001, theta=-1.0, vega=1.0, iv=15.0),
                    )
                )
        return OptionChain(
            underlying_symbol=symbol,
            underlying_ltp=self.underlying_ltp,
            fetched_at=datetime.now(UTC),
            rows=rows,
        )

    def get_history(
        self, symbol: str, resolution: str, date_from: date, date_to: date
    ) -> list[Candle]:
        candles: list[Candle] = []
        current = date_from
        while current <= date_to:
            candles.append(
                Candle(
                    timestamp=datetime.combine(current, datetime.min.time(), tzinfo=UTC),
                    open=self.underlying_ltp,
                    high=self.underlying_ltp,
                    low=self.underlying_ltp,
                    close=self.underlying_ltp,
                    volume=0,
                )
            )
            current += timedelta(days=1)
        return candles

    def subscribe_ticks(self, symbols: list[str], on_tick: Callable[[Quote], None]) -> None:
        for quote in self.get_quote(symbols).values():
            on_tick(quote)
