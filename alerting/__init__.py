"""Deciding what is worth telling you about, and telling you once.

Layered so the decision is testable without any of the plumbing:

- `rules.py` - every condition that holds right now. Pure.
- `engine.py` - folding those into a log, once each. Pure.
- `format.py` - wording, kept byte-identical to the frontend's.

The loop that does it on an interval and hands what fired to a notifier is
`jobs/alert_watcher.py`, with the desk's other background work.

This began as TypeScript in the browser, which meant nothing was watching once
the tab was closed. The port keeps the rules and their tests as they were: each
guard in `engine.py` exists because of a specific way the same alert reached the
screen twice.
"""

from alerting.engine import FIRE_COOLDOWN_MS, dedupe_log, reconcile
from alerting.models import Alert, Condition, Limits, Outcome, Severity
from alerting.rules import evaluate

__all__ = [
    "FIRE_COOLDOWN_MS",
    "Alert",
    "Condition",
    "Limits",
    "Outcome",
    "Severity",
    "dedupe_log",
    "evaluate",
    "reconcile",
]
