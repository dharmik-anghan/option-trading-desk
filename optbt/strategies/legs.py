"""A strategy built leg by leg.

Every structure is a list of legs: a straddle is two, an iron condor four, a
spread two. Each leg says which way, which option, how many lots, which expiry,
which strike - relative to the money, or by premium - and its own stop and
target. The strategy around them says when to enter, when to leave, on which
weekdays, whether to hold overnight, and what to do with the whole position.

Strike offsets count listed strikes away from the money, signed by moneyness
rather than by price: OTM 2 on a call is two strikes *above* the ATM strike, on
a put two strikes *below*. That is how a structure is described ("sell the OTM 2
call and put") and it means a preset reads the same for either side.

All the legs enter together or none do. A leg whose strike is not quoted, or
whose expiry is not in the store, skips the day with its reason counted -
entering three legs of a four-leg condor would be a different, unhedged trade.

The config is plain data, which is what the optimiser will vary.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal

from analytics import black_scholes as bs
from broker.models import OptionType
from optbt.data.models import Kind
from optbt.engine import Context, Leg, Level, Side
from optbt.market import OptionKey, Quote, View
from venues.calendar import NSE_CLOSE


class ExpiryRule(StrEnum):
    WEEK = "week"
    NEXT_WEEK = "next_week"
    MONTH = "month"
    NEXT_MONTH = "next_month"
    #: The monthly expiry closest to `LegSpec.expiry_days` calendar days out -
    #: "45 DTE". Monthlies, because that is where a trade that long is placed
    #: and where the far strikes have a market.
    DAYS = "days"


@dataclass(frozen=True)
class StrikeRule:
    #: "atm": `offset` listed strikes from the money, + OTM and - ITM.
    #: "premium": the strike whose premium is closest to `premium`.
    #: "pct": the strike nearest `pct` percent from spot, + OTM and - ITM - how a
    #: far strike is described: at 45 days a short sits 4-5% out, forty strikes
    #: away, where counting strikes stops being natural.
    #: "delta": the strike whose delta (absolute) is nearest `delta` - 0.30 to sell,
    #: 0.17 to buy. Worked out from each strike's own premium, since the store
    #: has no greeks: see `strike_deltas`.
    mode: Literal["atm", "premium", "pct", "delta"] = "atm"
    offset: int = 0
    premium: float = 0.0
    pct: float = 0.0
    delta: float = 0.30


@dataclass(frozen=True)
class LegSpec:
    side: Side
    kind: Kind
    lots: int = 1
    expiry: ExpiryRule = ExpiryRule.WEEK
    #: For ExpiryRule.DAYS: calendar days to expiry to aim for.
    expiry_days: int = 45
    strike: StrikeRule = field(default_factory=StrikeRule)
    stop: Level | None = None
    target: Level | None = None


@dataclass(frozen=True)
class DayFilter:
    """Which days to trade at all, judged at the moment of entry.

    Everything is known by then: the pivots and gap from yesterday's close and
    today's open, the VIX at the bar before entry, its rank against earlier days
    only. None means "no condition". Weekdays live on `LegsConfig`.
    """

    #: "only": trade only on the day the first leg expires; "skip": never on it.
    expiry_day: Literal["any", "only", "skip"] = "any"
    #: Calendar days from today to the first leg's expiry.
    dte_min: int | None = None
    dte_max: int | None = None
    vix_min: float | None = None
    vix_max: float | None = None
    #: 0-100, against the previous `vix_lookback` sessions' closes.
    vix_pct_min: float | None = None
    vix_pct_max: float | None = None
    vix_lookback: int = 252
    #: Today's open against yesterday's close, in percent. Signed.
    gap_min: float | None = None
    gap_max: float | None = None
    #: Where the open must sit against today's pivots (optbt.context.ZONES).
    #: {"S1-P", "P-R1"} is "opened between S1 and R1". Empty means anywhere.
    open_zones: frozenset[str] = frozenset()

    def why_not(self, tags: Mapping[str, object]) -> str | None:
        """The first condition these tags fail, or None if the day qualifies."""

        def outside(key: str, low: float | None, high: float | None) -> bool:
            if low is None and high is None:
                return False
            value = tags.get(key)
            if not isinstance(value, int | float):
                return True  # asked for, and not known: not a day that qualifies
            return (low is not None and value < low) or (high is not None and value > high)

        if self.expiry_day == "only" and not tags.get("expiry_day"):
            return "filter: not an expiry day"
        if self.expiry_day == "skip" and tags.get("expiry_day"):
            return "filter: expiry day"
        if outside("dte", self.dte_min, self.dte_max):
            return "filter: days to expiry"
        if outside("vix", self.vix_min, self.vix_max):
            return "filter: VIX"
        if outside("vix_pct", self.vix_pct_min, self.vix_pct_max):
            return "filter: VIX percentile"
        if outside("gap_pct", self.gap_min, self.gap_max):
            return "filter: gap"
        if self.open_zones and tags.get("open_zone") not in self.open_zones:
            return "filter: open outside the chosen pivot zones"
        return None


@dataclass(frozen=True)
class Adjustment:
    """Moving the untested side in when the market runs at a condor's wing.

    Falls to within `near_points` of the long put: the call spread - in profit
    by now - is closed, and a new one opened with its short `fall_points` above
    the long put (or the short put, by `fall_from`) and, with `move_wing`, its
    long the same width above that. Rises to within `near_points` of the long
    call: the put spread is closed and reopened with its short `rise_points`
    below the short call (or the long call); at 0 below the short call it is an
    iron fly.

    Points, not strikes: "two strikes" meant 200 points to a trader reading a
    100-point chain and 100 to one whose chain lists every 50.

    Moving the wing with the short is what keeps the risk defined. Moving only
    the short left a new short call 22950 hedged by a 24450 wing in Feb 2025 -
    fifteen hundred points of naked exposure - and it lost 33,000 in the March
    rally where the same adjustment with its wing lost a few hundred.

    The position's exits stay measured against the credit it opened for.
    """

    enabled: bool = False
    near_points: float = 50
    fall_from: Literal["long", "short"] = "long"
    fall_points: float = 200
    rise_from: Literal["long", "short"] = "short"
    rise_points: float = 0
    move_wing: bool = True
    #: Rolls allowed in one trade, and never two on the same day.
    max_per_trade: int = 1


@dataclass(frozen=True)
class LegsConfig:
    legs: tuple[LegSpec, ...]
    entry: time = time(9, 20)
    exit: time = time(15, 15)
    #: Monday is 0.
    weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    #: "intraday": everything is closed at `exit` the same day.
    #: "expiry": held overnight, closed at `exit` on the nearest leg's expiry day
    #: (or settled, if `exit` is after the close).
    hold: Literal["intraday", "expiry"] = "intraday"
    #: Whole-position limits in rupees of combined P&L, checked on each close.
    mtm_stop: float | None = None
    mtm_target: float | None = None
    #: The same, as a fraction of the credit the position took in: 0.5 takes
    #: profit when half the credit is captured, 1.0 stops when the loss equals it.
    #: Ignored for a position opened for a debit, which has no credit to measure.
    target_credit: float | None = None
    stop_credit: float | None = None
    #: Positional only: close at the exit time once the nearest leg is this many
    #: calendar days from expiry - "manage at 21 DTE". None holds to expiry day.
    exit_dte: int | None = None
    #: After any leg stops out, move every other leg's stop to its entry price.
    trail_to_cost: bool = False
    days: DayFilter = field(default_factory=DayFilter)
    adjust: Adjustment = field(default_factory=Adjustment)
    #: After the strikes are chosen, set both wings of a condor to the same
    #: width - the average of the two - so each side risks about the same.
    equal_wings: bool = False


def atm_strike(chain: list[Quote], spot: float) -> float | None:
    """Nearest strike to spot at which both a call and a put are priced."""
    calls = {q.key.strike for q in chain if q.key.kind is Kind.CALL and q.price > 0}
    puts = {q.key.strike for q in chain if q.key.kind is Kind.PUT and q.price > 0}
    both = calls & puts
    return min(both, key=lambda k: (abs(k - spot), k)) if both else None


def strike_step(chain: list[Quote], atm: float) -> float | None:
    """The spacing of listed strikes around the money.

    Read from the chain, not assumed: NIFTY lists every 50 near the money and
    every 100 further out, and a far strike's gap is not the one an offset means.
    """
    strikes = sorted({q.key.strike for q in chain})
    near = [k for k in strikes if abs(k - atm) <= atm * 0.03]
    gaps = [b - a for a, b in zip(near, near[1:], strict=False) if b > a]
    if not gaps:
        return None
    return Counter(gaps).most_common(1)[0][0]


#: How far from the asked-for days to expiry a monthly may be and still count.
#: Monthlies are four or five weeks apart, so one is always within about 17.
DAYS_SLACK = 20


def pick_expiry(
    view: View, rule: ExpiryRule, *, overnight: bool = False, days: int = 45
) -> date | None:
    """The expiry a rule means today.

    `overnight` is for a position that will be held past today's close. It
    cannot hold a contract that settles at today's close, so on an expiry day
    "this week" means the next weekly: a positional straddle entered at 15:00 on
    Tuesday 8 Sep 2026 bought the contract expiring that afternoon, and its
    stops were hit within fifteen minutes.
    """
    if rule is ExpiryRule.DAYS:
        monthlies = [e for e in view.monthly_expiries() if not overnight or e > view.day]
        if not monthlies:
            return None
        # Nearest to the target; on a tie, the later one - more time, not less.
        best = min(monthlies, key=lambda e: (abs((e - view.day).days - days), -e.toordinal()))
        # Not one within DAYS_SLACK of the target is no expiry at all. Without
        # this, a run whose calendar ended early traded a "45 DTE" condor on an
        # expiry five days out.
        if abs((best - view.day).days - days) > DAYS_SLACK:
            return None
        return best
    if rule in (ExpiryRule.WEEK, ExpiryRule.NEXT_WEEK):
        found = view.expiries()
        i = 0 if rule is ExpiryRule.WEEK else 1
    else:
        found = view.monthly_expiries()
        i = 0 if rule is ExpiryRule.MONTH else 1
    if overnight:
        found = [e for e in found if e > view.day]
    return found[i] if len(found) > i else None


#: The expiry's settlement moment, for time to expiry.
EXPIRY_CLOSE = NSE_CLOSE


def strike_deltas(
    chain: list[Quote], spot: float, kind: Kind, now: datetime
) -> dict[float, float]:
    """Each quoted strike's delta, solved from its own premium.

    Black-76 on the forward, with the forward read off the chain by put-call
    parity at the strike nearest the money (call - put + strike). That forward
    already carries the interest and dividends between now and expiry, so no
    rate has to be guessed, and it is what the market itself is pricing to.

    Implied volatility is found by bisection on each option's price; a strike
    whose price is below its intrinsic value, or too small to invert, gets no
    delta rather than a made-up one.
    """
    calls = {q.key.strike: q.price for q in chain if q.key.kind is Kind.CALL and q.price > 0}
    puts = {q.key.strike: q.price for q in chain if q.key.kind is Kind.PUT and q.price > 0}
    both = calls.keys() & puts.keys()
    if not both or not chain:
        return {}
    expiry = chain[0].key.expiry
    years = (datetime.combine(expiry, EXPIRY_CLOSE) - now).total_seconds() / (365 * 24 * 3600)
    if years <= 0:
        return {}
    pivot = min(both, key=lambda k: abs(k - spot))
    forward = calls[pivot] - puts[pivot] + pivot
    option_type: OptionType = "CE" if kind is Kind.CALL else "PE"
    out: dict[float, float] = {}
    for strike, premium in (calls if kind is Kind.CALL else puts).items():
        sigma = _implied_vol(premium, forward, strike, years, option_type)
        if sigma is not None:
            out[strike] = bs.greeks(forward, strike, 0.0, sigma, years, option_type).delta
    return out


def _implied_vol(
    premium: float, forward: float, strike: float, years: float, option_type: OptionType
) -> float | None:
    intrinsic = max(0.0, forward - strike) if option_type == "CE" else max(0.0, strike - forward)
    if premium <= intrinsic + 0.01:
        return None
    lo, hi = 0.001, 5.0
    if bs.price(forward, strike, 0.0, hi, years, option_type) < premium:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs.price(forward, strike, 0.0, mid, years, option_type) > premium:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def pick_strike(
    chain: list[Quote], spot: float, kind: Kind, rule: StrikeRule, now: datetime | None = None
) -> tuple[float | None, str]:
    """The strike a rule means, or None and why not."""
    priced = {q.key.strike: q.price for q in chain if q.key.kind is kind and q.price > 0}
    if rule.mode == "delta":
        if now is None:
            return None, "no time to measure delta from"
        deltas = strike_deltas(chain, spot, kind, now)
        if not deltas:
            return None, "no delta could be measured"
        strike = min(deltas, key=lambda k: (abs(abs(deltas[k]) - rule.delta), k))
        # Nothing within 0.05 of what was asked is a chain that does not reach it.
        if abs(abs(deltas[strike]) - rule.delta) > 0.05:
            return None, "no strike near that delta"
        return strike, ""
    if rule.mode == "pct":
        if not priced:
            return None, "no strike priced"
        direction = 1 if kind is Kind.CALL else -1
        aim = spot * (1 + direction * rule.pct / 100)
        strike = min(priced, key=lambda k: (abs(k - aim), k))
        # A strike more than 1% of spot from where it was aimed is a gap in the
        # chain, not the strike that was asked for.
        if abs(strike - aim) > spot * 0.01:
            return None, "strike not quoted"
        return strike, ""
    if rule.mode == "premium":
        if not priced:
            return None, "no strike priced"
        return min(priced, key=lambda k: (abs(priced[k] - rule.premium), k)), ""
    atm = atm_strike(chain, spot)
    if atm is None:
        return None, "no strike priced on both sides"
    if rule.offset == 0:
        return atm, ""
    step = strike_step(chain, atm)
    if step is None:
        return None, "no strike spacing near the money"
    direction = 1 if kind is Kind.CALL else -1
    strike = atm + rule.offset * step * direction
    if strike not in priced:
        return None, "strike not quoted"
    return strike, ""


#: How late after the entry time an entry may still happen. A session that opens
#: after it - the 21 Oct 2025 Muhurat session started at 13:45 - is not a "09:20"
#: entry, and trading it as one would be a different strategy.
ENTRY_GRACE = timedelta(minutes=5)


def _plus(t: time, delta: timedelta) -> time:
    return (datetime.combine(date.min, t) + delta).time()


class LegStrategy:
    def __init__(self, config: LegsConfig) -> None:
        self.config = config
        self._tried_today = False
        #: Per trade: how many rolls, and the day of the last one.
        self._rolled: dict[int, tuple[int, date]] = {}


    def on_day(self, ctx: Context) -> None:
        self._tried_today = False

    def on_bar(self, ctx: Context) -> None:
        cfg = self.config
        view = ctx.view
        clock = view.clock

        if ctx.open_legs:
            self._manage(ctx)
            return

        if self._tried_today or ctx.pending or view.day.weekday() not in cfg.weekdays:
            return
        late = _plus(cfg.entry, ENTRY_GRACE)
        if cfg.hold == "intraday" and clock >= cfg.exit:
            return
        if clock >= late:
            self._tried_today = True
            # Counted only when the session itself opened after the entry window
            # (a Muhurat session). A day whose entry time passed while a
            # positional trade was still open is simply not an entry day.
            if view.session_start > cfg.entry:
                ctx.skip("no session at the entry time")
            return
        if clock < cfg.entry:
            return
        self._tried_today = True
        self._enter(ctx)

    def _describe(self, view: View, expiry: date) -> dict[str, str | float | int | bool | None]:
        """The day as it stood at this decision - what a trade is tagged with and
        what the day filter judges."""
        day = view.context.day(view.day)
        vix = view.context.vix_at(view.now)
        lookback = self.config.days.vix_lookback
        return {
            "weekday": view.day.strftime("%a"),
            "month": view.day.strftime("%Y-%m"),
            "dte": (expiry - view.day).days,
            "expiry_day": expiry == view.day,
            "monthly_expiry": expiry in view.monthly_expiries(),
            "spot": round(view.spot(), 2),
            "vix": round(vix, 2) if vix is not None else None,
            "vix_pct": (
                round(p, 1)
                if vix is not None
                and (p := view.context.vix_percentile(view.day, vix, lookback)) is not None
                else None
            ),
            "gap_pct": round(day.gap_pct, 2) if day and day.gap_pct is not None else None,
            "open_zone": day.open_zone if day else None,
        }

    def _enter(self, ctx: Context) -> None:
        view = ctx.view
        spot = view.spot()
        overnight = self.config.hold == "expiry"
        first = (
            pick_expiry(
                view,
                self.config.legs[0].expiry,
                overnight=overnight,
                days=self.config.legs[0].expiry_days,
            )
            if self.config.legs
            else None
        )
        if first is None:
            ctx.skip("no expiry listed")
            return
        tags = self._describe(view, first)
        why = self.config.days.why_not(tags)
        if why is not None:
            ctx.skip(why)
            return
        chains: dict[date, list[Quote]] = {}
        orders: list[tuple[OptionKey, LegSpec]] = []
        for spec in self.config.legs:
            expiry = pick_expiry(view, spec.expiry, overnight=overnight, days=spec.expiry_days)
            if expiry is None:
                ctx.skip("no expiry listed")
                return
            if expiry not in chains:
                chains[expiry] = view.chain(expiry)
            chain = chains[expiry]
            if not chain:
                ctx.skip("expiry not in the store")
                return
            strike, why = pick_strike(chain, spot, spec.kind, spec.strike, view.now)
            if strike is None:
                ctx.skip(why)
                return
            orders.append((OptionKey(expiry, strike, spec.kind), spec))
        if self.config.equal_wings:
            orders = _equal_wings(orders, chains)
        ctx.tag(**tags)
        for i, (key, spec) in enumerate(orders):
            ctx.open(
                key,
                spec.side,
                spec.lots,
                tag=f"leg {i + 1}",
                stop=spec.stop,
                target=spec.target,
            )

    def _manage(self, ctx: Context) -> None:
        cfg = self.config
        view = ctx.view
        legs = ctx.open_legs
        clock = view.clock

        nearest = min(leg.key.expiry for leg in legs)
        if cfg.hold == "intraday":
            last_day = view.day
        elif cfg.exit_dte is not None:
            last_day = min(nearest, nearest - timedelta(days=cfg.exit_dte))
        else:
            last_day = nearest
        if view.day >= last_day and clock >= cfg.exit:
            ctx.close_all("time" if view.day >= nearest or cfg.exit_dte is None else "dte exit")
            return
        if cfg.hold == "intraday" and view.bars_left <= 1:
            # The session ends before the exit time - a Saturday special session
            # closed at 12:29. Out on its last bar, rather than carried over a
            # weekend by a strategy that says intraday.
            ctx.close_all("session end")
            return

        stop, target = cfg.mtm_stop, cfg.mtm_target
        if (cfg.stop_credit is not None or cfg.target_credit is not None) and ctx.trade:
            credit = _credit(ctx.trade.legs)
            if credit > 0:
                if cfg.stop_credit is not None:
                    by_credit = cfg.stop_credit * credit
                    stop = by_credit if stop is None else min(stop, by_credit)
                if cfg.target_credit is not None:
                    by_credit = cfg.target_credit * credit
                    target = by_credit if target is None else min(target, by_credit)
        if stop is not None or target is not None:
            pnl = ctx.pnl()
            if pnl is not None:
                if stop is not None and pnl <= -stop:
                    ctx.note(f"position P&L {pnl:+,.0f} reached the {stop:,.0f} stop")
                    ctx.close_all("mtm stop")
                    return
                if target is not None and pnl >= target:
                    ctx.note(f"position P&L {pnl:+,.0f} reached the {target:,.0f} target")
                    ctx.close_all("mtm target")
                    return

        if cfg.adjust.enabled and not ctx.pending:
            self._adjust(ctx)

        if cfg.trail_to_cost and ctx.trade is not None:
            if any(leg.exit_reason == "stop" for leg in ctx.trade.legs):
                for leg in legs:
                    against = leg.stop is not None and (leg.stop - leg.entry_price) * -leg.side > 0
                    if against:
                        ctx.set_stop(leg, leg.entry_price)
                        ctx.note(f"stop on {leg.key} moved to cost {leg.entry_price:.2f}")


    def _adjust(self, ctx: Context) -> None:
        """Move the untested side in, if the market has reached a wing."""
        rule = self.config.adjust
        trade = ctx.trade
        view = ctx.view
        if trade is None:
            return
        count, last = self._rolled.get(trade.id, (0, date.min))
        if count >= rule.max_per_trade or last == view.day:
            return

        def one(side: Side, kind: Kind) -> Leg | None:
            found = [leg for leg in ctx.open_legs if leg.side is side and leg.key.kind is kind]
            return found[0] if len(found) == 1 else None

        short_ce, long_ce = one(Side.SELL, Kind.CALL), one(Side.BUY, Kind.CALL)
        short_pe, long_pe = one(Side.SELL, Kind.PUT), one(Side.BUY, Kind.PUT)
        if None in (short_ce, long_ce, short_pe, long_pe):
            return  # not a condor any more: nothing to roll
        assert short_ce and long_ce and short_pe and long_pe
        spot = view.spot()
        if spot <= long_pe.key.strike + rule.near_points:
            anchor = long_pe if rule.fall_from == "long" else short_pe
            aim = anchor.key.strike + rule.fall_points
            short, wing, direction, side_name = short_ce, long_ce, 1, "the long put"
        elif spot >= long_ce.key.strike - rule.near_points:
            anchor = long_ce if rule.rise_from == "long" else short_ce
            aim = anchor.key.strike - rule.rise_points
            short, wing, direction, side_name = short_pe, long_pe, -1, "the long call"
        else:
            return
        chain = view.chain(short.key.expiry)
        kind = short.key.kind
        new_short = _nearest_quoted(chain, kind, aim)
        width = abs(wing.key.strike - short.key.strike)
        new_wing = (
            _nearest_quoted(chain, kind, (new_short or aim) + direction * width)
            if rule.move_wing
            else wing.key.strike
        )
        self._rolled[trade.id] = (count + 1, view.day)
        if new_short is None or new_wing is None:
            ctx.note(f"spot {spot:.0f} near {side_name}, but {aim:g} {kind} is not quoted: no roll")
            return
        if new_short == short.key.strike:
            return
        ctx.note(
            f"spot {spot:.0f} within {rule.near_points:g} of {side_name}: moving the "
            f"{kind} side from {short.key.strike:g}/{wing.key.strike:g} "
            f"to {new_short:g}/{new_wing:g}"
        )
        expiry = short.key.expiry
        ctx.close(short, "adjusted")
        new_short_key = OptionKey(expiry, new_short, kind)
        ctx.open(new_short_key, Side.SELL, short.lots, tag=f"{short.tag} rolled")
        if rule.move_wing and new_wing != wing.key.strike:
            ctx.close(wing, "adjusted")
            new_wing_key = OptionKey(expiry, new_wing, kind)
            ctx.open(new_wing_key, Side.BUY, wing.lots, tag=f"{wing.tag} rolled")


def _nearest_quoted(chain: list[Quote], kind: Kind, aim: float) -> float | None:
    """The quoted strike of this type nearest `aim`, if one is within a strike's reach."""
    strikes = [q.key.strike for q in chain if q.key.kind is kind and q.price > 0]
    if not strikes:
        return None
    best = min(strikes, key=lambda k: (abs(k - aim), k))
    return best if abs(best - aim) <= 100 else None


