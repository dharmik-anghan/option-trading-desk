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

## Status

See `docs/PHASES.md` for what's built vs. planned.
