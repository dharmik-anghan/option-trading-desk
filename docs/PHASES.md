# Build phases

Single source of truth for what's done and what's next. Each phase is built
test-first (TDD) and isn't considered done until its checkpoint passes.

- [x] **Phase 0 — Scaffolding + prove Fyers login works** — done
  - [x] Repo structure, `.env` config, mypy/ruff/pytest set up
  - [x] `scripts/fyers_login.py` + `scripts/verify_login.py`
  - [x] Checkpoint: `uv run python scripts/verify_login.py` printed `Login OK`
    against the real Fyers account
- [x] **Phase 1 — Broker layer + market data** — done
  - [x] `Broker` protocol (`broker/base.py`) + contract tests (`tests/broker/test_contract.py`)
    + `FakeBroker` test double
  - [x] `FyersBroker`: quotes, option chain (incl. broker-supplied Greeks/IV),
    historical candles — parsing tested against real recorded fixtures
    (`tests/broker/fixtures/`, `tests/broker/test_fyers_parsing.py`)
  - [x] SQLite storage for option-chain snapshots (`storage/db.py`,
    `storage/option_chain_repo.py`) — this is what makes historical
    premium/IV comparison (e.g. today's straddle vs last week's) possible later
  - [~] WebSocket tick streaming — **deferred**, not implemented in Phase 1.
    It's a long-lived stateful connection with no automated way to verify
    without a live market session, and nothing yet consumes live ticks.
    `Broker.subscribe_ticks` raises `NotImplementedError` until the phase
    that first needs live ticks (likely Phase 5/6) implements it for real.
  - [x] Checkpoint: `uv run python scripts/fetch_option_chain.py` fetched a
    real Nifty option chain (42 strikes) and stored it to SQLite
- [ ] **Phase 2 — Options analytics**
  - Note: Fyers' option chain already returns Delta/Gamma/Theta/Vega/IV per
    strike (`greeks=1` param, see `broker/fyers.py`), so `FyersBroker` doesn't
    need us to compute them. A self-contained Black-Scholes/Greeks
    implementation is still worth building for: (a) brokers that don't
    supply Greeks once multi-broker support lands, and (b) payoff/max-loss/
    breakeven calculations for multi-leg strategies, which no broker computes
    for you. Prefer broker-supplied Greeks/IV when present; fall back to
    self-computed ones otherwise.
  - Black-Scholes pricer, Greeks, payoff/max-loss/breakeven calculator
  - Checkpoint: Greeks match published reference values
- [ ] **Phase 3 — Strategy framework**
  - `Strategy` base class; short strangle, iron condor, credit spread
- [ ] **Phase 4 — Risk management**
  - Margin checks, position sizing, max loss, portfolio Greeks, kill-switch
- [ ] **Phase 5 — Semi-auto execution**
  - Scanner + CLI confirm-then-place flow via `ExecutionManager`
  - Checkpoint: one real small-lot order placed and reconciled
- [ ] **Phase 6 — Position/P&L tracking + minimal dashboard**

**Deferred:** multi-broker adapters, backtesting engine, full automation.