def _equal_wings(
    orders: list[tuple[OptionKey, LegSpec]], chains: dict[date, list[Quote]]
) -> list[tuple[OptionKey, LegSpec]]:
    """Both wings of a condor set to the same width: the average of the two.

    The 0.17-delta wings of a condor rarely sit the same distance from their
    shorts - skew makes the put side wider - so one side risks more than the
    other. A trader evens them out; this does the same, rounding the width to a
    listed strike. Anything that is not one short and one long per side is left
    as it was.
    """

    def find(side: Side, kind: Kind) -> int | None:
        hits = [i for i, (k, spec) in enumerate(orders) if spec.side is side and k.kind is kind]
        return hits[0] if len(hits) == 1 else None

    sc, lc = find(Side.SELL, Kind.CALL), find(Side.BUY, Kind.CALL)
    sp, lp = find(Side.SELL, Kind.PUT), find(Side.BUY, Kind.PUT)
    if None in (sc, lc, sp, lp):
        return orders
    assert sc is not None and lc is not None and sp is not None and lp is not None
    call_width = orders[lc][0].strike - orders[sc][0].strike
    put_width = orders[sp][0].strike - orders[lp][0].strike
    if call_width <= 0 or put_width <= 0:
        return orders
    width = (call_width + put_width) / 2
    out = list(orders)
    for short_i, long_i, direction in ((sc, lc, 1), (sp, lp, -1)):
        short_key, long_key = orders[short_i][0], orders[long_i][0]
        chain = chains.get(long_key.expiry, [])
        strike = _nearest_quoted(chain, long_key.kind, short_key.strike + direction * width)
        if strike is not None:
            out[long_i] = (OptionKey(long_key.expiry, strike, long_key.kind), orders[long_i][1])
    return out


