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

CAPS = PerpLimits(max_notional=2000.0, max_leverage=10.0, max_quantity=0.01)
#: What the venue itself allows on BTCUSDT, for the checks that ask.
VENUE_MAX = 150.0


class TestQuantity:
    def test_no_cap_set_means_no_cap(self) -> None:
        # Off by default, and it should usually stay off: a quantity cannot be
        # compared across instruments worth 840 USDT and 94 cents apiece. A cap of
        # 0.01 - tight enough to be useful on Bitcoin - blocked oil's smallest
        # legal order of 0.07, making the contract untradeable.
        assert check_quantity(0.07, 0.0).passed

    def test_a_size_is_still_a_size(self) -> None:
        assert not check_quantity(0, 0.0).passed
        assert not check_quantity(-1, 0.0).passed

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
    def test_the_venues_own_ceiling_is_enforced(self) -> None:
        # Per contract, and sharply different: 150x on BTCUSDT, 75x on gold, 50x
        # on oil. A single number written here would block a legitimate order or
        # wave through one the venue rejects.
        assert check_leverage(100, 150.0).passed
        assert not check_leverage(100, 75.0).passed
        assert not check_leverage(60, 50.0).passed

    def test_your_own_ceiling_applies_on_top(self) -> None:
        assert not check_leverage(50, 150.0, own_ceiling=10.0).passed
        assert check_leverage(8, 150.0, own_ceiling=10.0).passed

    def test_no_ceiling_of_your_own_means_only_the_venues(self) -> None:
        assert check_leverage(100, 150.0, own_ceiling=0.0).passed

    def test_an_unknown_venue_maximum_does_not_block(self) -> None:
        # Without the catalogue the venue's limit goes unchecked, and it will
        # reject the order itself if it is broken.
        assert check_leverage(100, 0.0).passed

    def test_zero_is_not_a_multiple(self) -> None:
        assert not check_leverage(0, 150.0).passed


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
        result = check_perp_order(0.005, 84_000.0 / 1000, 8, CAPS, venue_max_leverage=VENUE_MAX)
        assert result.passed, result.reasons

    def test_one_failure_fails_the_order_and_names_only_that(self) -> None:
        result = check_perp_order(0.5, 100.0, 8, CAPS, venue_max_leverage=VENUE_MAX)
        assert not result.passed
        assert len(result.reasons) == 1
        assert "Quantity" in result.reasons[0]

    def test_it_reports_the_notional_for_the_review(self) -> None:
        assert check_perp_order(0.002, 84_000.0, 8, CAPS).notional == 168.0

    def test_a_size_under_the_venues_minimum_is_refused(self) -> None:
        # The floor that protects against a rejection rather than a mistake. The
        # venue answers "order failed" with no arithmetic, and the real floor moves
        # with the price because it is a notional minimum.
        result = check_perp_order(
            0.001, 84_000.0, 8, CAPS, venue_max_leverage=VENUE_MAX, smallest_order=0.002
        )
        assert not result.passed
        assert any("minimum" in r for r in result.reasons)

    def test_exactly_the_minimum_is_allowed(self) -> None:
        result = check_perp_order(
            0.002, 84_000.0, 8, CAPS, venue_max_leverage=VENUE_MAX, smallest_order=0.002
        )
        assert result.passed, result.reasons

    def test_an_unknown_minimum_does_not_block(self) -> None:
        assert check_perp_order(0.001, 100.0, 8, CAPS, smallest_order=0.0).passed
