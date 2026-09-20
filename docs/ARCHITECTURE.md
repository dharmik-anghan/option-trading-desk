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

## What's built (Phase 5)

- `analytics/payoff.Leg` gained an optional `symbol` field, populated by
  strategies from the chain row - payoff math itself doesn't need it, only
  `execution/` does, to place a real order
- `broker/base.py` gained `place_order()`/`get_positions()`
- `execution/manager.py` — `ExecutionManager`: the *only* code allowed to
  call `Broker.place_order`, converting `Leg`s to `OrderRequest`s
- `execution/confirm.py` — `confirm_and_place`: the human-in-the-loop gate.
  Requires the exact string `CONFIRM`; never even prompts if Phase 4's
  pre-trade checks failed. The `confirm` callable is injected so this whole
  flow is unit-testable without a real terminal
- `scripts/place_strategy_order.py` — the end-to-end CLI: fetch chain, build
  strategy, run pre-trade checks, confirm, place, reconcile against
  `get_positions()`
- Scope note: no multi-strategy "scanner" yet — one strategy/symbol per run.
  See `docs/PHASES.md` for why.

## What's built (Phase 6)

- `broker/models.Funds` gained `realized_pnl` (Fyers' "Realized Profit and
  Loss" funds field)
- `execution/portfolio_status.py` — `get_portfolio_status()`: combines real
  per-position unrealized P&L with real realized P&L into one
  `PortfolioStatus.total_pnl`, which `risk.limits.check_daily_kill_switch`
  (Phase 4) acts on
- `storage/portfolio_repo.py` — historical P&L snapshots. Depends only on
  `broker.models.Position`, not on `execution.PortfolioStatus` — a layering
  violation (storage importing from execution, inverting the dependency
  direction below) was caught and fixed during development rather than
  left in
- `scripts/portfolio_status.py` — the "minimal dashboard": a CLI status
  view, not a web UI. A real dashboard stays a deliberate, undecided choice
  until there's a felt need for one beyond the CLI

## Web dashboard

Decided: React + Vite + TypeScript frontend, FastAPI backend, **read-only**
to start (order placement stays in the CLI's `CONFIRM` flow — see
`execution/confirm.py` for why that gate matters).

```
frontend/  (React + Vite + TS - fetches from the API below)
     |
api/app.py  (FastAPI - read-only: get_positions, get_funds, get_option_chain;
             never place_order)
     |
(same broker/analytics/risk/strategies/storage stack as everything else)
```

- `api/dependencies.py` — `get_broker()`/`get_db_path()` as FastAPI
  dependencies, overridden in tests with `FakeBroker`/a temp DB path so
  the test suite never touches real credentials or the real `data/trading.db`
- `api/schemas.py` — API wire types, separate from internal domain types
  (`Leg`, `PortfolioStatus` are dataclasses; the API boundary gets its own
  pydantic schemas) for the same reason `broker/models.py` translates
  Fyers' wire format rather than exposing it directly
- **Bug caught and fixed during development**: `analyze()`'s unbounded
  max profit/loss (`math.inf`/`-math.inf`) can't be serialized as JSON —
  Python's `json.dumps` emits the literal token `Infinity`, which is not
  valid JSON and a browser's `JSON.parse` rejects outright. The API
  represents these as `null` instead; a regression test
  (`test_strategy_endpoint_serializes_unbounded_risk_as_json_null`) asserts
  the raw response text never contains the string `Infinity`
- CORS is open to any `localhost`/`127.0.0.1` port for local dev (Vite picks
  a free port, which varies) — tighten before exposing beyond localhost

## Status

See `docs/PHASES.md` for what's built vs. planned.
