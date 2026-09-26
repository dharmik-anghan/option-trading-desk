from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from analytics.market_context import (
    atm_iv,
    atm_straddle,
    atm_strike,
    by_strike,
    futures_symbol,
    historical_vol,
    max_pain,
    oi_wall,
    put_call_ratio,
    skew,
)
from broker.models import Greeks, OptionChain, OptionChainRow


def _row(
    strike: float, kind: str, *, ltp: float = 10.0, oi: int = 0, iv: float = 12.0
) -> OptionChainRow:
    return OptionChainRow(
        symbol=f"NSE:NIFTY26OCT{int(strike)}{kind}",
        strike=strike,
        option_type=kind,  # type: ignore[arg-type]
        ltp=ltp,
        bid=ltp - 0.5,
        ask=ltp + 0.5,
        oi=oi,
        prev_oi=oi,
        volume=0,
        greeks=Greeks(delta=0.3, gamma=0.0003, theta=-4.0, vega=18.0, iv=iv),
    )


def _chain(
    rows: list[OptionChainRow], spot: float = 23140.0, call_oi: int = 0, put_oi: int = 0
) -> OptionChain:
    return OptionChain(
        underlying_symbol="NSE:NIFTY50-INDEX",
        underlying_ltp=spot,
        fetched_at=datetime.now(UTC),
        rows=rows,
        call_oi=call_oi,
        put_oi=put_oi,
    )


class TestShape:
    def test_pairs_each_strike_and_sorts_them(self) -> None:
        chain = _chain([_row(23200, "CE"), _row(23000, "PE"), _row(23000, "CE")])
        strikes = by_strike(chain)

        assert [s.strike for s in strikes] == [23000, 23200]
        assert strikes[0].call is not None and strikes[0].put is not None
        assert strikes[1].put is None  # only a call was listed there


class TestAtm:
    def test_nearest_listed_strike_wins(self) -> None:
        strikes = by_strike(_chain([_row(k, "CE") for k in (23050, 23100, 23150, 23200)]))
        assert atm_strike(strikes, 23140.0) == 23150

    def test_straddle_needs_both_sides_priced(self) -> None:
        both = by_strike(_chain([_row(23150, "CE", ltp=370.0), _row(23150, "PE", ltp=258.0)]))
        assert atm_straddle(both, 23140.0) == pytest.approx(628.0)

        one = by_strike(_chain([_row(23150, "CE", ltp=370.0)]))
        assert atm_straddle(one, 23140.0) is None

    def test_unquoted_iv_is_absence_not_zero(self) -> None:
        strikes = by_strike(_chain([_row(23150, "CE", iv=0.0), _row(23150, "PE", iv=0.0)]))
        assert atm_iv(strikes, 23140.0) is None


