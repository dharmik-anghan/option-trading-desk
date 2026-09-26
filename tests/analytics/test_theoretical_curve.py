from __future__ import annotations

import pytest

from analytics.payoff import (
    Leg,
    PayoffResult,
    analyze,
    curve_domain,
    payoff_curve_points,
    theoretical_curve,
)


def _short_straddle() -> list[Leg]:
    return [
        Leg(option_type="CE", strike=100, premium=5.0, quantity=1, side="SELL"),
        Leg(option_type="PE", strike=100, premium=5.0, quantity=1, side="SELL"),
    ]


def _long_call() -> list[Leg]:
    return [Leg(option_type="CE", strike=100, premium=5.0, quantity=1, side="BUY")]


def test_zero_time_collapses_onto_the_expiry_payoff() -> None:
    legs = _short_straddle()
    result = analyze(legs)
    spots = [80.0, 95.0, 100.0, 105.0, 120.0]

    today = theoretical_curve(legs, spots, sigmas=[0.2, 0.2], time_years=0.0)

    assert today == pytest.approx([result.payoff_at(s) for s in spots])


def test_unknown_vol_falls_back_to_intrinsic_rather_than_guessing() -> None:
    legs = _long_call()
    spots = [90.0, 110.0]

    unknown = theoretical_curve(legs, spots, sigmas=[0.0], time_years=0.5)
    at_expiry = theoretical_curve(legs, spots, sigmas=[0.2], time_years=0.0)

    assert unknown == pytest.approx(at_expiry)


def test_short_premium_is_worth_far_less_than_its_expiry_peak() -> None:
    # The whole point of the second curve: at the strike the expiry payoff
    # shows the full credit kept, but today that premium is still owed back.
    legs = _short_straddle()
    result = analyze(legs)

    at_strike = theoretical_curve(legs, [100.0], sigmas=[0.2, 0.2], time_years=0.5)[0]

    assert result.payoff_at(100.0) == pytest.approx(10.0)
    assert at_strike < 0  # nowhere near the credit, with half a year to run


def test_the_two_curves_converge_as_expiry_approaches() -> None:
    legs = _short_straddle()
    result = analyze(legs)
    spot = 104.0
    expiry_value = result.payoff_at(spot)

    gaps = [
        abs(theoretical_curve(legs, [spot], sigmas=[0.2, 0.2], time_years=t)[0] - expiry_value)
        for t in (0.5, 0.25, 0.05, 0.01)
    ]

    assert gaps == sorted(gaps, reverse=True)


def test_deep_in_the_money_can_invert_because_of_discounting() -> None:
    # A European put deep in the money is worth slightly less than intrinsic,
    # so a short one shows a smaller loss now than the expiry payoff implies.
    legs = [Leg(option_type="PE", strike=100, premium=5.0, quantity=1, side="SELL")]
    result = analyze(legs)

    today = theoretical_curve(legs, [40.0], sigmas=[0.2], time_years=0.5)[0]

    assert today > result.payoff_at(40.0)


def test_long_option_is_worth_more_before_expiry_than_at_it() -> None:
    legs = _long_call()
    result = analyze(legs)

    today = theoretical_curve(legs, [100.0], sigmas=[0.25], time_years=0.5)

    assert today[0] > result.payoff_at(100.0)


def test_sigmas_must_match_legs() -> None:
    with pytest.raises(ValueError, match="one entry per leg"):
        theoretical_curve(_short_straddle(), [100.0], sigmas=[0.2], time_years=0.5)


def test_curve_domain_spans_the_expiry_curve_and_is_evenly_spaced() -> None:
    result = analyze(_short_straddle())
    vertices = payoff_curve_points(result)

    grid = curve_domain(result, points=41)

    assert len(grid) == 41
    assert grid[0] == pytest.approx(vertices[0][0])
    assert grid[-1] == pytest.approx(vertices[-1][0])
    gaps = [b - a for a, b in zip(grid[:-1], grid[1:], strict=True)]
    assert gaps == pytest.approx([gaps[0]] * len(gaps))


def test_curve_domain_of_a_fully_closed_position_is_empty() -> None:
    # `analyze` rejects an empty leg list, but a fully-closed basket reaches
    # the renderer as a PayoffResult with nothing left to plot.
    closed = PayoffResult(legs=[], max_profit=0.0, max_loss=0.0, breakevens=[])

    assert curve_domain(closed) == []


def test_realized_offset_shifts_the_whole_curve() -> None:
    legs = _short_straddle()
    spots = [95.0, 105.0]

    base = theoretical_curve(legs, spots, sigmas=[0.2, 0.2], time_years=0.3)
    shifted = theoretical_curve(
        legs, spots, sigmas=[0.2, 0.2], time_years=0.3, realized_offset=25.0
    )

    assert shifted == pytest.approx([v + 25.0 for v in base])


def test_non_positive_strike_falls_back_instead_of_raising() -> None:
    # Black-Scholes needs log(spot/strike); a zero or negative strike has no
    # answer, and a bad chain must not take the whole response down with it.
    legs = [Leg(option_type="CE", strike=0.0, premium=1.0, quantity=1, side="BUY")]

    today = theoretical_curve(legs, [100.0], sigmas=[0.2], time_years=0.5)

    assert today == pytest.approx([99.0])  # intrinsic 100, less the 1.0 paid


def test_zero_spot_falls_back_instead_of_raising() -> None:
    legs = [Leg(option_type="PE", strike=100.0, premium=4.0, quantity=1, side="BUY")]

    today = theoretical_curve(legs, [0.0], sigmas=[0.2], time_years=0.5)

    assert today == pytest.approx([96.0])  # put worth 100 at zero, less the 4.0 paid


