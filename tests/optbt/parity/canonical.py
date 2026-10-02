"""A run's result as exact, comparable data: every fill, charge, tag, event and
equity point. Floats are written with `repr`, so two engines agree only if they
agree to the last bit - the parity test then decides how close is close enough."""

from __future__ import annotations

from typing import Any

from optbt.engine import Leg, Result


def _leg(leg: Leg) -> dict[str, Any]:
    return {
        "key": str(leg.key),
        "side": int(leg.side),
        "lots": leg.lots,
        "lot_size": leg.lot_size,
        "tag": leg.tag,
        "entry_ts": leg.entry_ts.isoformat(),
        "entry_price": leg.entry_price,
        "exit_ts": leg.exit_ts.isoformat() if leg.exit_ts else None,
        "exit_price": leg.exit_price,
        "exit_reason": leg.exit_reason,
        "stop": leg.stop,
        "target": leg.target,
        "charges": leg.charges.total,
    }


def canonical(result: Result) -> dict[str, Any]:
    return {
        "days": result.days,
        "abandoned": result.abandoned_orders,
        "skipped": dict(sorted(result.skipped.items())),
        "equity": [[d.isoformat(), v] for d, v in result.equity],
        "trades": [
            {
                "id": t.id,
                "opened": t.opened.isoformat(),
                "closed": t.closed.isoformat() if t.closed else None,
                "reason": t.reason,
                "net": t.net,
                "worst": t.worst,
                "best": t.best,
                "tags": dict(sorted(t.tags.items())),
                "legs": [_leg(leg) for leg in t.legs],
                "events": t.events,
            }
            for t in result.trades
        ],
    }
