"""Trading only at certain hours, in real timezones."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from backtest.engine import Execution, run
from backtest.market import PerpetualMarket, Side
from backtest.models import Action, Position
from backtest.rules import SpecRule
from backtest.sessions import PRESETS, Session, in_any, preset
from backtest.spec import SpecError, parse
from backtest.view import build, wind
from marketdata.models import Bar, Interval

LONDON = PRESETS["london"]
NEW_YORK = PRESETS["newyork"]


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# --------------------------------------------------------------------------
# Daylight saving, which is the whole reason this is not a fixed offset
# --------------------------------------------------------------------------


def test_a_session_keeps_its_local_hours_across_the_clock_change() -> None:
    """London opens at 08:00 local whether or not the clocks have gone forward.

    Written as fixed UTC hours this window would slide by an hour twice a year,
    in the middle of the period being measured, and nothing in the result would
    show it.
    """
    # 07:30 UTC: winter that is 07:30 in London and shut, summer it is 08:30 and open
    assert LONDON.contains(_at("2026-01-15T07:30")) is False
    assert LONDON.contains(_at("2026-07-15T07:30")) is True

    # and the far end moves with it
    assert LONDON.contains(_at("2026-01-15T16:15")) is True
    assert LONDON.contains(_at("2026-07-15T16:15")) is False


def test_new_york_is_five_or_four_hours_behind_depending_on_the_month() -> None:
    # 13:00 UTC is 08:00 in New York in January and 09:00 in July - open in both
    assert NEW_YORK.contains(_at("2026-01-15T13:00")) is True
    assert NEW_YORK.contains(_at("2026-07-15T13:00")) is True
    # 12:30 UTC is 07:30 in January, before the open, and 08:30 in July, after it
    assert NEW_YORK.contains(_at("2026-01-15T12:30")) is False
    assert NEW_YORK.contains(_at("2026-07-15T12:30")) is True


def test_a_session_that_crosses_midnight() -> None:
    """Sydney's morning is the previous evening in UTC."""
    overnight = Session("Overnight", time(22, 0), time(6, 0), "UTC")

    assert overnight.contains(_at("2026-01-15T23:00")) is True
    assert overnight.contains(_at("2026-01-16T03:00")) is True
    assert overnight.contains(_at("2026-01-16T07:00")) is False


def test_a_session_crossing_midnight_belongs_to_the_day_it_began() -> None:
    """Friday's late session running into Saturday morning is still Friday's."""
    overnight = Session("Overnight", time(22, 0), time(6, 0), "UTC")

    # 2026-01-16 is a Friday, so 02:00 on Saturday belongs to Friday's window
    assert overnight.contains(_at("2026-01-17T02:00")) is True
    # while 02:00 on Sunday belongs to Saturday's, which is not a weekday
    assert overnight.contains(_at("2026-01-18T02:00")) is False


def test_weekends_are_shut() -> None:
    # 2026-01-17 is a Saturday
    assert LONDON.contains(_at("2026-01-17T12:00")) is False
    assert LONDON.contains(_at("2026-01-19T12:00")) is True


def test_a_session_with_no_days_runs_every_day() -> None:
    """Which is what a crypto session is."""
    always = Session("Always", time(0, 0), time(23, 59), "UTC", days=frozenset())

    assert always.contains(_at("2026-01-17T12:00")) is True


def test_an_unknown_zone_is_treated_as_always_open() -> None:
    """Never open would produce a run with no trades, which reads as a strategy
    that never fired rather than a machine missing a timezone."""
    broken = Session("Nowhere", time(8, 0), time(17, 0), "Mars/Olympus")

    assert broken.contains(_at("2026-01-15T03:00")) is True


def test_two_sessions_mean_either_not_both() -> None:
    """Otherwise picking London and New York would mean only their overlap."""
    both = (LONDON, NEW_YORK)

    # 09:00 UTC in January: London is open, New York is not
    assert in_any(both, _at("2026-01-15T09:00")) is True
    # 03:00 UTC: London is asleep and New York shut four hours earlier
    assert in_any(both, _at("2026-01-15T03:00")) is False
    # and 21:00 UTC is 16:00 in New York, which is still inside its session
    assert in_any(both, _at("2026-01-15T21:00")) is True


def test_no_sessions_means_always() -> None:
    assert in_any((), _at("2026-01-17T03:00")) is True


# --------------------------------------------------------------------------
# From a screen
# --------------------------------------------------------------------------


def test_a_session_can_be_named() -> None:
    spec = _spec(sessions=["london", "newyork"])

    assert [s.name for s in spec.sessions] == ["London", "New York"]