class TestCalibration:
    """Anchoring the curve to the prices actually being quoted."""

    def _legs(self) -> list[Leg]:
        return [
            Leg(option_type="CE", strike=105, premium=4.0, quantity=10, side="SELL"),
            Leg(option_type="PE", strike=95, premium=4.0, quantity=10, side="SELL"),
        ]

    def test_the_curve_passes_through_the_real_mark_to_market(self) -> None:
        legs = self._legs()
        sigmas = [0.2, 0.2]
        spot = 100.0
        # the market is a little above what this pricer makes of that vol,
        # which is what happens when the broker derived it with its own model
        modelled = theoretical_curve(legs, [spot], sigmas=sigmas, time_years=0.25)[0]
        market = [3.6, 3.1]

        calibrated = theoretical_curve(
            legs,
            [spot],
            sigmas=sigmas,
            time_years=0.25,
            calibrate_to=market,
            at_spot=spot,
        )[0]

        actual = sum(
            -1 * (price - leg.premium) * leg.quantity
            for leg, price in zip(legs, market, strict=True)
        )
        assert calibrated == pytest.approx(actual)
        assert calibrated != pytest.approx(modelled)

    def test_calibration_shifts_the_whole_curve_not_just_one_point(self) -> None:
        legs = self._legs()
        sigmas = [0.2, 0.2]
        spots = [90.0, 100.0, 110.0]

        plain = theoretical_curve(legs, spots, sigmas=sigmas, time_years=0.25)
        fitted = theoretical_curve(
            legs, spots, sigmas=sigmas, time_years=0.25, calibrate_to=[3.6, 3.1], at_spot=100.0
        )

        gaps = [f - p for f, p in zip(fitted, plain, strict=True)]
        # a constant per leg, so the shape is untouched and only the level moves
        assert gaps == pytest.approx([gaps[0]] * len(gaps))

    def test_matching_prices_leave_the_curve_alone(self) -> None:
        legs = self._legs()
        sigmas = [0.2, 0.2]
        exact = [
            theoretical_curve([leg], [100.0], sigmas=[s], time_years=0.25)[0] / -10 + leg.premium
            for leg, s in zip(legs, sigmas, strict=True)
        ]

        plain = theoretical_curve(legs, [100.0, 105.0], sigmas=sigmas, time_years=0.25)
        fitted = theoretical_curve(
            legs, [100.0, 105.0], sigmas=sigmas, time_years=0.25, calibrate_to=exact, at_spot=100.0
        )

        assert fitted == pytest.approx(plain)

    def test_both_calibration_arguments_are_required_together(self) -> None:
        legs = self._legs()
        with pytest.raises(ValueError, match="go together"):
            theoretical_curve(legs, [100.0], sigmas=[0.2, 0.2], time_years=0.25, at_spot=100.0)
        with pytest.raises(ValueError, match="go together"):
            theoretical_curve(
                legs, [100.0], sigmas=[0.2, 0.2], time_years=0.25, calibrate_to=[3.0, 3.0]
            )

    def test_one_price_per_leg(self) -> None:
        with pytest.raises(ValueError, match="one entry per leg"):
            theoretical_curve(
                self._legs(),
                [100.0],
                sigmas=[0.2, 0.2],
                time_years=0.25,
                calibrate_to=[3.0],
                at_spot=100.0,
            )


class TestDomainCoversSpot:
    """A chart that does not contain the market is not much use."""

    def _bull_put_spread(self) -> list[Leg]:
        # both strikes well below a spot of 23,140
        return [
            Leg(option_type="PE", strike=22900, premium=175.4, quantity=75, side="SELL"),
            Leg(option_type="PE", strike=22500, premium=94.0, quantity=75, side="BUY"),
        ]

    def test_without_spot_the_domain_misses_it(self) -> None:
        points = payoff_curve_points(analyze(self._bull_put_spread()))
        highest = max(x for x, _ in points)

        # the bug: 22,960-ish, with the market at 23,140
        assert highest < 23140

    def test_including_spot_widens_the_domain_to_reach_it(self) -> None:
        points = payoff_curve_points(analyze(self._bull_put_spread()), 23140.5)
        lowest = min(x for x, _ in points)
        highest = max(x for x, _ in points)

        assert lowest <= 23140.5 <= highest

    def test_spot_below_the_strikes_widens_the_other_way(self) -> None:
        # a bear call spread, with the market underneath it
        legs = [
            Leg(option_type="CE", strike=23800, premium=92.95, quantity=75, side="SELL"),
            Leg(option_type="CE", strike=24200, premium=31.3, quantity=75, side="BUY"),
        ]
        points = payoff_curve_points(analyze(legs), 23140.5)

        assert min(x for x, _ in points) <= 23140.5

    def test_a_spot_already_inside_changes_nothing_much(self) -> None:
        legs = [
            Leg(option_type="CE", strike=23000, premium=200.0, quantity=75, side="SELL"),
            Leg(option_type="CE", strike=23400, premium=90.0, quantity=75, side="BUY"),
        ]
        without = payoff_curve_points(analyze(legs))
        within = payoff_curve_points(analyze(legs), 23200.0)

        assert min(x for x, _ in within) <= min(x for x, _ in without)
        assert max(x for x, _ in within) >= max(x for x, _ in without)

    def test_the_sampled_domain_follows_the_same_range(self) -> None:
        result = analyze(self._bull_put_spread())
        grid = curve_domain(result, points=41, include=23140.5)

        assert grid[0] <= 23140.5 <= grid[-1]
        assert len(grid) == 41

    def test_a_nonsense_spot_is_ignored(self) -> None:
        result = analyze(self._bull_put_spread())
        assert payoff_curve_points(result, 0.0) == payoff_curve_points(result)
        assert payoff_curve_points(result, -5.0) == payoff_curve_points(result)