class TestOpenInterest:
    def test_ratio_prefers_the_brokers_whole_chain_totals(self) -> None:
        # the loaded window would say 2.0; the contract as a whole says 0.9
        chain = _chain(
            [_row(23000, "CE", oi=100), _row(23000, "PE", oi=200)],
            call_oi=1000,
            put_oi=900,
        )
        assert put_call_ratio(chain, by_strike(chain)) == pytest.approx(0.9)

    def test_ratio_falls_back_to_the_loaded_window(self) -> None:
        chain = _chain([_row(23000, "CE", oi=100), _row(23000, "PE", oi=200)])
        assert put_call_ratio(chain, by_strike(chain)) == pytest.approx(2.0)

    def test_walls_are_the_heaviest_strike_on_each_side(self) -> None:
        strikes = by_strike(
            _chain(
                [
                    _row(23000, "PE", oi=500),
                    _row(23100, "PE", oi=900),
                    _row(23400, "CE", oi=700),
                    _row(23500, "CE", oi=300),
                ]
            )
        )
        support = oi_wall(strikes, "PE", 23140.0)
        resistance = oi_wall(strikes, "CE", 23140.0)
        assert support is not None and support.strike == 23100
        assert resistance is not None and resistance.strike == 23400

    def test_no_open_interest_means_no_wall(self) -> None:
        strikes = by_strike(_chain([_row(23000, "CE", oi=0)]))
        assert oi_wall(strikes, "CE", 23140.0) is None

    def test_support_is_never_above_spot(self) -> None:
        # the live BANKNIFTY case: the heaviest put sat 1,900 points above
        # spot, which the unconstrained maximum happily called support
        strikes = by_strike(
            _chain(
                [
                    _row(55000, "PE", oi=1_000),
                    _row(57500, "PE", oi=9_000),  # heaviest, but above spot
                ],
                spot=55580.0,
            )
        )
        support = oi_wall(strikes, "PE", 55580.0)

        assert support is not None
        assert support.strike == 55000
        assert support.strike < 55580.0

    def test_resistance_is_never_below_spot(self) -> None:
        strikes = by_strike(
            _chain([_row(22000, "CE", oi=9_000), _row(23500, "CE", oi=1_000)], spot=23140.0)
        )
        resistance = oi_wall(strikes, "CE", 23140.0)

        assert resistance is not None
        assert resistance.strike == 23500

    def test_a_round_strike_does_not_win_on_roundness_alone(self) -> None:
        """The reason this is normalised at all.

        On a live NIFTY chain the median open interest at multiples of 1000 was
        ten times that at multiples of 50, so the plain maximum just finds the
        roundest strike. Here 24000 is the heaviest but only typical for a
        1000-multiple, while 23250 is heavy for its own kind.
        """
        rows = [
            # the 1000-multiples: all large, 24000 largest
            _row(24000, "CE", oi=10_000),
            _row(25000, "CE", oi=9_000),
            # the 250-multiples: small apart from 23250
            _row(23250, "CE", oi=4_000),
            _row(23750, "CE", oi=500),
            _row(24250, "CE", oi=400),
        ]
        wall = oi_wall(by_strike(_chain(rows)), "CE", 23140.0)

        assert wall is not None
        assert wall.strike == 23250
        assert wall.heaviest == 24000  # what the plain maximum would have said
        assert wall.prominence > 1


class TestRoundness:
    def test_coarsest_round_number_wins(self) -> None:
        from analytics.market_context import roundness

        assert roundness(24000, 50) == 1000
        assert roundness(23500, 50) == 500
        assert roundness(23250, 50) == 250
        assert roundness(23100, 50) == 100
        assert roundness(23150, 50) == 50

    def test_classes_finer_than_the_listing_step_are_ignored(self) -> None:
        from analytics.market_context import roundness

        # BANKNIFTY lists every 100, so "a multiple of 50" is not a distinction
        assert roundness(55300, 100) == 100
        assert roundness(55500, 100) == 500

    def test_step_is_taken_from_the_listed_strikes(self) -> None:
        from analytics.market_context import strike_step

        strikes = by_strike(_chain([_row(k, "CE") for k in (23000, 23100, 23200)]))
        assert strike_step(strikes) == 100
        assert strike_step(by_strike(_chain([_row(23000, "CE")]))) == 0


class TestMaxPain:
    def test_lands_where_the_most_open_interest_expires_worthless(self) -> None:
        # puts piled at 23000, calls at 23400: pain is least between them,
        # pulled toward whichever side carries more
        strikes = by_strike(
            _chain(
                [
                    _row(23000, "PE", oi=1000),
                    _row(23200, "PE", oi=100),
                    _row(23200, "CE", oi=100),
                    _row(23400, "CE", oi=1000),
                ]
            )
        )
        assert max_pain(strikes) == 23200

    def test_pain_sits_at_the_heavier_side_when_lopsided(self) -> None:
        strikes = by_strike(
            _chain([_row(23000, "PE", oi=10_000), _row(23400, "CE", oi=10)])
        )
        # almost all the open interest is puts at 23000, which expire worthless
        # at or above it
        assert max_pain(strikes) == 23000

    def test_no_open_interest_gives_no_answer(self) -> None:
        strikes = by_strike(_chain([_row(23000, "CE"), _row(23200, "PE")]))
        assert max_pain(strikes) is None

    def test_zero_pain_is_a_real_answer_not_a_missing_one(self) -> None:
        # the minimising strike sitting between the two piles gives pain of
        # exactly 0, which an earlier guard mistook for "no data"
        strikes = by_strike(
            _chain(
                [
                    _row(23000, "PE", oi=1000),
                    _row(23200, "PE", oi=0),
                    _row(23400, "CE", oi=1000),
                ]
            )
        )
        assert max_pain(strikes) is not None


