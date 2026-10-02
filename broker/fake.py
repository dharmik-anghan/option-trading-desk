"""In-memory `Broker` test double.

Used by contract tests (so the contract is checkable without hitting a real
broker) and by anything above `broker/` that wants to test against a broker
without network calls.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from broker.base import Broker
from broker.models import (
    Candle,
    Expiry,
    Fill,
    Funds,
    Greeks,
    OptionChain,
    OptionChainRow,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
)


class FakeBroker(Broker):
    def __init__(
        self,
        underlying_ltp: float = 23350.0,
        available_balance: float = 100000.0,
        positions: list[Position] | None = None,
        realized_pnl: float = 0.0,
        fills: list[Fill] | None = None,
    ) -> None:
        self.underlying_ltp = underlying_ltp
        self.available_balance = available_balance
        self.positions = positions or []
        self.realized_pnl = realized_pnl
        self.fills = fills or []
        self.placed_orders: list[OrderRequest] = []
        self._next_order_id = 1

    def get_funds(self) -> Funds:
        return Funds(
            total_balance=self.available_balance,
            utilized_margin=0.0,
            available_balance=self.available_balance,
            realized_pnl=self.realized_pnl,
        )

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.placed_orders.append(order)
        order_id = str(self._next_order_id)
        self._next_order_id += 1
        return OrderResult(order_id=order_id, message="Order submitted successfully (fake)")

    def get_positions(self) -> list[Position]:
        return self.positions

    def get_booked_pnl(self) -> float:
        return self.realized_pnl

    def get_fills(self, date_from: date, date_to: date) -> list[Fill]:
        return sorted(
            (f for f in self.fills if date_from <= f.at.date() <= date_to), key=lambda f: f.at
        )

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

    def get_option_chain(
        self, symbol: str, strike_count: int = 10, expiry_token: str = ""
    ) -> OptionChain:
        # Step with the underlying rather than a flat 50, or a small
        # `underlying_ltp` walks the ladder down through zero into negative
        # strikes - which no exchange lists, and which Black-Scholes cannot
        # price because log(spot/strike) has no value there.
        step = max(1.0, round(self.underlying_ltp / 500) * 5 or 5.0)
        atm = round(self.underlying_ltp / step) * step
        rows: list[OptionChainRow] = []
        for i in range(-strike_count, strike_count + 1):
            strike = atm + i * step
            if strike <= 0:
                continue
            # Delta has to fall as a call goes further out of the money, or
            # selecting "the 0.16-delta strike" is meaningless: with a flat
            # 0.5 everywhere, every target resolves to whichever strike came
            # first, and a four-leg structure collapses onto one strike.
            moneyness = (strike - self.underlying_ltp) / self.underlying_ltp
            call_delta = 1.0 / (1.0 + math.exp(12.0 * moneyness))
            for option_type in ("CE", "PE"):
                delta = call_delta if option_type == "CE" else call_delta - 1.0
                rows.append(
                    OptionChainRow(
                        symbol=f"{symbol}-{int(strike)}-{option_type}",
                        strike=strike,
                        option_type=option_type,
                        ltp=max(1.0, abs(atm - strike) * 0.1),
                        bid=0.0,
                        ask=0.0,
                        oi=1000,
                        prev_oi=900,
                        volume=0,
                        # a call rising and a put falling, so the two sides
                        # classify differently and tests can tell them apart
                        ltp_change=1.0 if option_type == "CE" else -1.0,
                        ltp_change_pct=2.0 if option_type == "CE" else -2.0,
                        oi_change=100,
                        oi_change_pct=11.1,
                        greeks=Greeks(
                            delta=round(delta, 4), gamma=0.001, theta=-1.0, vega=1.0, iv=15.0
                        ),
                    )
                )
        expiries = [
            Expiry(date="29-10-2026", token="1793095800", weekly=False),
            Expiry(date="05-11-2026", token="1793700600", weekly=True),
        ]
        return OptionChain(
            underlying_symbol=symbol,
            underlying_ltp=self.underlying_ltp,
            fetched_at=datetime.now(UTC),
            rows=rows,
            expiries=expiries,
            expiry_token=expiry_token or expiries[0].token,
            call_oi=0,
            put_oi=0,
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
