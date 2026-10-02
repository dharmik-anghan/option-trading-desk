"""What an option trade costs beyond its premium.

Everything is charged on **premium**, never on notional - the easiest way to get an
options backtest wrong by two orders of magnitude. A NIFTY 24000 CE at 80 with a
lot of 65 is 15.6 lakh of notional and 5,200 of premium; charges are on the 5,200.

The statutory rates have moved three times in the span the store covers, so they
are a dated table and a fill pays the rates in force on its day:

    2024-10-01  STT on options sold 0.0625% -> 0.1%; NSE charge 0.05% -> 0.03503%
    2026-04-01  STT on options sold 0.1% -> 0.15%; STT on exercise 0.125% -> 0.15%

Checked 2026-09-28 against current broker schedules and Budget 2026 coverage. The
pre-October-2024 exchange rate is the least certain figure here.

Two charges people forget, both of which matter most on the worst trades:

  STT on exercise. A long option that settles in the money is exercised, and STT
  is charged on its intrinsic value - not on the premium it was bought for.

  Brokerage is per order. A four-leg condor pays eight tickets in and out, and
  every adjustment pays two more.

Slippage is separate and applied to the fill price, because it is a worse price
rather than a fee. Without bid/ask history it is a model, and it says so: a
fraction of the premium with a floor of one tick. The chain snapshots the desk
records hold bid and ask, and are what this should eventually be calibrated on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

TICK = 0.05


@dataclass(frozen=True)
class Rates:
    since: date
    stt_sell: float
    stt_exercise: float
    #: Exchange transaction charge plus the investor-protection fund levy.
    exchange: float
    sebi: float = 10 / 1e7
    stamp_buy: float = 0.00003
    gst: float = 0.18


SCHEDULE: tuple[Rates, ...] = (
    Rates(since=date(2000, 1, 1), stt_sell=0.000625, stt_exercise=0.00125, exchange=0.000505),
    Rates(since=date(2024, 10, 1), stt_sell=0.001, stt_exercise=0.00125, exchange=0.0003553),
    Rates(since=date(2026, 4, 1), stt_sell=0.0015, stt_exercise=0.0015, exchange=0.0003553),
)


def rates_on(day: date) -> Rates:
    return [r for r in SCHEDULE if r.since <= day][-1]


@dataclass(frozen=True)
class Charges:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp: float = 0.0
    gst: float = 0.0

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst

    def __add__(self, other: Charges) -> Charges:
        return Charges(
            self.brokerage + other.brokerage,
            self.stt + other.stt,
            self.exchange + other.exchange,
            self.sebi + other.sebi,
            self.stamp + other.stamp,
            self.gst + other.gst,
        )


@dataclass(frozen=True)
class CostModel:
    brokerage_per_order: float = 20.0
    #: Slippage per fill, as a fraction of premium, never less than `min_slip`.
    slippage: float = 0.003
    min_slip: float = TICK
    schedule: tuple[Rates, ...] = field(default=SCHEDULE)

    def slip(self, price: float) -> float:
        return max(self.min_slip, self.slippage * price)

    def fill(self, day: date, price: float, quantity: int, *, buy: bool) -> Charges:
        """Charges on one order: `quantity` units at `price` premium."""
        rates = [r for r in self.schedule if r.since <= day][-1]
        turnover = price * quantity
        brokerage = self.brokerage_per_order
        exchange = turnover * rates.exchange
        sebi = turnover * rates.sebi
        return Charges(
            brokerage=brokerage,
            stt=0.0 if buy else turnover * rates.stt_sell,
            exchange=exchange,
            sebi=sebi,
            stamp=turnover * rates.stamp_buy if buy else 0.0,
            gst=(brokerage + exchange + sebi) * rates.gst,
        )

    def exercise(self, day: date, intrinsic: float, quantity: int) -> Charges:
        """A long option settling in the money: STT on intrinsic value, no brokerage."""
        rates = [r for r in self.schedule if r.since <= day][-1]
        return Charges(stt=intrinsic * quantity * rates.stt_exercise)