def _credit(legs: list[Leg]) -> float:
    """What the position took in when it was put on: premium sold minus premium
    bought, in rupees, at the fills of the legs that opened it."""
    if not legs:
        return 0.0
    first = min(leg.entry_ts for leg in legs)
    return sum(-leg.side * leg.entry_price * leg.quantity for leg in legs if leg.entry_ts == first)


# ------------------------------------------------------------------ presets


def _leg(side: Side, kind: Kind, offset: int, stop: Level | None = None) -> LegSpec:
    return LegSpec(side=side, kind=kind, strike=StrikeRule(offset=offset), stop=stop)


#: The stop the short presets carry unless told otherwise.
QUARTER = Level("pct", 0.25)


def straddle(stop: Level | None = QUARTER) -> tuple[LegSpec, ...]:
    return (_leg(Side.SELL, Kind.CALL, 0, stop), _leg(Side.SELL, Kind.PUT, 0, stop))


def iron_condor(short: int = 4, wing: int = 4) -> tuple[LegSpec, ...]:
    return (
        _leg(Side.SELL, Kind.CALL, short),
        _leg(Side.BUY, Kind.CALL, short + wing),
        _leg(Side.SELL, Kind.PUT, short),
        _leg(Side.BUY, Kind.PUT, short + wing),
    )
