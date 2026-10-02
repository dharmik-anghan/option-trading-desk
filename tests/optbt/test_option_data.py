"""Option history: the source's parsing and pacing, the store's ledger, and the
backfill's promise that nothing is fetched twice."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from optbt.data.backfill import backfill, fetch_contract, in_band
from optbt.data.fyers import MIN_INTERVAL, FyersExpired, parse_strike
from optbt.data.models import Candle, Contract, Expiries, Kind
from optbt.data.source import SourceError
from optbt.data.store import OptionStore

FETCHED = datetime(2026, 9, 28, 20, 0)


def _candle(ts: datetime, price: float = 10.0, volume: int = 65) -> Candle:
    return Candle(ts=ts, open=price, high=price, low=price, close=price, volume=volume, oi=650)


def _minutes(day: date, n: int = 3) -> list[Candle]:
    start = datetime(day.year, day.month, day.day, 9, 15)
    return [_candle(start + timedelta(minutes=i)) for i in range(n)]


def _option(strike: float, expiry: date = date(2026, 6, 16), kind: Kind = Kind.CALL) -> Contract:
    return Contract(
        symbol=f"NSE:NIFTY26616{int(strike)}{kind}",
        underlying="NIFTY",
        expiry=expiry,
        kind=kind,
        strike=strike,
    )


# ------------------------------------------------------------------ source


@pytest.mark.parametrize(
    ("symbol", "strike"),
    [
        ("NSE:NIFTY2661623500CE", 23500.0),  # weekly, June: YYMDD
        ("NSE:NIFTY26O0625000PE", 25000.0),  # weekly, October is a letter
        ("NSE:NIFTY22OCT17450CE", 17450.0),  # monthly: YYMON
        ("NSE:NIFTY20OCT11950CE", 11950.0),
    ],
)
def test_strike_is_read_after_the_five_character_expiry(symbol: str, strike: float) -> None:
    assert parse_strike(symbol, "NIFTY") == strike


def test_a_symbol_of_another_shape_has_no_strike_rather_than_a_guess() -> None:
    assert parse_strike("NSE:NIFTY26JUNFUT", "NIFTY") is None
    assert parse_strike("NSE:BANKNIFTY26JUN50000CE", "NIFTY") is None


class _Client:
    """Stands in for the SDK: answers queued per method."""

    def __init__(self, answers: dict[str, list[Any]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, method: str) -> Any:
        def call(params: dict[str, Any]) -> Any:
            self.calls.append((method, params))
            answer = self.answers[method].pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return call


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def __call__(self) -> float:
        return self.now


def test_requests_are_spaced_to_stay_under_the_per_minute_limit() -> None:
    ok = {"s": "ok", "candles": []}
    clock = _Clock()
    source = FyersExpired(
        _Client({"fno_historical_data": [ok, ok, ok]}), sleep=clock.sleep, clock=clock
    )
    for _ in range(3):
        source.candles("NSE:X", date(2026, 1, 1), date(2026, 1, 2))
    assert clock.slept == pytest.approx([MIN_INTERVAL, MIN_INTERVAL])


def test_a_rate_limit_is_waited_out_and_asked_again() -> None:
    clock = _Clock()
    client = _Client(
        {
            "fno_historical_data": [
                {"s": "error", "code": 429, "message": "request limit reached"},
                {"s": "ok", "candles": [[1782704700, 1.5, 1.7, 1.2, 1.3, 100, 650, None]]},
            ]
        }
    )
    source = FyersExpired(client, sleep=clock.sleep, clock=clock)
    candles = source.candles("NSE:X", date(2026, 6, 1), date(2026, 6, 16))
    assert len(client.calls) == 2
    assert max(clock.slept) == 60.0
    # Epoch 1782704700 is 09:15 IST on 29 Jun 2026 - stored as exchange time.
    assert candles[0].ts == datetime(2026, 6, 29, 9, 15)
    assert (candles[0].close, candles[0].volume, candles[0].oi) == (1.3, 100, 650)


def test_a_refused_token_is_renewed_and_the_request_asked_again() -> None:
    expired = _Client({"fno_historical_data": [{"s": "error", "code": -16, "message": "x"}]})
    fresh = _Client({"fno_historical_data": [{"s": "ok", "candles": []}]})
    source = FyersExpired(expired, renew=lambda: fresh, sleep=lambda _: None)
    assert source.candles("NSE:X", date(2026, 1, 1), date(2026, 1, 2)) == []
    assert len(fresh.calls) == 1


def test_a_request_that_never_answers_is_abandoned_and_asked_again() -> None:
    import threading as _threading

    stuck = _threading.Event()

    class Hangs:
        def fno_historical_data(self, params: dict[str, Any]) -> dict[str, Any]:
            stuck.wait(5)  # a socket that never answers, as on 29 Sep
            return {"s": "ok", "candles": []}

    fresh = _Client({"fno_historical_data": [{"s": "ok", "candles": []}]})
    source = FyersExpired(Hangs(), renew=lambda: fresh, sleep=lambda _: None, timeout=0.05)
    assert source.candles("NSE:X", date(2026, 1, 1), date(2026, 1, 2)) == []
    assert len(fresh.calls) == 1
    stuck.set()


def test_no_data_is_an_empty_answer_not_a_failure() -> None:
    # Verbatim shape of Fyers' reply for a strike that was listed and never traded.
    client = _Client(
        {"fno_historical_data": [{"s": "no_data", "candles": [], "resolution": "1"}]}
    )
    source = FyersExpired(client, sleep=lambda _: None)
    assert source.candles("NSE:NIFTY2691525900PE", date(2026, 6, 8), date(2026, 9, 15)) == []


def test_a_refusal_that_is_not_about_rate_is_raised_not_retried() -> None:
    client = _Client({"fno_historical_data": [{"s": "error", "message": "Invalid input"}]})
    source = FyersExpired(client, sleep=lambda _: None)
    with pytest.raises(SourceError):
        source.candles("NSE:X", date(2026, 1, 1), date(2026, 1, 2))
    assert len(client.calls) == 1


def test_contracts_are_read_into_futures_and_options_with_strikes() -> None:
    listing = {
        "s": "ok",
        "data": {
            "symbol": "NIFTY",
            "contracts": {
                "futures": ["NSE:NIFTY26JUNFUT"],
                # Listed twice, as Fyers did for 30 Jun 2026: kept once.
                "options": [
                    "NSE:NIFTY26JUN25000CE",
                    "NSE:NIFTY26JUN25000PE",
                    "NSE:NIFTY26JUN25000CE",
                    "NSE:ODD",
                ],
            },
        },
    }
    source = FyersExpired(_Client({"history_underlying_symbols": [listing]}), sleep=lambda _: None)
    contracts = source.contracts("NIFTY", date(2026, 6, 30))
    assert [(c.kind, c.strike) for c in contracts] == [
        (Kind.FUTURE, None),
        (Kind.CALL, 25000.0),
        (Kind.PUT, 25000.0),
    ]


# ------------------------------------------------------------------- store


def test_a_contract_is_stored_with_its_ledger_row_and_cannot_be_stored_twice() -> None:
    store = OptionStore()
    contract = _option(23500)
    assert store.write_contract(contract, _minutes(date(2026, 6, 15)), fetched_at=FETCHED) == 3
    assert store.held([contract.symbol, "NSE:OTHER"]) == {contract.symbol}
    with pytest.raises(Exception, match="(?i)constraint"):
        store.write_contract(contract, _minutes(date(2026, 6, 15)), fetched_at=FETCHED)
    # The refused second write left neither bars nor a second ledger row behind.
    assert store.summary() == [("NIFTY", 1, 3, 1)]


def test_a_contract_that_never_traded_is_ledgered_so_it_is_not_asked_again() -> None:
    store = OptionStore()
    store.write_contract(_option(40000), [], fetched_at=FETCHED)
    assert store.held([_option(40000).symbol]) == {_option(40000).symbol}


def test_index_bars_replace_rather_than_double() -> None:
    store = OptionStore()
    day = _minutes(date(2026, 6, 15))
    store.write_index("NSE:NIFTY50-INDEX", "1", day)
    store.write_index("NSE:NIFTY50-INDEX", "1", day)
    assert store.daily_ranges("NSE:NIFTY50-INDEX") == {date(2026, 6, 15): (10.0, 10.0)}


# ---------------------------------------------------------------- backfill


def test_the_band_keeps_futures_and_strikes_near_where_the_index_traded() -> None:
    expiry = date(2026, 6, 16)
    fut = Contract("NSE:NIFTY26JUNFUT", "NIFTY", expiry, Kind.FUTURE, None)
    contracts = [fut, _option(21000), _option(23500), _option(26000)]
    ranges = {date(2026, 6, 1): (23000.0, 24000.0)}
    kept = in_band(contracts, expiry, ranges, 0.05)
    assert kept == [fut, _option(23500)]
    assert in_band(contracts, expiry, ranges, 0) == contracts
    # No index history for the span: fetch everything rather than nothing.
    assert in_band(contracts, expiry, {}, 0.05) == contracts


class _Source:
    """A source that records what it was asked for."""

    def __init__(self, contracts: list[Contract], listed_on: date) -> None:
        self._contracts = contracts
        self._listed_on = listed_on
        self.asked: list[tuple[str, date, date]] = []

    def expiries(self, underlying: str, start: date, end: date) -> Expiries:
        found = sorted({c.expiry for c in self._contracts if start <= c.expiry <= end})
        return Expiries(options=tuple(found), futures=())

    def contracts(self, underlying: str, expiry: date) -> list[Contract]:
        return [c for c in self._contracts if c.expiry == expiry]

    def candles(self, symbol: str, start: date, end: date) -> list[Candle]:
        self.asked.append((symbol, start, end))
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return [c for d in days if d >= self._listed_on and d.weekday() < 5 for c in _minutes(d, 1)]

    def index_candles(
        self, symbol: str, resolution: str, start: date, end: date
    ) -> list[Candle]:
        return []


def test_a_contract_listed_inside_the_first_window_costs_one_request() -> None:
    source = _Source([], listed_on=date(2026, 5, 14))
    candles = fetch_contract(source, _option(23500))
    assert len(source.asked) == 1
    assert candles[0].ts.date() == date(2026, 5, 14)


def test_a_long_lived_contract_is_walked_back_to_its_listing() -> None:
    source = _Source([], listed_on=date(2025, 6, 2))
    candles = fetch_contract(source, _option(23500))
    assert len(source.asked) == 4  # a year back, in windows of 100 days
    assert min(c.ts for c in candles).date() == date(2025, 6, 2)
    assert len({c.ts for c in candles}) == len(candles)


def test_a_second_run_fetches_nothing_already_held_and_skips_unsettled_expiries() -> None:
    settled, live = date(2026, 6, 16), date(2026, 9, 29)
    contracts = [_option(23500, settled), _option(23600, settled), _option(23500, live)]
    source = _Source(contracts, listed_on=date(2026, 6, 1))
    store = OptionStore()
    kwargs: dict[str, Any] = {
        "since": date(2026, 1, 1),
        "until": date(2026, 9, 28),
        "band": 0,
        "now": lambda: FETCHED,
    }

    first = list(backfill(store, source, "NIFTY", **kwargs))
    assert [(r.expiry, r.fetched) for r in first] == [(settled, 2)]

    source.asked.clear()
    second = list(backfill(store, source, "NIFTY", **kwargs))
    assert [(r.expiry, r.already_held, r.fetched) for r in second] == [(settled, 2, 0)]
    assert source.asked == []


def test_a_dry_run_fetches_no_bars() -> None:
    source = _Source([_option(23500)], listed_on=date(2026, 6, 1))
    store = OptionStore()
    reports = list(
        backfill(
            store, source, "NIFTY", since=date(2026, 1, 1), until=date(2026, 9, 28),
            band=0, dry_run=True,
        )
    )
    assert reports[0].wanted == 1 and reports[0].fetched == 0
    assert source.asked == []


def test_a_contract_is_fetched_whole_even_when_listed_before_the_runs_start() -> None:
    # `since` picks expiries. Cutting a contract's history at it would store the
    # contract short and ledger it as complete, and it would never be refetched.
    expiry = date(2026, 6, 16)
    source = _Source([_option(23500, expiry)], listed_on=date(2026, 5, 14))
    store = OptionStore()
    list(backfill(store, source, "NIFTY", since=date(2026, 6, 10), until=date(2026, 9, 28), band=0))
    assert source.asked[0][1] < date(2026, 5, 14)


def test_a_reader_gets_the_store_between_a_writers_operations(tmp_path: Any) -> None:
    # The backfill used to hold the file for its whole run, locking every
    # backtest out. Now a reader can open it whenever no write is in progress.
    from optbt.data.store import connect

    path = str(tmp_path / "options.duckdb")
    store = OptionStore(path)
    store.write_contract(_option(23500), _minutes(date(2026, 6, 15)), fetched_at=FETCHED)
    reader = connect(path, read_only=True, wait=0)
    assert reader.execute("SELECT count(*) FROM option_bar").fetchone() == (3,)
    reader.close()
    # And the writer carries on after the reader lets go.
    store.write_contract(_option(23600), _minutes(date(2026, 6, 15)), fetched_at=FETCHED)
    assert store.summary() == [("NIFTY", 2, 6, 1)]
