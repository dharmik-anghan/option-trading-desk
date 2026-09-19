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
- [x] **Phase 2 — Options analytics** — done
  - Note: Fyers' option chain already returns Delta/Gamma/Theta/Vega/IV per
    strike (`greeks=1` param, see `broker/fyers.py`), so `FyersBroker` doesn't
    need us to compute them. The self-contained Black-Scholes/Greeks
    implementation below exists as: (a) a fallback for brokers that don't
    supply Greeks once multi-broker support lands, and (b) the basis for
    payoff/max-loss/breakeven math, which no broker computes for you. Prefer
    broker-supplied Greeks/IV when present; fall back to self-computed ones
    otherwise.
  - [x] `analytics/black_scholes.py` — `price()`/`greeks()`, dependency-free
    (plain `math`, no numpy/scipy — matches the lightweight stack decision).
    Checkpoint: tested against Hull's textbook reference example (S=42,
    K=40, r=10%, sigma=20%, T=0.5y) in `tests/analytics/test_black_scholes.py`,
    not re-derived against itself
  - [x] `analytics/payoff.py` — multi-leg `Leg`/`analyze()`: max profit, max
    loss (including unbounded cases as `+/-math.inf`), and breakevens, tested
    against hand-computed textbook strategies (long call, short put, bull
    call spread, long straddle, iron condor) in `tests/analytics/test_payoff.py`
- [x] **Phase 3 — Strategy framework** — done
  - Scope: "given a chain, which legs does this strategy trade" only.
    Entry-condition evaluation (e.g. IV-percentile-based entries) needs
    historical query logic not built yet; exit/adjustment rules need live
    position state. Both deferred to Phase 4/5 rather than built as unused
    hooks now.
  - [x] `strategies/base.py` — `Strategy` ABC (`build_legs(chain) -> list[Leg]`)
  - [x] `strategies/selection.py` — `select_by_delta`, `select_atm`, tested
    against a fixed synthetic chain fixture (`tests/strategies/conftest.py`)
  - [x] `strategies/short_strangle.py`, `iron_condor.py`, `credit_spread.py`
    (bullish=bull put spread, bearish=bear call spread)
  - [x] `tests/strategies/test_payoff_integration.py` — ties Phase 2 + 3
    together: confirms the naked short strangle has unbounded max loss while
    iron condor/credit spread are fully capped, using real `analyze()`
  - [x] Checkpoint: `uv run python scripts/analyze_strategy.py` built all
    three strategies from a real live Nifty option chain and printed
    correct legs/max-profit/max-loss/breakevens (short strangle correctly
    showed `-inf` max loss; the other two showed finite bounded numbers)
- [ ] **Phase 4 — Risk management**
  - Margin checks, position sizing, max loss, portfolio Greeks, kill-switch
- [ ] **Phase 5 — Semi-auto execution**
  - Scanner + CLI confirm-then-place flow via `ExecutionManager`
  - Checkpoint: one real small-lot order placed and reconciled
- [ ] **Phase 6 — Position/P&L tracking + minimal dashboard**

**Deferred:** multi-broker adapters, backtesting engine, full automation.
