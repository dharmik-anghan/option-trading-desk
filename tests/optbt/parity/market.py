"""A synthetic option market that exercises every path of the engine.

Deterministic, closed-form and dependency-free, so any engine - the Python one,
or one in another language reading the Parquet export - sees exactly the same
bars. Three weeks of a NIFTY-like index and its weekly options, priced by
Black-Scholes, with what a real store has and a toy one would not:

  - a rally (day 3) and a fall (day 8) that trip stops, targets and adjustments;
  - a gap down (day 5), for gap filters and pivot zones;
  - a weekly listed on a holiday (15 Jan), which settles on the session before;
  - a stretch with no option bars at all (day 6, 09:20-09:26), so opening
    orders are abandoned;
  - an illiquid strike that prints every tenth minute, so a mark falls back to
    an earlier bar;
  - India VIX alongside, for the VIX filters and tags;
  - volume and open interest in whole lots of 65, which is how a lot is read.
"""

from __future__ import annotations

import csv
import math
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb

from analytics import black_scholes as bs
from optbt.data.store import SCHEMA
from venues.instruments import INDIA_VIX, OPTION_SERIES

UNDERLYING = "NIFTY"
INDEX = OPTION_SERIES[UNDERLYING]
LOT = 65
RATE = 0.065

HOLIDAY = date(2026, 1, 15)
DAYS = [
    d
    for d in (date(2026, 1, 5) + timedelta(days=i) for i in range(19))
    if d.weekday() < 5 and d != HOLIDAY
]
START, END = DAYS[0], DAYS[-1]
#: Weeklies; the 15th is a holiday, as 29 Jun 2023 was in the real calendar.
EXPIRIES = [date(2026, 1, 8), HOLIDAY, date(2026, 1, 22), date(2026, 1, 29), date(2026, 2, 5)]
MONTHLIES = [date(2026, 1, 29), date(2026, 2, 26)]
STRIKES = [22300.0 + 50 * i for i in range(29)]
ILLIQUID = (23400.0, "CE")
#: Day 6: no option traded 09:20-09:26, so an order decided at 09:20 - which
#: fills in the 09:20 bar - finds no price for five bars and is abandoned.
DARK_DAY, DARK = DAYS[6], (time(9, 20), time(9, 26))

#: Where each session opens, against 23,000.
OPENS = [0, 60, -40, 150, 80, -200, -150, -100, 20, 90, 140, 60, -30, 10]


def minutes(day: date) -> list[datetime]:
    start = datetime.combine(day, time(9, 15))
    return [start + timedelta(minutes=i) for i in range(375)]


def spot(k: int, i: int) -> float:
    """Index level on day `k` at minute `i` of the session."""
    level = 23000 + OPENS[k] + 40 * math.sin(i / 30)
    if k == 3:  # rally: +260 between 10:45 and 11:45, held
        level += 260 * min(1.0, max(0.0, (i - 90) / 60))
    if k == 8:  # fall: -250 between 12:35 and 13:35, held
        level -= 250 * min(1.0, max(0.0, (i - 200) / 60))
    return round(level, 2)


def _tick(x: float) -> float:
    return max(0.05, round(round(x / 0.05) * 0.05, 2))


def _settles(expiry: date) -> datetime:
    """The session an expiry actually settles in: the last trading day on or before it."""
    sessions = [d for d in DAYS if d <= expiry]
    day = expiry if expiry > END or not sessions else sessions[-1]
    return datetime.combine(day, time(15, 30))


def _listed(day: date) -> list[date]:
    """The expiries a day carries bars for: the next three weeklies, and the monthly."""
    ahead = [e for e in EXPIRIES if _settles(e).date() >= day][:3]
    return sorted(set(ahead) | {MONTHLIES[0]})


def _rows(path: Path) -> tuple[Path, Path]:
    index_csv, option_csv = path / "index.csv", path / "option.csv"
    with index_csv.open("w", newline="") as fi, option_csv.open("w", newline="") as fo:
        index_out, option_out = csv.writer(fi), csv.writer(fo)
        for k, day in enumerate(DAYS):
            last: dict[tuple[date, float, str], float] = {}
            for i, ts in enumerate(minutes(day)):
                s = spot(k, i)
                index_out.writerow([INDEX, "1", ts, s - 2, s + 3, s - 3, s, 0])
                vix = round(13 + 0.3 * k + 0.5 * math.sin(i / 50), 2)
                index_out.writerow([INDIA_VIX, "1", ts, vix, vix, vix, vix, 0])
                if day == DARK_DAY and DARK[0] <= ts.time() <= DARK[1]:
                    continue
                for expiry in _listed(day):
                    years = max((_settles(expiry) - ts).total_seconds(), 60) / (365 * 24 * 3600)
                    for j, strike in enumerate(STRIKES):
                        for kind in ("CE", "PE"):
                            if (strike, kind) == ILLIQUID and i % 10:
                                continue
                            sigma = 0.13 + 0.01 * math.sin(k) + abs(strike - s) / 400_000
                            c = _tick(bs.price(s, strike, RATE, sigma, years, kind))
                            o = last.get((expiry, strike, kind), c)
                            last[(expiry, strike, kind)] = c
                            option_out.writerow([
                                UNDERLYING, expiry, kind, strike, ts,
                                o, max(o, c) + 0.5, _tick(min(o, c) - 0.5), c,
                                LOT * ((i * 7 + j) % 11), LOT * (2000 + i + 13 * j),
                            ])
    return index_csv, option_csv


def build(conn: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    """The market, in a store with the real schema. In memory unless given a connection."""
    conn = conn or duckdb.connect()
    conn.execute(SCHEMA)
    for e in EXPIRIES:
        conn.execute("INSERT INTO expiry VALUES (?, ?, 'options')", [UNDERLYING, e])
    for e in MONTHLIES:
        conn.execute("INSERT INTO expiry VALUES (?, ?, 'futures')", [UNDERLYING, e])
    with tempfile.TemporaryDirectory() as tmp:
        index_csv, option_csv = _rows(Path(tmp))
        conn.execute(f"COPY index_bar FROM '{index_csv}' (HEADER false)")
        conn.execute(f"COPY option_bar FROM '{option_csv}' (HEADER false)")
    return conn


def export_parquet(conn: duckdb.DuckDBPyConnection, out: Path) -> None:
    """The store's tables as Parquet, for an engine that reads files rather than DuckDB."""
    out.mkdir(parents=True, exist_ok=True)
    for table in ("expiry", "index_bar", "option_bar"):
        conn.execute(f"COPY {table} TO '{out / table}.parquet' (FORMAT parquet)")
