"""Shark's wire format, turned into the desk's models.

Held apart from the HTTP so it can be tested against saved responses rather than
a live account - `tests/broker/fixtures/shark/` holds real ones, with the
account's own figures replaced.

The venue is Binance-shaped: single-letter ticker fields, numbers as strings,
milliseconds as integers or as strings depending on the endpoint. Everything is
read defensively because a missing field must not become a zero - a zero price is
a number the desk would act on, and absence is not zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from broker.models import Candle, Position, Quote
from broker.shark.models import PerpPosition


class SharkParseError(ValueError):
    """The venue answered with something this code cannot read."""


def _num(value: Any, field: str) -> float:
    """A float from a string or a number, or an error naming the field."""
    if value is None or value == "":
        raise SharkParseError(f"missing {field}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SharkParseError(f"{field} is not a number: {value!r}") from exc


def _opt_num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_ticker(payload: dict[str, Any]) -> Quote:
    """One instrument's 24-hour ticker.

    The response nests under "data". Fields are Binance's: c last, o the price 24
    hours ago, h/l the range, v base volume, p the change, E the event time.

    `prev_close` is derived as last minus change rather than taken from `o`: `o`
    is the price at the start of a rolling 24-hour window, which on a market that
    never closes is not yesterday's close and not what a day-change should be
    measured against. The venue's own `p` is the figure it displays.

    No bid or ask: the ticker does not carry them, and a depth request would be a
    second call for something nothing here reads yet. Left at zero, which the
    desk already treats as "not quoted".
    """
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise SharkParseError("ticker payload has no object to read")

    last = _num(data.get("c"), "c (last price)")
    change = _opt_num(data.get("p"))
    event_ms = data.get("E")

    return Quote(
        symbol=str(data.get("s") or ""),
        ltp=last,
        open=_num(data.get("o"), "o (open)"),
        high=_num(data.get("h"), "h (high)"),
        low=_num(data.get("l"), "l (low)"),
        prev_close=last - change if change is not None else last,
        volume=_opt_num(data.get("v")) or 0.0,
        bid=0.0,
        ask=0.0,
        timestamp=(
            datetime.fromtimestamp(int(event_ms) / 1000, tz=UTC)
            if event_ms is not None
            else datetime.now(UTC)
        ),
    )


def parse_klines(rows: list[dict[str, Any]]) -> list[Candle]:
    """Candles, oldest first, one per distinct start time.

    Deduplicated on `startTime` because the venue repeats buckets on some
    intervals - the reference implementation hit this on 1d. The later entry for a
    bucket wins: it reflects more complete aggregation. Without this the series is
    not strictly ascending, which is wrong arithmetic before it is a broken chart.
    """
    by_start: dict[int, Candle] = {}
    for row in rows:
        start = row.get("startTime")
        if start is None:
            raise SharkParseError("kline has no startTime")
        start_ms = int(start)
        by_start[start_ms] = Candle(
            timestamp=datetime.fromtimestamp(start_ms / 1000, tz=UTC),
            open=_num(row.get("open"), "open"),
            high=_num(row.get("high"), "high"),
            low=_num(row.get("low"), "low"),
            close=_num(row.get("close"), "close"),
            volume=_opt_num(row.get("volume")) or 0.0,
        )
    return [by_start[k] for k in sorted(by_start)]


def parse_position(row: dict[str, Any]) -> Position:
    """One position.

    Signed by `positionType`: the venue reports SHORT with a positive quantity, so
    the sign has to be applied rather than read. A short held as a positive number
    is how a hedge gets counted as exposure.

    Unrealized P&L is read from whichever field the venue provides and left at
    zero when it provides none. This is the one shape here not verified against a
    live example - the account had no open crypto position when these were
    captured, and a closed one carries `realizedProfit` instead. The field names
    tried below are the plausible ones; if the desk shows a zero for an open
    position, this is the first place to look.
    """
    symbol = str(row.get("contractPair") or row.get("symbol") or "")
    if not symbol:
        raise SharkParseError("position has no contract pair")

    size = _opt_num(row.get("positionAmount"))
    if size is None:
        size = _opt_num(row.get("quantity")) or 0.0
    if str(row.get("positionType", "")).upper() == "SHORT":
        size = -abs(size)

    unrealized = next(
        (
            value
            for value in (
                _opt_num(row.get("unrealizedProfit")),
                _opt_num(row.get("unrealisedProfit")),
                _opt_num(row.get("pnl")),
            )
            if value is not None
        ),
        0.0,
    )

    return Position(
        symbol=symbol,
        net_quantity=size,
        average_price=_opt_num(row.get("entryPrice")) or 0.0,
        # The mark price is not in this payload; the desk prices positions from a
        # quote it already polls, so an entry price stands in rather than a zero.
        ltp=_opt_num(row.get("markPrice")) or _opt_num(row.get("entryPrice")) or 0.0,
        unrealized_pnl=unrealized,
        # Carries the margin mode, which is the perps equivalent of a product
        # type and the thing that decides how a liquidation is calculated.
        product_type=str(row.get("marginType") or row.get("contractType") or "PERPETUAL"),
    )


def parse_positions(rows: list[dict[str, Any]]) -> list[Position]:
    """Only the positions actually open.

    Closure is read from `positionStatus`, not from the size. A closed position
    keeps its original `positionAmount` - a real closed XAUUSDT row reports
    status CLOSED with amount 0.01 and `positionSize` 0 - so filtering on size
    alone reads a finished trade as a live holding. The endpoint asked for is
    /v1/positions/OPEN, but a row that says otherwise is believed over the URL.
    """
    open_rows = [
        row
        for row in rows
        if str(row.get("positionStatus", "OPEN")).upper() == "OPEN"
    ]
    return [p for p in (parse_position(row) for row in open_rows) if p.net_quantity != 0]


def parse_perp_position(row: dict[str, Any]) -> PerpPosition:
    """One leveraged position, with everything that decides whether it survives.

    Unrealised P&L is read from whichever key the venue uses, because this is the
    shape that could not be verified: the account had no open position when these
    fixtures were captured, and a closed one reports `realizedProfit` instead. The
    names tried below are the plausible ones. When none is present the figure is
    None rather than zero, and the desk works it out from the mark price instead -
    which is why a missing field here shows as a computed P&L rather than a wrong
    one.
    """
    symbol = str(row.get("contractPair") or row.get("symbol") or "")
    if not symbol:
        raise SharkParseError("position has no contract pair")

    unrealized = next(
        (
            value
            for value in (
                _opt_num(row.get("unrealizedProfit")),
                _opt_num(row.get("unrealisedProfit")),
                _opt_num(row.get("unrealizedPnl")),
                _opt_num(row.get("pnl")),
            )
            if value is not None
        ),
        None,
    )
    unrealized_margin = next(
        (
            value
            for value in (
                _opt_num(row.get("unrealizedProfitInMarginAsset")),
                _opt_num(row.get("unrealisedProfitInMarginAsset")),
            )
            if value is not None
        ),
        None,
    )

    return PerpPosition(
        symbol=symbol,
        side=str(row.get("positionType") or "").upper(),
        quantity=abs(_opt_num(row.get("positionAmount")) or _opt_num(row.get("quantity")) or 0.0),
        entry_price=_opt_num(row.get("entryPrice")) or 0.0,
        mark_price=_opt_num(row.get("markPrice")),
        leverage=_opt_num(row.get("leverage")) or 1.0,
        liquidation_price=_opt_num(row.get("liquidationPrice")),
        margin_type=str(row.get("marginType") or ""),
        margin=_opt_num(row.get("margin")) or 0.0,
        margin_asset=str(row.get("marginAsset") or ""),
        unrealized_pnl=unrealized,
        unrealized_pnl_in_margin_asset=unrealized_margin,
        position_id=str(row.get("positionId") or row.get("id") or ""),
    )


def parse_perp_positions(rows: list[dict[str, Any]]) -> list[PerpPosition]:
    """Only the positions actually open - see `parse_positions` for why status is
    believed over the endpoint asked for."""
    return [
        parse_perp_position(row)
        for row in rows
        if str(row.get("positionStatus", "OPEN")).upper() == "OPEN"
        and (_opt_num(row.get("positionAmount")) or _opt_num(row.get("quantity")) or 0.0)
    ]
