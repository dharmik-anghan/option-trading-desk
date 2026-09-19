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
- [x] **Phase 4 — Risk management** — done
  - Known gap: Fyers' SDK has no pre-trade SPAN/exposure margin calculator,
    only account funds. `required_margin` is caller-supplied throughout,
    not computed from a real margin API. See `docs/GLOSSARY.md`.
  - [x] `Broker.get_funds()` added (real account balances) + `FyersBroker`/
    `FakeBroker` implementations, tested against a real recorded funds
    fixture (`tests/broker/fixtures/fyers_funds.json`)
  - [x] `risk/margin.py` — `check_sufficient_margin`
  - [x] `risk/sizing.py` — `max_quantity_for_risk`; correctly returns 0 for
    unbounded (`math.inf`) risk rather than a misleading finite number
  - [x] `risk/limits.py` — `check_max_loss_limit` (per-trade),
    `check_daily_kill_switch` (takes today's P&L as input; Phase 6 computes
    that number for real)
  - [x] `risk/portfolio_greeks.py` — `aggregate_portfolio_greeks`: bought
    legs contribute their own Greeks, sold legs the negative (short theta
    becomes a positive portfolio contribution)
  - [x] `risk/pre_trade_check.py` — `run_pre_trade_checks` ties margin +
    max-loss + sizing into the one gate Phase 5's confirm screen will call
  - [x] Checkpoint: `uv run python scripts/pre_trade_check.py` ran the full
    gate against a real live option chain and real account funds
- [~] **Phase 5 — Semi-auto execution** — code done, live checkpoint pending
  - Scope note: no separate multi-strategy "scanner" was built. The CLI
    script takes one strategy + symbol at a time rather than scanning many
    strategies for candidate signals automatically - that's a real feature
    gap vs. the original plan, not yet needed since nothing today generates
    multiple candidates to rank. Revisit once there's an actual need to
    compare several strategies/symbols at once.
  - Leg gained an optional `symbol` field (populated by strategies from the
    chain row) so `execution/` can actually place an order for it - payoff
    math itself still doesn't need it, kept optional for that reason.
  - `Broker` gained `place_order()`/`get_positions()`, tested against a
    real-schema-based fixture for positions and a fixture built from Fyers'
    publicly documented place-order response (not a live capture - that
    would mean placing a real order just to record a fixture).
  - [x] `execution/manager.py` — `ExecutionManager`: the only code allowed
    to call `Broker.place_order`; converts `Leg`s to `OrderRequest`s
  - [x] `execution/confirm.py` — `confirm_and_place`: the human-in-the-loop
    gate. Never places an order without the exact string `CONFIRM`, and
    never even prompts if the Phase 4 pre-trade checks failed. The
    `confirm` callable is injected so this is fully unit-testable without a
    real terminal - see `tests/execution/test_confirm.py`, including a test
    that asserts the confirm function is *never called* when checks fail.
  - [x] `scripts/place_strategy_order.py` — ties chain fetch, strategy,
    pre-trade checks, confirm, and post-order position reconciliation
    together. Dry-run verified against real Fyers data (real funds, real
    option chain, real risk checks) with a deliberate non-CONFIRM answer -
    correctly aborted with zero orders placed.
  - [ ] Checkpoint: **you** run `scripts/place_strategy_order.py`
    interactively and type `CONFIRM` yourself to place one real small-lot
    order, since that's your money and your call to make - not something
    to automate away. See docs/SETUP.md for guidance.
- [ ] **Phase 6 — Position/P&L tracking + minimal dashboard**

**Deferred:** multi-broker adapters, backtesting engine, full automation.
