"""Edge triggering, the loaded-yet-unjudgeable guard, and the fire cooldown.

Ported from the frontend suite. Every test here is a bug that reached the screen:
alerts repeating on every poll, on every refresh, seconds apart when a slow
source landed, and eleven minutes apart when a threshold wobbled.
"""

from __future__ import annotations

from alerting.engine import FIRE_COOLDOWN_MS, REFIRE_FLOOR_MS, dedupe_log, reconcile
from alerting.models import Alert, Condition, Limits, Severity
from alerting.rules import evaluate

L = Limits()
BREACH = evaluate(-L.daily_loss, None, L)
#: The transition rule, isolated. These timestamps are seconds apart, so the
#: cooldown would mask what is being tested; it has its own tests below.
NO_COOLDOWN = 0


def _alert(key: str, at: int) -> Alert:
    return Alert(key=key, severity=Severity.WARN, message="whatever", at=at)


class TestEdgeTriggering:
    def test_writes_once_and_stays_quiet(self) -> None:
        active: frozenset[str] = frozenset()
        log: list[Alert] = []
        for at in (1000, 2000, 3000):
            out = reconcile(active, log, BREACH, at, cooldown_ms=NO_COOLDOWN)
            active, log = out.active, out.log
        # three polls, one line - the whole point of the engine
        assert len(log) == 1
        assert log[0].at == 1000

    def test_can_fire_again_once_cleared(self) -> None:
        out = reconcile(frozenset(), [], BREACH, 1000, cooldown_ms=NO_COOLDOWN)
        out = reconcile(out.active, out.log, [], 2000, cooldown_ms=NO_COOLDOWN)
        assert not out.active
        assert len(out.log) == 1

        out = reconcile(out.active, out.log, BREACH, 3000, cooldown_ms=NO_COOLDOWN)
        assert len(out.log) == 2
        assert out.log[0].at == 3000  # newest first

    def test_does_not_refire_across_a_restart_when_active_is_restored(self) -> None:
        first = reconcile(frozenset(), [], BREACH, 1000, cooldown_ms=NO_COOLDOWN)
        after = reconcile(first.active, list(first.log), BREACH, 2000, cooldown_ms=NO_COOLDOWN)
        assert after.fired == []
        assert len(after.log) == 1
        assert after.log[0].at == 1000  # the original time, not the restart's

    def test_refires_across_a_restart_if_active_was_lost(self) -> None:
        first = reconcile(frozenset(), [], BREACH, 1000, cooldown_ms=NO_COOLDOWN)
        lost = reconcile(frozenset(), first.log, BREACH, 2000, cooldown_ms=NO_COOLDOWN)
        assert len(lost.fired) == 1

    def test_an_empty_condition_list_clears_everything(self) -> None:
        # reconcile's contract, and why callers must not run it before the data
        # it reads has arrived.
        first = reconcile(frozenset(), [], BREACH, 1000, cooldown_ms=NO_COOLDOWN)
        assert len(first.active) == 1
        premature = reconcile(first.active, first.log, [], 1500, cooldown_ms=NO_COOLDOWN)
        assert not premature.active

    def test_one_line_even_if_a_key_is_offered_twice(self) -> None:
        twice = [
            Condition(key="dup", severity=Severity.WARN, message="first"),
            Condition(key="dup", severity=Severity.WARN, message="second"),
        ]
        out = reconcile(frozenset(), [], twice, 1000, cooldown_ms=NO_COOLDOWN)
        assert len(out.fired) == 1
        assert out.fired[0].message == "first"

    def test_the_log_is_capped(self) -> None:
        log: list[Alert] = []
        for i in range(10):
            out = reconcile(frozenset(), log, BREACH, i * 1000, cooldown_ms=NO_COOLDOWN)
            log = out.log
        capped = reconcile(frozenset(), log, BREACH, 99999, limit=3, cooldown_ms=NO_COOLDOWN)
        assert len(capped.log) == 3


class TestASourceThatHasNotLoaded:
    """An unjudgeable key is carried over, not treated as cleared."""

    EVENT = [Condition(key="event:1:2026-10-02:NFP", severity=Severity.WARN, message="lands")]

    def test_keys_are_carried_over_while_unjudgeable(self) -> None:
        first = reconcile(frozenset(), [], self.EVENT, 1000, cooldown_ms=NO_COOLDOWN)
        # the calendar has not answered this pass
        gap = reconcile(
            first.active,
            first.log,
            [],
            2000,
            evaluable=lambda key: not key.startswith("event:"),
            cooldown_ms=NO_COOLDOWN,
        )
        assert gap.active == first.active
        arrived = reconcile(gap.active, gap.log, self.EVENT, 3400, cooldown_ms=NO_COOLDOWN)
        assert arrived.fired == []
        assert len(arrived.log) == 1

    def test_without_the_guard_it_fires_twice_seconds_apart(self) -> None:
        # the reported bug, reproduced: this is what the old behaviour did
        first = reconcile(frozenset(), [], self.EVENT, 1000, cooldown_ms=NO_COOLDOWN)
        gap = reconcile(first.active, first.log, [], 2000, cooldown_ms=NO_COOLDOWN)
        arrived = reconcile(gap.active, gap.log, self.EVENT, 3400, cooldown_ms=NO_COOLDOWN)
        assert len(arrived.log) == 2
        assert arrived.log[0].at - arrived.log[1].at < REFIRE_FLOOR_MS


class TestTheFireCooldown:
    def test_a_churning_condition_does_not_refire_inside_the_cooldown(self) -> None:
        now = 1_000_000
        log = [_alert("tested:19", now)]
        churned = [Condition(key="tested:19", severity=Severity.WARN, message="tested")]
        # went false, came back eleven minutes later - the reported pair
        after = reconcile(frozenset(), log, churned, now + 11 * 60_000)
        assert after.fired == []
        assert len(after.log) == 1
        # but it is active again, so it is not reported as cleared
        assert "tested:19" in after.active

    def test_it_fires_again_once_the_cooldown_has_passed(self) -> None:
        now = 1_000_000
        log = [_alert("tested:19", now)]
        churned = [Condition(key="tested:19", severity=Severity.WARN, message="tested")]
        after = reconcile(frozenset(), log, churned, now + FIRE_COOLDOWN_MS + 1)
        assert len(after.fired) == 1


class TestHealingAPoisonedLog:
    def test_collapses_the_eleven_minute_repeats_that_were_reported(self) -> None:
        base = 1_000_000
        key = "event:8:2026-10-02:Monthly Non Farm (United States)"
        poisoned = [_alert(key, base + 11 * 60_000), _alert(key, base + 22_000), _alert(key, base)]
        assert len(dedupe_log(poisoned, FIRE_COOLDOWN_MS)) == 1

    def test_keeps_distinct_keys(self) -> None:
        base = 1_000_000
        two = [_alert("a", base), _alert("b", base + 10)]
        assert len(dedupe_log(two, FIRE_COOLDOWN_MS)) == 2

    def test_returns_newest_first(self) -> None:
        out = dedupe_log([_alert("a", 1000), _alert("b", 5000)])
        assert [a.at for a in out] == [5000, 1000]

    def test_a_genuine_refire_beyond_the_floor_survives(self) -> None:
        out = dedupe_log([_alert("a", 0), _alert("a", REFIRE_FLOOR_MS + 1)], REFIRE_FLOOR_MS)
        assert len(out) == 2