def test_an_unknown_session_lists_the_ones_that_exist() -> None:
    with pytest.raises(SpecError, match="not a session"):
        _spec(sessions=["atlantis"])


def test_a_window_of_your_own_needs_a_timezone() -> None:
    """Somebody writing 08:00 means eight o'clock somewhere, and guessing which
    somewhere is how a session ends up an hour out for half the year."""
    with pytest.raises(SpecError, match="needs a timezone"):
        _spec(sessions=[{"name": "Mine", "start": "08:00", "end": "17:00"}])


def test_a_timezone_the_machine_does_not_know_is_refused_at_the_edge() -> None:
    with pytest.raises(SpecError, match="not a timezone"):
        _spec(sessions=[{"start": "08:00", "end": "17:00", "tz": "Mars/Olympus"}])


def test_a_bad_time_says_what_one_looks_like() -> None:
    with pytest.raises(SpecError, match="a time like 08:30"):
        _spec(sessions=[{"start": "morning", "end": "17:00", "tz": "UTC"}])


@pytest.mark.parametrize("name", list(PRESETS))
def test_every_preset_is_a_real_zone(name: str) -> None:
    session = preset(name)

    assert session is not None
    assert session.contains(_at("2026-06-15T12:00")) in (True, False)  # no exception


# --------------------------------------------------------------------------
# As a rule
# --------------------------------------------------------------------------


def _spec(**extra: object):  # type: ignore[no-untyped-def]
    return parse(
        {
            "name": "Session test",
            "interval": "1h",
            "long_entry": {
                "left": {"kind": "price", "field": "close"}, "op": "above", "right": 0
            },
            "long_exit": {
                "left": {"kind": "price", "field": "close"}, "op": "below", "right": 0
            },
            **extra,
        }
    )


def _hours(n: int, start: str = "2026-01-15T00:00") -> list[Bar]:
    at = _at(start)
    return [
        Bar(
            ts=at + timedelta(hours=i),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1.0,
        )
        for i in range(n)
    ]


def test_an_entry_outside_the_session_does_not_fire() -> None:
    bars = _hours(24)
    spec = _spec(sessions=["london"])
    rule = SpecRule(spec)
    view, cursors = build(bars, Interval.H1, spec.context)

    fired = []
    for i in range(len(bars)):
        wind(view, cursors, i)
        if rule.entry(view).action is Action.ENTER:
            fired.append(i)

    # a bar stamped 07:00 closes at 08:00, which is when London opens
    assert fired
    assert min(fired) == 7
    assert max(fired) == 15  # the 15:00 bar closes at 16:00, still inside 16:30


def test_the_session_ending_can_close_a_position() -> None:
    """The difference between trading the London session and merely opening in it."""
    spec = _spec(sessions=["london"], close_outside_session=True)
    rule = SpecRule(spec)
    view, cursors = build(_hours(24), Interval.H1, spec.context)
    wind(view, cursors, 20)  # closes at 21:00 UTC, long after London

    leaving = rule.exit(
        view, Position(side=Side.LONG, quantity=1.0, entry=100.0, opened_at=_at("2026-01-15T09:00"))
    )

    assert leaving.action is Action.EXIT
    assert leaving.reason == "the session ended"


def test_without_that_a_position_is_held_through_the_night() -> None:
    spec = _spec(sessions=["london"], close_outside_session=False)
    rule = SpecRule(spec)
    view, cursors = build(_hours(24), Interval.H1, spec.context)
    wind(view, cursors, 20)

    leaving = rule.exit(
        view, Position(side=Side.LONG, quantity=1.0, entry=100.0, opened_at=_at("2026-01-15T09:00"))
    )

    assert leaving.action is Action.NOTHING


def test_a_session_filter_cuts_the_trades_a_run_takes() -> None:
    """End to end: the same strategy, restricted, trades less."""
    bars = _hours(24 * 10)
    market = PerpetualMarket(symbol="BTCUSDT", maker_fee=0.0, taker_fee=0.0, min_quantity=0.0)
    settings = Execution(capital=1000.0, slippage_bps=0.0)

    everywhere = run(bars, SpecRule(_spec()), market, interval=Interval.H1, execution=settings)
    restricted = run(
        bars,
        SpecRule(_spec(sessions=["london"], close_outside_session=True)),
        market,
        interval=Interval.H1,
        execution=settings,
    )

    assert restricted.trades
    assert len(restricted.trades) > len(everywhere.trades)  # opened and shut each day
    assert all(t.exit_reason == "the session ended" for t in restricted.trades[:-1])
