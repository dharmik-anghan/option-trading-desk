# Build phases

Single source of truth for what's done and what's next. Each phase is built
test-first (TDD) and isn't considered done until its checkpoint passes.

- [x] **Phase 0 — Scaffolding + prove Fyers login works** — done
  - [x] Repo structure, `.env` config, mypy/ruff/pytest set up
  - [x] `scripts/fyers_login.py` + `scripts/verify_login.py`
  - [x] Checkpoint: `uv run python scripts/verify_login.py` printed `Login OK`
    against the real Fyers account
- [ ] **Phase 1 — Broker layer + market data**
  - `Broker` protocol + contract tests + `FakeBroker`
  - `FyersBroker`: quotes, option chain, historical candles, WebSocket ticks
  - SQLite storage for option-chain snapshots
  - Checkpoint: fetch a real Nifty/BankNifty option chain and store it
- [ ] **Phase 2 — Options analytics**
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
