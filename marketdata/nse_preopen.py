"""NSE's pre-open auction: what each stock was going to open at.

From 09:00 to 09:08 IST the exchange collects orders without matching them, then
settles one price per stock - the equilibrium price that clears the most
quantity - and the session opens there at 09:15. That price, the quantity behind
it and the book it was struck from say which way the day was leaning before
anything traded. That is a signal a backtest can only use if it was written
down on the day, because NSE serves the latest session and nothing older.

Two ways in, one shape out:

- `fetch` asks NSE's own JSON endpoint, the one its pre-open page reads. It
  carries the session's timestamp and the top ten levels of the auction book.
- `parse_csv` reads the file the page's download button saves, so days fetched
  by hand before this existed are not lost. It has no book, no buy and sell
  totals and no date - the date is in the file name.

NSE refuses API requests that do not look like they came from its page: without
the cookies the page sets, the endpoint answers 401 or an empty body. So `fetch`
loads the page first in the same session, which is what a browser does.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import requests

from broker.session import IST

PAGE_URL = "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market"
API_URL = "https://www.nseindia.com/api/market-data-pre-open"

#: The page's own selector values.
KEYS = ("NIFTY 50", "NIFTY BANK", "FO", "SME", "OTHERS", "ALL")

#: What the desk records. `FO` is every stock with derivatives - a superset of
#: NIFTY 50 and NIFTY BANK, about two hundred names - because an options
#: backtest never needs a stock without options. `NIFTY 50` as well, because
#: only that list's answer carries the index's own pre-open figure.
DEFAULT_KEYS = ("FO", "NIFTY 50")

#: When order collection closes and the price is settled. Anything stamped
#: earlier is an indicative figure from an auction still taking orders.
SETTLED_AT = time(9, 8)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": PAGE_URL,
}

#: Rupees to crores, the unit the page shows turnover and market cap in.
_CRORE = 1e7


class PreOpenError(RuntimeError):
    """NSE answered, but not with a pre-open session."""


@dataclass(frozen=True)
class BookLevel:
    price: float
    buy_qty: int
    sell_qty: int
    #: The level the auction settled at.
    is_iep: bool = False


@dataclass(frozen=True)
class PreOpenQuote:
    """One stock's pre-open result on one day."""

    symbol: str
    prev_close: float
    #: The settled price, which the session opens at.
    final_price: float
    final_quantity: int
    change: float
    pct_change: float
    series: str = "EQ"
    #: Indicative equilibrium price. The page blanks it once the auction is
    #: settled, so from a CSV it is usually absent; `final_price` is the one to use.
    iep: float | None = None
    turnover_cr: float | None = None
    ffm_cap_cr: float | None = None
    best_bid: float | None = None
    best_bid_qty: int | None = None
    best_ask: float | None = None
    best_ask_qty: int | None = None
    total_buy_qty: int | None = None
    total_sell_qty: int | None = None
    ato_buy_qty: int | None = None
    ato_sell_qty: int | None = None
    imbalance_at_iep: int | None = None
    imbalance_at_market: int | None = None
    year_high: float | None = None
    year_low: float | None = None
    book: tuple[BookLevel, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class IndexPreOpen:
    """Where NIFTY 50 was set to open: the gap the options will open into."""

    name: str
    price: float
    change: float
    pct_change: float


@dataclass(frozen=True)
class PreOpenDay:
    """A session's pre-open, as one source reported it."""

    day: date
    quotes: tuple[PreOpenQuote, ...]
    #: `nse` for the API, `csv` for a downloaded file.
    source: str
    #: When NSE last updated the figures. Unknown for a CSV.
    as_of: datetime | None = None
    index: IndexPreOpen | None = None

    @property
    def settled(self) -> bool:
        """Whether this is the auction's result rather than a reading taken
        while it was still collecting orders."""
        return self.as_of is None or self.as_of.time() >= SETTLED_AT


# -- the API ---------------------------------------------------------------


def fetch(keys: tuple[str, ...] = DEFAULT_KEYS, *, session: requests.Session | None = None,
          timeout: float = 15.0) -> PreOpenDay:
    """The latest pre-open session NSE has, for one or more of its lists.

    NSE keeps showing a session until the next one's order collection starts,
    so this run on a Saturday returns Friday's - the date comes from NSE's
    timestamp, never from the clock here.
    """
    unknown = [k for k in keys if k not in KEYS]
    if unknown or not keys:
        raise ValueError(f"unknown list {unknown}; NSE's are {', '.join(KEYS)}")
    own = session is None
    s = session or requests.Session()
    try:
        s.headers.update(_HEADERS)
        s.get(PAGE_URL, timeout=timeout).raise_for_status()
        answers = []
        for key in keys:
            response = s.get(API_URL, params={"key": key}, timeout=timeout)
            response.raise_for_status()
            answers.append(parse_api(response.json()))
        return merge(answers)
    finally:
        if own:
            s.close()


def merge(answers: list[PreOpenDay]) -> PreOpenDay:
    """Several lists' answers from one session as one, each stock once."""
    days = {a.day for a in answers}
    if len(days) != 1:
        # One list refreshed for the new session and another not yet: asking
        # again in a minute gets a consistent pair.
        raise PreOpenError(f"lists answered for different sessions: {sorted(days)}")
    quotes: dict[str, PreOpenQuote] = {}
    for answer in answers:
        for quote in answer.quotes:
            quotes.setdefault(quote.symbol, quote)
    stamps = [a.as_of for a in answers if a.as_of is not None]
    return PreOpenDay(
        day=answers[0].day,
        quotes=tuple(quotes.values()),
        source=answers[0].source,
        as_of=max(stamps) if stamps else None,
        index=next((a.index for a in answers if a.index is not None), None),
    )


def parse_api(payload: dict[str, Any]) -> PreOpenDay:
    rows = payload.get("data") or []
    stamp = payload.get("timestamp")
    if not rows or not stamp:
        raise PreOpenError(payload.get("msg") or "no pre-open data in the response")
    as_of = datetime.strptime(stamp, "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    quotes = tuple(_quote_from_api(row) for row in rows)
    index = None
    nifty = payload.get("niftyPreopenStatus")
    if nifty and _num(nifty.get("lastPrice")):
        index = IndexPreOpen(
            name="NIFTY 50",
            price=float(nifty["lastPrice"]),
            change=_num(nifty.get("change")) or 0.0,
            pct_change=_num(nifty.get("pChange")) or 0.0,
        )
    return PreOpenDay(day=as_of.date(), quotes=quotes, source="nse", as_of=as_of, index=index)


def _quote_from_api(row: dict[str, Any]) -> PreOpenQuote:
    meta = row["metadata"]
    detail = (row.get("detail") or {}).get("preOpenMarket") or {}
    book = tuple(
        BookLevel(
            price=float(level["price"]),
            buy_qty=int(level.get("buyQty") or 0),
            sell_qty=int(level.get("sellQty") or 0),
            is_iep=bool(level.get("iep")),
        )
        for level in detail.get("preopen") or ()
        # The page pads an empty book with a zero-price row.
        if level.get("price")
    )
    bids = [lvl for lvl in book if lvl.buy_qty > 0]
    asks = [lvl for lvl in book if lvl.sell_qty > 0]
    bid = max(bids, key=lambda lvl: lvl.price) if bids else None
    ask = min(asks, key=lambda lvl: lvl.price) if asks else None
    turnover = meta.get("totalTurnover")
    cap = meta.get("marketCap")
    return PreOpenQuote(
        symbol=meta["symbol"],
        series=meta.get("series") or "EQ",
        prev_close=float(meta["previousClose"]),
        final_price=float(detail.get("finalPrice", meta["lastPrice"])),
        final_quantity=int(detail.get("finalQuantity", meta.get("finalQuantity") or 0)),
        change=float(meta.get("change") or 0.0),
        pct_change=float(meta.get("pChange") or 0.0),
        iep=_num(detail.get("IEP", meta.get("iep"))),
        turnover_cr=round(turnover / _CRORE, 2) if turnover else None,
        ffm_cap_cr=round(cap / _CRORE, 2) if cap else None,
        best_bid=bid.price if bid else None,
        best_bid_qty=bid.buy_qty if bid else None,
        best_ask=ask.price if ask else None,
        best_ask_qty=ask.sell_qty if ask else None,
        total_buy_qty=_int(detail.get("totalBuyQuantity")),
        total_sell_qty=_int(detail.get("totalSellQuantity")),
        ato_buy_qty=_int(detail.get("atoBuyQty")),
        ato_sell_qty=_int(detail.get("atoSellQty")),
        year_high=_num(meta.get("yearHigh")),
        year_low=_num(meta.get("yearLow")),
        book=book,
    )


# -- the downloaded CSV ----------------------------------------------------

#: `MW-Pre-Open-Market-NIFTY 50-29-Sep-2026.csv`, and the browser's
#: `... (1).csv` for a second download with the same name.
_FILE_NAME = re.compile(
    r"^MW-Pre-Open-Market-(?P<list>.+)-(?P<day>\d{2}-[A-Za-z]{3}-\d{4})"
    r"(?: \((?P<copy>\d+)\))?\.csv$"
)


@dataclass(frozen=True)
class CsvName:
    list_name: str
    day: date
    #: 0 for the original, n for the browser's `(n)` copy.
    copy: int


def parse_csv_name(name: str) -> CsvName | None:
    match = _FILE_NAME.match(name)
    if match is None:
        return None
    return CsvName(
        list_name=match["list"],
        day=datetime.strptime(match["day"], "%d-%b-%Y").date(),
        copy=int(match["copy"] or 0),
    )


#: The page's column headings, which are what the file has for a header.
_COLUMNS = {
    "SYMBOL": "symbol",
    "PREV. CLOSE": "prev_close",
    "BEST BID QTY": "best_bid_qty",
    "BEST BID PRICE": "best_bid",
    "BEST ASK PRICE": "best_ask",
    "BEST ASK QTY": "best_ask_qty",
    "INDICATIVE EQUILIBRIUM QUANTITY": "_ieq",
    "INDICATIVE EQUILIBRIUM PRICE": "iep",
    "CHANGE": "change",
    "%CHANGE": "pct_change",
    "FINAL PRICE": "final_price",
    "FINAL VOLUME": "final_quantity",
    "VALUE (₹ Crores)": "turnover_cr",
    "FFM CAP (₹ Crores)": "ffm_cap_cr",
    "INDICATIVE IMBALANCE QUANTITY AT EQUILIBRIUM PRICE": "imbalance_at_iep",
    "INDICATIVE IMBALANCE QUANTITY AT MARKET ORDER": "imbalance_at_market",
    "52 WEEK HIGH": "year_high",
    "52 WEEK LOW": "year_low",
}
_INTS = {"best_bid_qty", "best_ask_qty", "final_quantity", "imbalance_at_iep",
         "imbalance_at_market"}


def parse_csv(text: str, day: date) -> PreOpenDay:
    """A downloaded pre-open file. The date has to come from the caller - the
    file does not carry one."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    quotes: list[PreOpenQuote] = []
    for raw in reader:
        row = {_COLUMNS[k.strip()]: (v or "").strip() for k, v in raw.items()
               if k and k.strip() in _COLUMNS}
        if not row.get("symbol"):
            continue
        values: dict[str, Any] = {}
        for name, cell in row.items():
            if name in ("symbol", "_ieq"):
                continue
            values[name] = _int(cell) if name in _INTS else _num(cell)
        for required in ("prev_close", "final_price"):
            if values.get(required) is None:
                raise PreOpenError(f"{row['symbol']}: no {required} in the file")
        # The page writes an unchanged price's change as "-", not "0.00".
        values["change"] = values.get("change") or 0.0
        values["pct_change"] = values.get("pct_change") or 0.0
        values["final_quantity"] = values.get("final_quantity") or 0
        quotes.append(PreOpenQuote(symbol=row["symbol"], **values))
    if not quotes:
        raise PreOpenError("no rows in the file")
    return PreOpenDay(day=day, quotes=tuple(quotes), source="csv")


def read_csv(path: Path, day: date | None = None) -> PreOpenDay:
    """A downloaded file, dated by its name unless told otherwise."""
    if day is None:
        name = parse_csv_name(path.name)
        if name is None:
            raise PreOpenError(f"{path.name}: no date in the file name; pass one")
        day = name.day
    return parse_csv(path.read_text(encoding="utf-8-sig"), day)


def _num(value: Any) -> float | None:
    """A figure as NSE writes it: `"1,771.40"`, `"14,88,871"`, or `"-"` for none."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if text in ("", "-"):
        return None
    return float(text)


def _int(value: Any) -> int | None:
    number = _num(value)
    return None if number is None else int(round(number))
