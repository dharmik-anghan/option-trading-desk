"""Every condition that holds right now.

A port of the frontend's `evaluate`, deliberately line-for-line: the same
thresholds, the same wording, the same keys. The browser engine stays for now,
so any drift between the two would show up as one situation logged twice with
slightly different text.

Pure - same inputs, same output, no timestamps and no memory. Folding these
into a log is `alerting/engine.py`'s job, which is what makes the firing rule
easy to state and to trust.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date

from alerting.format import integer, rupees, rupees_compact
from alerting.models import (
    BasketView,
    Condition,
    Direction,
    EventView,
    Limits,
    Severity,
    Watch,
    WatchKind,
)

#: Countries whose releases move an Indian index enough to be worth a warning.
#:
#: India obviously, and the United States because a Fed decision or a US CPI
#: print reprices risk everywhere. Everything else is left out on purpose:
#: filtering only by "high impact" produced 46 alerts for one position,
#: including inflation prints from Thailand, Turkiye and Brazil. A warning list
#: that long is one nobody reads, which defeats the point of having one.
MOVES_INDIA = frozenset({"United States"})

#: Fraction of a threshold at which an alert that is already on clears.
#:
#: A short sitting at delta 0.32 against a 0.30 threshold crosses back and forth
#: all session, and each crossing is a genuine false->true transition, so the
#: alert fired again every time. Once it is on it stays on until delta falls
#: clear, which is how a trader reads it anyway.
HYSTERESIS = 0.9


def affects_india(event: EventView) -> bool:
    if event.importance != "H":
        return False
    return event.coverage == "india" or (
        event.country is not None and event.country in MOVES_INDIA
    )


def events_before(
    events: Iterable[EventView], expiry_date: str | None, today: date | None = None
) -> list[EventView]:
    """Events between now and an expiry that bear on an Indian index position.

    The basket carries its expiry as DD-MM-YYYY and the calendar uses ISO, so
    one is converted rather than compared as text - which would sort 07-10
    before 12-10 and be wrong about the year entirely.
    """
    if not expiry_date:
        return []
    parts = expiry_date.split("-")
    if len(parts) != 3 or not all(parts):
        return []
    day, month, year = parts
    try:
        expiry = date(int(year), int(month), int(day))
    except ValueError:
        return []
    now = today or date.today()
    return [e for e in events if affects_india(e) and now <= e.day <= expiry]


def evaluate(
    total_pnl: float | None,
    baskets: Sequence[BasketView] | None,
    limits: Limits,
    events: Sequence[EventView] = (),
    sticky: frozenset[str] = frozenset(),
    today: date | None = None,
) -> list[Condition]:
    """Every condition that holds right now.

    `sticky` is the set of keys already alerting, used only to widen a threshold
    that has already tripped. Still pure: it just needs to know which side of
    the band it is on.
    """
    on: list[Condition] = []

    if total_pnl is not None:
        net = total_pnl
        if net >= limits.target:
            on.append(
                Condition(
                    key="target",
                    severity=Severity.TARGET,
                    message=f"Profit target reached — net {rupees(net)}",
                )
            )
        if net <= -limits.daily_loss:
            on.append(
                Condition(
                    key="daily-loss",
                    severity=Severity.RISK,
                    message=f"Daily loss limit breached — net {rupees(net)}",
                )
            )
        elif net <= -0.8 * limits.daily_loss:
            on.append(
                Condition(
                    key="daily-loss-near",
                    severity=Severity.WARN,
                    message=f"80% of the daily loss used — net {rupees(net)}",
                )
            )

    for b in baskets or ():
        open_legs = [leg for leg in b.legs if leg.is_open]
        if not open_legs:
            continue

        if b.max_loss is None:
            on.append(
                Condition(
                    key=f"unbounded:{b.id}",
                    severity=Severity.RISK,
                    subject=b.name,
                    message="Unlimited downside — no worst case to check",
                )
            )
        elif abs(b.max_loss) > limits.max_loss:
            on.append(
                Condition(
                    key=f"worst-case:{b.id}",
                    severity=Severity.RISK,
                    subject=b.name,
                    message=(
                        f"Worst case {rupees_compact(b.max_loss)} is past your "
                        f"{rupees_compact(-limits.max_loss)} limit"
                    ),
                )
            )

        # Levels set on this structure, judged before the account-wide ones. They
        # are the ones that answer "how is this trade doing" - which a threshold
        # applied to every structure alike cannot, because a condor's acceptable
        # delta is not a calendar's and a target on one book is not a target on
        # one position.
        if b.profit_target is not None and b.mtm is not None and b.mtm >= b.profit_target:
            on.append(
                Condition(
                    key=f"profit:{b.id}:{b.profit_target:g}",
                    severity=Severity.TARGET,
                    subject=b.name,
                    message=f"Target reached \u2014 {rupees(b.mtm)} of {rupees(b.profit_target)}",
                )
            )

        # Held as a negative number, the way a loss reads. A positive one is
        # taken as the magnitude rather than refused, since "stop at 2000" is a
        # reasonable thing to type and refusing it would be pedantry.
        if b.stop_loss is not None and b.mtm is not None:
            floor = -abs(b.stop_loss)
            if b.mtm <= floor:
                on.append(
                    Condition(
                        key=f"stop:{b.id}:{floor:g}",
                        severity=Severity.RISK,
                        subject=b.name,
                        message=f"Stop hit \u2014 {rupees(b.mtm)} against {rupees(floor)}",
                    )
                )

        # Direction-agnostic: a structure meant to be neutral has drifted whether
        # it drifted long or short, and which way is in the figure.
        if b.delta_limit is not None and b.net_delta is not None:
            if abs(b.net_delta) >= abs(b.delta_limit):
                leaning = "long" if b.net_delta > 0 else "short"
                on.append(
                    Condition(
                        key=f"delta:{b.id}:{abs(b.delta_limit):g}",
                        severity=Severity.WARN,
                        subject=b.name,
                        message=(
                            f"Delta {b.net_delta:+.2f} is past {abs(b.delta_limit):.2f} "
                            f"\u2014 leaning {leaning}"
                        ),
                    )
                )

        if b.days_to_expiry is not None and b.days_to_expiry <= limits.expiry_days:
            on.append(
                Condition(
                    key=f"expiry:{b.id}",
                    severity=Severity.WARN,
                    subject=b.name,
                    message=f"Expires in {b.days_to_expiry:.1f} days",
                )
            )

        # A scheduled release inside the life of the position is the one piece
        # of news that certainly bears on it: the structure has to survive that
        # day, and a short-premium book cannot step aside for it.
        for e in events_before(events, b.expiry_date, today):
            on.append(
                Condition(
                    # Keyed on the label, not the name: the calendar carries
                    # "Goods Exports (Mexico)" and "Goods Exports (Malaysia)" on
                    # one day, and keying on the bare name collided them so the
                    # second was silently swallowed.
                    key=f"event:{b.id}:{e.day}:{e.label}",
                    severity=Severity.WARN,
                    subject=b.name,
                    message=f"{e.label} on {e.day} lands before this expires",
                )
            )

        for leg in open_legs:
            if leg.side != "SELL":
                continue
            label = f"{integer(leg.strike)} {leg.option_type}"

            # A band, not a line - see HYSTERESIS.
            tested_key = f"tested:{leg.id}"
            bound = limits.short_delta * HYSTERESIS if tested_key in sticky else limits.short_delta
            if leg.delta is not None and abs(leg.delta) >= bound:
                on.append(
                    Condition(
                        key=tested_key,
                        severity=Severity.WARN,
                        subject=b.name,
                        message=f"Short {label} tested — delta {abs(leg.delta):.2f}",
                    )
                )

            # Open interest building, or shorts covering, at a strike you are
            # short is the market moving against that leg specifically.
            if leg.ltp_change is not None and leg.oi_change is not None and leg.ltp_change > 0:
                if leg.oi_change != 0:
                    opening = leg.oi_change > 0
                    on.append(
                        Condition(
                            key=f"buildup:{leg.id}",
                            severity=Severity.WARN,
                            subject=b.name,
                            message=(
                                f"{'Long buildup' if opening else 'Short covering'} "
                                f"at short {label}"
                            ),
                        )
                    )

    return on


def _crossed(direction: Direction, value: float, level: float) -> bool:
    return value >= level if direction is Direction.ABOVE else value <= level


def _display(watch: Watch) -> str:
    """What to call the thing being watched.

    Your own note wins; otherwise the symbol, tidied. "NSE:NIFTY50-INDEX" is
    what the broker calls it and not what anyone says out loud.
    """
    if watch.note:
        return watch.note
    symbol = watch.symbol or ""
    return symbol.split(":")[-1].removesuffix("-INDEX") or "price"


def evaluate_watches(
    watches: Sequence[Watch],
    quotes: Mapping[str, float],
    total_pnl: float | None,
) -> list[Condition]:
    """Which of your own levels are currently through.

    Held apart from `evaluate` because the two answer different questions: that
    one reads risk out of the book, this one just checks lines you drew. Keeping
    them separate means neither grows a branch for the other's inputs.

    Crossing is "is it through the level now", not "did it move through this
    tick". Edge triggering in the engine turns that into one alert when it
    happens, and the fire cooldown keeps a price hovering on the line from
    announcing itself repeatedly - the same treatment a wobbling delta gets.
    """
    on: list[Condition] = []
    for watch in watches:
        if not watch.enabled:
            continue

        if watch.kind is WatchKind.PNL:
            if total_pnl is None:
                continue
            if not _crossed(watch.direction, total_pnl, watch.level):
                continue
            on.append(
                Condition(
                    key=watch.key,
                    # A P&L line crossed upward is the good kind of news.
                    severity=(
                        Severity.TARGET if watch.direction is Direction.ABOVE else Severity.RISK
                    ),
                    message=(
                        f"{_display(watch) if watch.note else 'Net P&L'} "
                        f"{watch.direction} {rupees(watch.level)} — now {rupees(total_pnl)}"
                    ),
                )
            )
            continue

        if watch.symbol is None:
            continue
        price = quotes.get(watch.symbol)
        if price is None or not _crossed(watch.direction, price, watch.level):
            continue
        on.append(
            Condition(
                key=watch.key,
                severity=Severity.INFO,
                message=(
                    f"{_display(watch)} {watch.direction} {integer(watch.level)} "
                    f"— now {integer(price)}"
                ),
            )
        )
    return on
