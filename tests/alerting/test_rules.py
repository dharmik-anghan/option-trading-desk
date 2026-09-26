"""The conditions, ported from the frontend's alerts.test.ts.

Kept as a port rather than rewritten: the browser engine still runs, and the two
must agree on thresholds, keys and wording. Where a test here reads oddly, it is
usually recording a bug that reached the screen once.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from alerting.models import Direction, Limits, Severity, Watch, WatchKind
from alerting.rules import HYSTERESIS, affects_india, evaluate, evaluate_watches, events_before
from tests.alerting.conftest import FakeBasket, FakeEvent, FakeLeg

L = Limits()
TODAY = date(2026, 9, 26)


def keys(conditions: list) -> list[str]:  # type: ignore[type-arg]
    return [c.key for c in conditions]


class TestAccountLimits:
    def test_profit_target(self) -> None:
        on = evaluate(L.target, None, L)
        assert "target" in keys(on)
        assert next(c for c in on if c.key == "target").severity is Severity.TARGET

    def test_daily_loss_breached(self) -> None:
        assert "daily-loss" in keys(evaluate(-L.daily_loss, None, L))

    def test_eighty_percent_of_the_daily_loss(self) -> None:
        on = keys(evaluate(-0.85 * L.daily_loss, None, L))
        assert "daily-loss-near" in on
        assert "daily-loss" not in on

    def test_nothing_when_flat(self) -> None:
        assert evaluate(0.0, None, L) == []

    def test_no_portfolio_is_not_a_zero_pnl(self) -> None:
        assert evaluate(None, None, L) == []


class TestPerStructureRisk:
    def test_unbounded_downside(self) -> None:
        b = FakeBasket(max_loss=None)
        on = evaluate(None, [b], L)
        assert f"unbounded:{b.id}" in keys(on)

    def test_worst_case_past_the_limit(self) -> None:
        b = FakeBasket(max_loss=-(L.max_loss + 1))
        assert f"worst-case:{b.id}" in keys(evaluate(None, [b], L))

    def test_worst_case_within_the_limit(self) -> None:
        b = FakeBasket(max_loss=-(L.max_loss - 1))
        assert f"worst-case:{b.id}" not in keys(evaluate(None, [b], L))

    def test_expiry_approaching(self) -> None:
        b = FakeBasket(days_to_expiry=L.expiry_days - 0.5)
        assert f"expiry:{b.id}" in keys(evaluate(None, [b], L))

    def test_a_closed_structure_raises_nothing(self) -> None:
        b = FakeBasket(max_loss=None, legs=[FakeLeg(is_open=False)])
        assert evaluate(None, [b], L) == []


class TestShortStrikesUnderPressure:
    def test_a_tested_short(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=19, side="SELL", delta=-0.32)])
        assert "tested:19" in keys(evaluate(None, [b], L))

    def test_a_comfortable_short_is_quiet(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=19, side="SELL", delta=-0.1)])
        assert "tested:19" not in keys(evaluate(None, [b], L))

    def test_a_long_leg_is_never_tested(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=19, side="BUY", delta=-0.9)])
        assert "tested:19" not in keys(evaluate(None, [b], L))

    def test_a_missing_delta_is_not_a_zero(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=19, side="SELL", delta=None)])
        assert "tested:19" not in keys(evaluate(None, [b], L))

    def test_long_buildup_against_a_short(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=7, side="SELL", ltp_change=2.0, oi_change=5000)])
        on = next(c for c in evaluate(None, [b], L) if c.key == "buildup:7")
        assert "Long buildup" in on.message

    def test_short_covering_against_a_short(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=7, side="SELL", ltp_change=2.0, oi_change=-5000)])
        on = next(c for c in evaluate(None, [b], L) if c.key == "buildup:7")
        assert "Short covering" in on.message

    def test_a_falling_price_is_not_buildup(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=7, side="SELL", ltp_change=-2.0, oi_change=5000)])
        assert "buildup:7" not in keys(evaluate(None, [b], L))

    def test_unchanged_open_interest_is_not_buildup(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=7, side="SELL", ltp_change=2.0, oi_change=0)])
        assert "buildup:7" not in keys(evaluate(None, [b], L))


class TestHysteresis:
    """A band, not a line - the fix for an alert that fired on every wobble."""

    def _basket(self, delta: float) -> FakeBasket:
        return FakeBasket(legs=[FakeLeg(id=19, side="SELL", delta=delta)])

    def test_fires_at_the_threshold(self) -> None:
        assert "tested:19" in keys(evaluate(None, [self._basket(-0.32)], L))

    def test_stays_on_inside_the_release_band(self) -> None:
        inside = -(L.short_delta * HYSTERESIS + 0.005)
        on = evaluate(None, [self._basket(inside)], L, sticky=frozenset({"tested:19"}))
        assert "tested:19" in keys(on)

    def test_would_not_have_fired_from_cold_at_that_delta(self) -> None:
        inside = -(L.short_delta * HYSTERESIS + 0.005)
        assert "tested:19" not in keys(evaluate(None, [self._basket(inside)], L))

    def test_clears_once_plainly_clear(self) -> None:
        on = evaluate(None, [self._basket(-0.2)], L, sticky=frozenset({"tested:19"}))
        assert "tested:19" not in keys(on)


class TestScheduledEvents:
    def test_an_event_before_expiry(self) -> None:
        b = FakeBasket(id=8, expiry_date="27-10-2026")
        on = evaluate(None, [b], L, [FakeEvent(day=date(2026, 10, 7))], today=TODAY)
        assert "event:8:2026-10-07:RBI Policy Rate" in keys(on)

    def test_an_event_after_expiry_is_ignored(self) -> None:
        b = FakeBasket(id=8, expiry_date="27-10-2026")
        on = evaluate(None, [b], L, [FakeEvent(day=date(2026, 11, 7))], today=TODAY)
        assert not [k for k in keys(on) if k.startswith("event:")]

    def test_a_past_event_is_ignored(self) -> None:
        b = FakeBasket(id=8, expiry_date="27-10-2026")
        on = evaluate(None, [b], L, [FakeEvent(day=date(2026, 9, 1))], today=TODAY)
        assert not [k for k in keys(on) if k.startswith("event:")]

    def test_two_events_of_one_name_on_one_day_both_warn(self) -> None:
        # Real data: 2026-09-28 carried "Goods Exports (Mexico)" and
        # "Goods Exports (Malaysia)". Keyed on `name` both were "Goods Exports",
        # so the second was silently swallowed as a duplicate of the first.
        both = [
            FakeEvent(
                day=date(2026, 9, 28),
                name="Goods Exports",
                label=f"Goods Exports ({where})",
                coverage="global",
                country="United States",
            )
            for where in ("Mexico", "Malaysia")
        ]
        on = evaluate(None, [FakeBasket(id=8)], L, both, today=TODAY)
        assert len({k for k in keys(on) if k.startswith("event:")}) == 2

    def test_only_india_and_the_us(self) -> None:
        # Filtering on importance alone produced 46 alerts for one position.
        assert affects_india(FakeEvent(coverage="india", country=None))
        assert affects_india(FakeEvent(coverage="global", country="United States"))
        assert not affects_india(FakeEvent(coverage="global", country="Thailand"))
        assert not affects_india(FakeEvent(coverage="global", country="Brazil"))

    def test_only_high_importance(self) -> None:
        assert not affects_india(FakeEvent(importance="M"))

    def test_expiry_is_read_as_a_date_not_as_text(self) -> None:
        # DD-MM-YYYY compared as text sorts 07-10 before 12-10 and is wrong
        # about the year entirely.
        events = [FakeEvent(day=date(2026, 10, 12))]
        assert events_before(events, "27-10-2026", TODAY)
        assert not events_before(events, "07-10-2026", TODAY)

    def test_no_expiry_means_no_events(self) -> None:
        assert events_before([FakeEvent()], None, TODAY) == []

    def test_a_malformed_expiry_is_not_a_crash(self) -> None:
        assert events_before([FakeEvent()], "garbage", TODAY) == []


class TestWording:
    """The messages are shared with the browser engine, so the text matters."""

    def test_money_is_worded_as_the_desk_words_it(self) -> None:
        on = evaluate(-L.daily_loss, None, L)
        assert next(c for c in on if c.key == "daily-loss").message == (
            "Daily loss limit breached — net −₹25,000"
        )

    def test_a_strike_carries_its_side(self) -> None:
        b = FakeBasket(legs=[FakeLeg(id=19, side="SELL", strike=22900, option_type="PE",
                                     delta=-0.32)])
        message = next(c for c in evaluate(None, [b], L) if c.key == "tested:19").message
        assert message == "Short 22,900 PE tested — delta 0.32"

    def test_subject_is_held_apart_from_the_message(self) -> None:
        b = FakeBasket(name="27 Oct - Iron Condor", max_loss=None)
        condition = next(c for c in evaluate(None, [b], L) if c.key.startswith("unbounded:"))
        assert condition.subject == "27 Oct - Iron Condor"
        assert "27 Oct" not in condition.message


def test_replace_keeps_the_builders_honest() -> None:
    # guards against a fixture default drifting out from under the tests
    assert replace(FakeLeg(), delta=-0.5).delta == -0.5


class TestYourOwnLevels:
    """Watches: lines you drew, which nothing in the data suggests on its own."""

    def _price(self, level: float, direction: Direction = Direction.ABOVE, **kw: object) -> Watch:
        return Watch(
            id=1,
            kind=WatchKind.PRICE,
            direction=direction,
            level=level,
            symbol="NSE:NIFTY50-INDEX",
            **kw,  # type: ignore[arg-type]
        )

    def test_a_price_through_the_level_fires(self) -> None:
        on = evaluate_watches([self._price(24000)], {"NSE:NIFTY50-INDEX": 24015.0}, None)
        assert len(on) == 1
        assert on[0].message == "NIFTY50 above 24,000 — now 24,015"

    def test_a_price_short_of_the_level_is_quiet(self) -> None:
        assert evaluate_watches([self._price(24000)], {"NSE:NIFTY50-INDEX": 23900.0}, None) == []

    def test_exactly_on_the_level_counts_as_through(self) -> None:
        assert evaluate_watches([self._price(24000)], {"NSE:NIFTY50-INDEX": 24000.0}, None)

    def test_below_is_the_other_direction(self) -> None:
        watch = self._price(23000, Direction.BELOW)
        assert evaluate_watches([watch], {"NSE:NIFTY50-INDEX": 22900.0}, None)
        assert evaluate_watches([watch], {"NSE:NIFTY50-INDEX": 23100.0}, None) == []

    def test_a_missing_quote_is_not_a_zero(self) -> None:
        # Otherwise every "below" watch fires the moment a quote goes missing.
        assert evaluate_watches([self._price(23000, Direction.BELOW)], {}, None) == []

    def test_a_disabled_watch_is_ignored(self) -> None:
        watch = self._price(24000, enabled=False)
        assert evaluate_watches([watch], {"NSE:NIFTY50-INDEX": 24015.0}, None) == []

    def test_your_own_words_win_over_the_symbol(self) -> None:
        watch = self._price(24000, note="Nifty breakout")
        on = evaluate_watches([watch], {"NSE:NIFTY50-INDEX": 24015.0}, None)
        assert on[0].message.startswith("Nifty breakout above")

    def test_a_pnl_level_reads_the_book_not_a_quote(self) -> None:
        watch = Watch(id=2, kind=WatchKind.PNL, direction=Direction.ABOVE, level=10000)
        on = evaluate_watches([watch], {}, 10500.0)
        assert on[0].message == "Net P&L above ₹10,000 — now ₹10,500"
        assert on[0].severity is Severity.TARGET

    def test_a_pnl_level_downward_is_a_risk(self) -> None:
        watch = Watch(id=2, kind=WatchKind.PNL, direction=Direction.BELOW, level=-5000)
        assert evaluate_watches([watch], {}, -6000.0)[0].severity is Severity.RISK

    def test_no_pnl_yet_is_not_a_zero(self) -> None:
        # A broker that has not answered must not read as a flat book, or every
        # "below zero" watch fires on startup.
        watch = Watch(id=2, kind=WatchKind.PNL, direction=Direction.BELOW, level=-5000)
        assert evaluate_watches([watch], {}, None) == []

    def test_moving_the_level_makes_a_new_condition(self) -> None:
        # so an edited line can fire again rather than being suppressed as
        # "already alerted"
        first = self._price(24000).key
        moved = self._price(24500).key
        assert first != moved
