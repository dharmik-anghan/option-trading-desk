"""Shared result type for risk checks — every check in `risk/` returns one
of these so the Phase 5 confirm screen can render a consistent pass/fail +
reason for each check rather than each module inventing its own shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskCheckResult:
    passed: bool
    reason: str