class TestSkew:
    def test_downside_vol_minus_upside(self) -> None:
        rows = [
            _row(22450, "PE", iv=14.0),  # ~3% below 23140
            _row(23150, "CE", iv=10.0),
            _row(23150, "PE", iv=10.0),
            _row(23830, "CE", iv=11.0),  # ~3% above
        ]
        assert skew(by_strike(_chain(rows)), 23140.0) == pytest.approx(3.0)

    def test_steps_over_strikes_with_no_quote(self) -> None:
        # the exact +/-3% strikes are unquoted, as the real wings often are
        rows = [
            _row(22450, "PE", iv=0.0),
            _row(22600, "PE", iv=13.5),
            _row(23830, "CE", iv=0.0),
            _row(23600, "CE", iv=10.5),
        ]
        assert skew(by_strike(_chain(rows)), 23140.0) == pytest.approx(3.0)

    def test_nothing_quoted_either_side_gives_no_reading(self) -> None:
        rows = [_row(22450, "PE", iv=0.0), _row(23830, "CE", iv=0.0)]
        assert skew(by_strike(_chain(rows)), 23140.0) is None


class TestHistoricalVol:
    def test_a_flat_series_has_no_volatility(self) -> None:
        assert historical_vol([100.0] * 30) == pytest.approx(0.0)

    def test_scales_by_root_252(self) -> None:
        # alternating +1%/-1% closes: stdev of |r| is known, so the annualised
        # figure is checkable rather than merely plausible
        closes = [100.0]
        for i in range(40):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
        hv = historical_vol(closes, sessions=20)
        assert hv is not None
        expected = math.log(1.01) * math.sqrt(252) * 100 * math.sqrt(20 / 19)
        assert hv == pytest.approx(expected, rel=0.02)

    def test_too_short_a_series_gives_no_answer(self) -> None:
        assert historical_vol([100.0, 101.0]) is None
        assert historical_vol([]) is None

    def test_only_the_requested_window_is_used(self) -> None:
        calm = [100.0] * 30
        wild = [100.0, 130.0, 90.0, 140.0]
        assert historical_vol(wild + calm, sessions=20) == pytest.approx(0.0)


class TestFuturesSymbol:
    @pytest.mark.parametrize(
        ("option", "strike", "future"),
        [
            ("NSE:NIFTY26OCT23100PE", 23100.0, "NSE:NIFTY26OCTFUT"),
            ("NSE:NIFTY26OCT23100CE", 23100.0, "NSE:NIFTY26OCTFUT"),
            # the index and its derivatives are not named alike
            ("NSE:BANKNIFTY26SEP55500CE", 55500.0, "NSE:BANKNIFTY26SEPFUT"),
            ("BSE:SENSEX26OCT80000CE", 80000.0, "BSE:SENSEX26OCTFUT"),
            # a weekly, whose expiry ends in digits the strike must not eat
            ("NSE:NIFTY26O0623100PE", 23100.0, "NSE:NIFTY26O06FUT"),
            # a fractional strike, as single stocks carry
            ("NSE:SOMECO26OCT1287.5CE", 1287.5, "NSE:SOMECO26OCTFUT"),
        ],
    )
    def test_derived_from_the_option_symbol(self, option: str, strike: float, future: str) -> None:
        assert futures_symbol(option, strike) == future

    def test_something_that_is_not_an_option_symbol(self) -> None:
        assert futures_symbol("NSE:NIFTY50-INDEX", 23100.0) is None
        assert futures_symbol("", 23100.0) is None

    def test_a_strike_that_does_not_match_the_symbol_is_refused(self) -> None:
        # rather than silently trimming the wrong number of characters
        assert futures_symbol("NSE:NIFTY26OCT23100PE", 99999.0) is None
