from __future__ import annotations

from broker.models import Expiry
from broker.symbols import common_expiry, expiry_for_symbol, expiry_infix, series_prefix

# the three real expiries this desk was checked against
SEP = Expiry(date="29-09-2026", token="1790676600", weekly=False)
OCT_W = Expiry(date="06-10-2026", token="1791281400", weekly=True)
OCT_M = Expiry(date="27-10-2026", token="1793095800", weekly=False)
ALL = [SEP, OCT_W, OCT_M]


def test_monthly_infix_is_year_and_month_abbreviation() -> None:
    assert expiry_infix("27-10-2026", weekly=False) == "26OCT"
    assert expiry_infix("29-09-2026", weekly=False) == "26SEP"


def test_weekly_infix_uses_a_month_letter_then_the_day() -> None:
    assert expiry_infix("06-10-2026", weekly=True) == "26O06"
    # single-digit months stay digits, so May is "5"
    assert expiry_infix("07-05-2026", weekly=True) == "26507"
    # November and December get their own letters
    assert expiry_infix("03-11-2026", weekly=True) == "26N03"
    assert expiry_infix("01-12-2026", weekly=True) == "26D01"


def test_september_is_a_digit_not_a_letter() -> None:
    # "9" for September; only Oct-Dec need letters, because 10-12 are two digits
    assert expiry_infix("22-09-2026", weekly=True) == "26922"


def test_unparseable_date_yields_nothing_rather_than_a_guess() -> None:
    assert expiry_infix("not-a-date", weekly=False) is None
    assert expiry_infix("2026-10-27", weekly=False) is None


def test_a_digit_infix_inside_a_strike_is_not_a_match() -> None:
    # "26507" is the weekly fragment for 7 May 2026, and it also appears
    # inside the strike of this October contract. It must not match.
    may = Expiry(date="07-05-2026", token="1778000000", weekly=True)
    assert expiry_for_symbol("NSE:NIFTY26O0626507CE", [may]) is None
    assert expiry_for_symbol("NSE:NIFTY26O0626507CE", [may, OCT_W]) == OCT_W


def test_a_monthly_symbol_is_not_attributed_to_a_weekly_expiry() -> None:
    # the regression that prompted anchoring: an October monthly symbol was
    # being matched to the 06-10 weekly
    assert expiry_for_symbol("NSE:NIFTY26OCT23100CE", [OCT_W, OCT_M]) == OCT_M


def test_matches_a_monthly_contract() -> None:
    assert expiry_for_symbol("NSE:NIFTY26OCT23100CE", ALL) == OCT_M
    assert expiry_for_symbol("NSE:NIFTY26SEP23100PE", ALL) == SEP


def test_matches_a_weekly_contract() -> None:
    assert expiry_for_symbol("NSE:NIFTY26O0623100PE", ALL) == OCT_W


def test_a_weekly_is_not_shadowed_by_a_monthly_of_the_same_month() -> None:
    # "26O06" and "26OCT" both describe October, in different spellings
    assert expiry_for_symbol("NSE:NIFTY26O0623100PE", [OCT_M, OCT_W]) == OCT_W


def test_a_bse_symbol_matches_the_same_way() -> None:
    sensex = Expiry(date="29-10-2026", token="1793268600", weekly=False)
    assert expiry_for_symbol("BSE:SENSEX26OCT80000CE", [sensex]) == sensex


def test_an_unlisted_expiry_is_never_inferred() -> None:
    assert expiry_for_symbol("NSE:NIFTY26DEC23100CE", ALL) is None


def test_common_expiry_when_every_leg_agrees() -> None:
    legs = [
        "NSE:NIFTY26OCT22500PE",
        "NSE:NIFTY26OCT22900PE",
        "NSE:NIFTY26OCT23800CE",
        "NSE:NIFTY26OCT24200CE",
    ]
    assert common_expiry(legs, ALL) == OCT_M


def test_a_calendar_spread_has_no_single_expiry() -> None:
    legs = ["NSE:NIFTY26SEP23100CE", "NSE:NIFTY26OCT23100CE"]
    assert common_expiry(legs, ALL) is None


def test_one_unknown_leg_makes_the_whole_basket_unknown() -> None:
    legs = ["NSE:NIFTY26OCT23100CE", "NSE:SOMETHINGELSE"]
    assert common_expiry(legs, ALL) is None


def test_no_legs_has_no_expiry() -> None:
    assert common_expiry([], ALL) is None


class TestSeriesPrefix:
    """Telling a calendar from a vertical without asking the broker."""

    def test_the_prefix_names_the_underlying_and_expiry(self) -> None:
        assert series_prefix("NSE:NIFTY26OCT23100CE", 23100.0) == "NSE:NIFTY26OCT"
        assert series_prefix("NSE:NIFTY26NOV23100CE", 23100.0) == "NSE:NIFTY26NOV"

    def test_two_legs_of_a_vertical_share_a_prefix(self) -> None:
        near = series_prefix("NSE:NIFTY26OCT23100CE", 23100.0)
        far = series_prefix("NSE:NIFTY26OCT23500CE", 23500.0)
        assert near == far

    def test_two_legs_of_a_calendar_do_not(self) -> None:
        near = series_prefix("NSE:NIFTY26OCT23100CE", 23100.0)
        far = series_prefix("NSE:NIFTY26NOV23100CE", 23100.0)
        assert near != far

    def test_a_weekly_keeps_its_day(self) -> None:
        # the strike is stripped, not "every trailing digit", or the day goes
        assert series_prefix("NSE:NIFTY26O0623100PE", 23100.0) == "NSE:NIFTY26O06"

    def test_something_unreadable_gives_nothing(self) -> None:
        assert series_prefix("NSE:NIFTY50-INDEX", 23100.0) is None
        assert series_prefix("NSE:NIFTY26OCT23100PE", 99999.0) is None
