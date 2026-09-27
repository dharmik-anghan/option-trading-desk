"""Reading today's volatility out of an option chain.

Separated from both the broker and the database so it can be tested against a
chain built by hand: everything here is arithmetic on rows.

The at-the-money strike is the one nearest spot, and its implied volatility is
the average of the call and the put there. That average is what an option seller
means by "the implied vol", and it is not India VIX - the VIX is a thirty-day
constant-maturity figure built across many strikes, and on the day this was
written it read 12.16 against an ATM implied of 9.85 on the near expiry. Both
are recorded; neither substitutes for the other.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from broker.models import OptionChain
from storage.vol_repo import VolSnapshot


def _day_in_ist(at: datetime) -> date:
    """The trading day a reading belongs to.

    In IST, because that is the day the market was open. A reading taken at
    15:25 in Mumbai is 09:55 UTC, and keying on the UTC date would be right by
    luck rather than by reasoning - it stops being right the moment anything
    runs after 05:30 local.
    """
    from zoneinfo import ZoneInfo

    return at.astimezone(ZoneInfo("Asia/Kolkata")).date()


def snapshot_from(chain: OptionChain, underlying: str) -> VolSnapshot | None:
    """What options cost right now, or None when the chain cannot say.

    None rather than a row of nulls: a chain with no greeks is a chain that
    answers no question this table exists for, and a row of nulls would sit in
    the history looking like a day when volatility was unknown rather than a day
    when the fetch was broken.
    """
    if not chain.rows or chain.underlying_ltp <= 0:
        return None

    spot = chain.underlying_ltp
    strikes = {row.strike for row in chain.rows}
    if not strikes:
        return None
    atm = min(strikes, key=lambda k: abs(k - spot))

    call = next((r for r in chain.rows if r.strike == atm and r.option_type == "CE"), None)
    put = next((r for r in chain.rows if r.strike == atm and r.option_type == "PE"), None)
    if call is None or put is None:
        return None

    call_iv = call.greeks.iv if call.greeks else None
    put_iv = put.greeks.iv if put.greeks else None
    both = [v for v in (call_iv, put_iv) if v is not None and v > 0]
    atm_iv = sum(both) / len(both) if both else None

    straddle = call.ltp + put.ltp if call.ltp > 0 and put.ltp > 0 else None
    if atm_iv is None and straddle is None:
        return None

    expiry, days = _expiry_of(chain)
    at = chain.fetched_at or datetime.now(UTC)
    return VolSnapshot(
        underlying=underlying,
        day=_day_in_ist(at),
        at=at,
        spot=spot,
        expiry=expiry,
        days_to_expiry=days,
        atm_strike=atm,
        call_iv=call_iv,
        put_iv=put_iv,
        atm_iv=atm_iv,
        straddle=straddle,
        india_vix=chain.india_vix,
    )


def _expiry_of(chain: OptionChain) -> tuple[str, float]:
    """Which expiry these rows are for, and how far away it is in days.

    The label is whatever the broker calls it, unparsed. Days are computed from
    a parsed date when one can be had and left at zero when it cannot - a wrong
    number of days would quietly rescale every volatility comparison built on
    it, where a zero is obviously missing.
    """
    token = chain.expiry_token or ""
    listed = next((e for e in chain.expiries if getattr(e, "token", None) == token), None)
    label = str(getattr(listed, "date", "") or token)
    days = 0.0
    if label:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                when = datetime.strptime(label, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
            days = max(0.0, (when - (chain.fetched_at or datetime.now(UTC))).days + 1)
            break
    return label, days
