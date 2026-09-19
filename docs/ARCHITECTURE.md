# Architecture

A personal, semi-automated options trading desk. Fyers is the first broker;
the design keeps broker-specific code behind one seam so other brokers
(Zerodha, Upstox, ...) can be added later without touching strategy or risk
logic.

## Layers

```
strategies/  (strategy definitions: legs, entry/exit rules)
     |
risk/        (position sizing, max loss, margin checks, portfolio greeks)
     |
analytics/   (Black-Scholes, Greeks, IV, payoff diagrams)
     |
broker/      (abstract Broker interface  ->  FyersBroker adapter)
     |
storage/     (SQLite: trades, positions, option-chain snapshots)
```

`strategies/`, `risk/`, and `execution/` depend on the abstract `Broker`
protocol in `broker/base.py`, never on a concrete adapter like
`broker/fyers.py`. `execution/` (the `ExecutionManager`) is the only code
that is allowed to call into `broker/` — strategies emit signals, they never
place orders directly.

## The `Broker` contract

Defined once in `broker/base.py` as a `Protocol`. Every adapter (starting
with `FyersBroker`) must implement it in full, and contract tests in
`tests/broker/test_contract.py` run against *any* implementation — including
a `FakeBroker` test double — so a new broker adapter is verified against the
same behavior Fyers is, before it's ever wired into strategies.

## Why this shape

- **Open/Closed**: new strategies subclass `Strategy` without editing it;
  new brokers implement `Broker` without editing callers.
- **Liskov substitution**: any `Broker` must be swappable with no
  caller-side special-casing — enforced by the shared contract tests.
- **Dependency inversion**: the direction of dependency always points at the
  abstract interface, not the concrete broker.

## What's built (Phase 1)

- `broker/models.py` — broker-agnostic pydantic models (`Quote`, `OptionChain`,
  `OptionChainRow`, `Greeks`, `Candle`)
- `broker/base.py` — the `Broker` `Protocol`, scoped to what's needed so far
  (quotes, option chain, history, tick subscription signature)
- `broker/fake.py` — `FakeBroker`, an in-memory implementation used by
  contract tests and by anything above `broker/` that wants to test without
  network calls
- `broker/fyers.py` — `FyersBroker`; parsing logic (`parse_quotes`,
  `parse_option_chain`, `parse_candles`) is factored out as pure functions
  tested against real recorded API responses in `tests/broker/fixtures/`
- `storage/db.py` + `storage/option_chain_repo.py` — SQLite persistence for
  timestamped option-chain snapshots, the basis for later historical
  premium/IV comparisons

`Broker.subscribe_ticks` is defined but `FyersBroker` raises
`NotImplementedError` for it — deferred until a real consumer needs live
ticks (see `docs/PHASES.md`).

## What's built (Phase 2 & 3)

- `analytics/black_scholes.py` — dependency-free Black-Scholes pricer/Greeks,
  a fallback for brokers that don't supply Greeks (Fyers does)
- `analytics/payoff.py` — `Leg`/`analyze()`: max profit/loss/breakevens for
  any multi-leg combination, from first principles (no broker computes this)
- `strategies/base.py` — `Strategy` ABC: `build_legs(chain) -> list[Leg]`,
  deliberately scoped to strike selection only (see `docs/PHASES.md` for why
  entry conditions and exit/adjustment rules are deferred)
- `strategies/selection.py`, `short_strangle.py`, `iron_condor.py`,
  `credit_spread.py` — concrete strike selection + strategy definitions

## What's built (Phase 4)

- `broker/base.py` gained `get_funds() -> Funds` (real account balances) —
  scope grows only as a phase needs it, not speculatively
- `risk/result.py` — `RiskCheckResult`, the shared pass/fail + reason shape
  every check in `risk/` returns
- `risk/margin.py`, `risk/limits.py`, `risk/sizing.py`,
  `risk/portfolio_greeks.py` — individually testable risk checks
- `risk/pre_trade_check.py` — `run_pre_trade_checks()` composes the above
  into the one gate Phase 5's confirm-before-order flow will call
- **Known gap** (see `docs/GLOSSARY.md`): Fyers' SDK has no pre-trade margin
  calculator, so `required_margin` is caller-supplied, not computed from a
  real API, until that's resolved

## Status

See `docs/PHASES.md` for what's built vs. planned.
