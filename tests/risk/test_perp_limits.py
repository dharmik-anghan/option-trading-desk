"""Pre-trade checks for a leveraged order.

These are seatbelts against this program being wrong, not against the market. A
sized position that moves against you is trading; an order a hundred times the
intended size is a bug, and the tests below are mostly about that bug not
reaching the venue.
"""

from __future__ import annotations

from risk.perps import (
    MIN_LIQUIDATION_DISTANCE,
    PerpLimits,
    check_leverage,
    check_liquidation_distance,
    check_notional,
    check_perp_order,
    check_quantity,
)

CAPS = PerpLimits(max_quantity=0.01, max_notional=2000.0, max_leverage=10.0, dry_run=True)


class TestQuantity:
    def test_within_the_cap_passes(self) -> None:
        assert check_quantity(0.005, 0.01).passed

    def test_exactly_the_cap_passes(self) -> None:
        assert check_quantity(0.01, 0.01).passed

    def test_past_the_cap_fails_and_says_both_numbers(self) -> None:
        result = check_quantity(0.5, 0.01)
        assert not result.passed
        assert "0.5" in result.reason and "0.01" in result.reason

    def test_zero_and_negative_are_not_sizes(self) -> None:
        assert not check_quantity(0, 0.01).passed
        assert not check_quantity(-0.001, 0.01).passed


class TestNotional:
    def test_a_quantity_cap_alone_is_not_enough(self) -> None:
        # 0.01 BTC is a different amount of money at 20,000 than at 120,000, and a
        # cap written when one was true does not hold when the other is.
        cheap = check_notional(0.01, 20_000, 500.0)
        dear = check_notional(0.01, 120_000, 500.0)
        assert cheap.passed, "200 of notional is inside a 500 cap"
        assert not dear.passed, "1,200 of notional is not"

    def test_no_price_means_no_sizing(self) -> None:
        assert not check_notional(0.01, 0, 2000.0).passed


class TestLeverage:
    def test_what_the_venue_allows_is_not_an_invitation(self) -> None:
        # Shark permits 150x on some contracts.
        assert not check_leverage(150, 10.0).passed
        assert check_leverage(8, 10.0).passed

    def test_zero_is_not_a_multiple(self) -> None:
        assert not check_leverage(0, 10.0).passed


class TestLiquidationDistance:
    def test_a_position_starting_next_to_liquidation_is_refused(self) -> None:
        # The check with no options equivalent: a spread cannot be closed against
        # you by a small move, and this can.
        result = check_liquidation_distance(4300.0, 4340.0)
        assert not result.passed
        assert "%" in result.reason

    def test_room_to_breathe_passes(self) -> None:
        assert check_liquidation_distance(4300.0, 3800.0).passed

    def test_it_works_either_side(self) -> None:
        assert check_liquidation_distance(4300.0, 4800.0).passed

    def test_unknown_is_not_a_failure(self) -> None:
        # The venue computes the real figure from the margin mode and its own
        # maintenance rules; an estimate refusing an order the venue would accept
        # is its own kind of wrong.
        result = check_liquidation_distance(4300.0, None)
        assert result.passed
        assert "not known" in result.reason

    def test_the_minimum_is_the_documented_one(self) -> None:
        just_inside = 4300.0 * (1 - MIN_LIQUIDATION_DISTANCE / 2)
        assert not check_liquidation_distance(4300.0, just_inside).passed


class TestAllTogether:
    def test_a_sensible_order_passes_every_check(self) -> None:
        result = check_perp_order(0.005, 84_000.0 / 1000, 8, CAPS)
        assert result.passed, result.reasons

    def test_one_failure_fails_the_order_and_names_only_that(self) -> None:
        result = check_perp_order(0.5, 100.0, 8, CAPS)
        assert not result.passed
        assert len(result.reasons) == 1
        assert "Quantity" in result.reasons[0]

    def test_it_reports_the_notional_for_the_review(self) -> None:
        assert check_perp_order(0.002, 84_000.0, 8, CAPS).notional == 168.0

    def test_dry_run_is_held_apart_from_passing(self) -> None:
        # "We did not send this" must not be mistakable for "this was refused".
        result = check_perp_order(0.005, 100.0, 8, CAPS)
        assert result.passed is True
        assert result.dry_run is True

    def test_live_mode_says_so(self) -> None:
        live = PerpLimits(max_quantity=0.01, max_notional=2000.0, max_leverage=10.0,
                          dry_run=False)
        assert check_perp_order(0.005, 100.0, 8, live).dry_run is False
