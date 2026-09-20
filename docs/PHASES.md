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
- [~] **Phase 5 — Semi-auto execution** — code done, live order checkpoint deferred by choice
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
  - [ ] Checkpoint: placing one real order is deliberately **not required**
    to trust this code - the dry-run above already exercised the full real
    pipeline (funds, live chain, risk checks) end-to-end; only the final
    `place_order` API call itself is unverified against a live fill. You
    decided not to test with a real order while markets are closed, which
    is the right call - orders placed outside market hours would need AMO
    handling this code doesn't yet have (`offlineOrder` is hardcoded
    `False` in `broker/fyers.py`). Run it yourself whenever you want to
    verify a real fill - no urgency.
- [x] **Phase 6 — Position/P&L tracking + minimal dashboard** — done
  - `Funds` gained `realized_pnl` (from Fyers' "Realized Profit and Loss"
    funds field, previously unused)
  - [x] `execution/portfolio_status.py` — `get_portfolio_status`: combines
    real per-position unrealized P&L (`Broker.get_positions`) with real
    realized P&L (`Broker.get_funds`) into one `PortfolioStatus.total_pnl`
  - [x] `tests/execution/test_kill_switch_integration.py` — ties Phase 4's
    pure `check_daily_kill_switch` to Phase 6's real portfolio P&L
  - [x] `storage/portfolio_repo.py` — historical P&L snapshots, same
    pattern as `storage/option_chain_repo.py`. Deliberately depends only on
    `broker.models.Position` (not `execution.PortfolioStatus`) to keep
    `storage/` at the bottom of the dependency direction - caught and fixed
    a layering violation during development where it briefly imported
    `execution/` directly.
  - [x] `scripts/portfolio_status.py` — the "minimal dashboard": CLI view of
    open positions, realized/unrealized/total P&L, the kill-switch check,
    and persists a snapshot. A real web dashboard remains a deliberate,
    undecided-until-needed choice (see docs/ARCHITECTURE.md and the earlier
    frontend-integration discussion) - this stays CLI-first for now.
  - [x] Checkpoint: `uv run python scripts/portfolio_status.py` ran against
    the real account - 4 real open positions, correct realized/unrealized/
    total P&L, kill-switch check passed, snapshot persisted to SQLite

- [x] **Phase 7 — Web dashboard** — done, read-only at first (superseded by
  Phase 9, which moved order placement into the dashboard)
  - Decided: React + Vite + TypeScript frontend, FastAPI backend,
    read-only to start (order placement stays in the CLI's `CONFIRM` flow)
  - [x] `api/app.py` — FastAPI app exposing `/api/portfolio`,
    `/api/option-chain/{symbol}`, `/api/strategies/{name}`,
    `/api/portfolio/history`. Only calls broker methods that can't move
    money; `place_order` is never reachable from this API
  - [x] `api/dependencies.py` — `get_broker()`/`get_db_path()` as FastAPI
    dependencies, overridden in tests with `FakeBroker`/a temp DB path
  - [x] `api/schemas.py` — API wire types kept separate from internal
    domain dataclasses (`Leg`, `PortfolioStatus`)
  - **Bug caught and fixed during development**: `analyze()`'s unbounded
    max profit/loss (`math.inf`) isn't valid JSON - `json.dumps` emits the
    literal token `Infinity`, which a browser's `JSON.parse` rejects
    outright. Fixed by serializing unbounded values as `null`; regression
    test `test_strategy_endpoint_serializes_unbounded_risk_as_json_null`
    asserts the raw response text never contains `Infinity`.
  - [x] `frontend/` — Vite React-TS app: `PortfolioPanel` (positions +
    P&L), `StrategyPanel` (read-only strategy preview), `HistoryPanel`
    (P&L over time). TypeScript compiles clean, `oxlint` clean, production
    build succeeds.
  - [x] Checkpoint: backend verified against real live Fyers data (all 4
    endpoints, correct CORS headers). Frontend rendering was verified by
    the user in their own browser (the agent's browser-automation tool
    wasn't connected in this environment) - user confirmed positions/P&L
    render correctly, and caught a real display bug (unrounded
    floating-point P&L, e.g. `942.5000000000017`) which was fixed in
    `frontend/src/format.ts`.

- [x] **Phase 8 — TOTP auto-login** — done
  - Problem: the manual browser+paste login (Phase 0) had to be repeated
    every trading day, since Fyers access tokens expire daily.
  - Considered installing the `multi-broker-sdk` PyPI package (does exactly
    this), but it's anonymous (no listed author/repo) at v0.1.1 and would
    need the TOTP secret + PIN - too sensitive to hand to an unaudited,
    unmaintained dependency whose future updates can't be verified.
  - Instead: fully read and audited its ~460-line Fyers implementation
    (confirmed it only talks to Fyers' own domains, computes TOTP locally,
    no telemetry), then **ported the technique** into our own code rather
    than taking the dependency - same benefit, no supply-chain exposure,
    and it's now something we can maintain if Fyers changes these
    undocumented endpoints.
  - [x] `broker/fyers_auth.py` — `auto_login()`: replicates the manual
    login steps (OTP -> TOTP verify -> PIN verify -> get auth code ->
    exchange for token) against Fyers' undocumented login endpoints. Uses
    `pyotp` for TOTP (standard RFC 6238, same algorithm the reference
    package's hand-rolled version used). HTTP session and the final token
    exchange are both injectable, so tests never hit real Fyers servers or
    need real credentials (`tests/broker/test_fyers_auth.py`).
  - [x] `settings.py` gained optional `fyers_username`/`fyers_totp_key`/
    `fyers_pin` + `has_auto_login_credentials` - all-or-nothing, missing
    any of the three keeps you on the manual flow
  - [x] `scripts/fyers_login.py` tries auto-login first when configured,
    falls back to the manual browser flow on any `AutoLoginError` (these
    are undocumented endpoints Fyers could change without notice)
  - Checkpoint: pending — the user needs to add their own
    `FYERS_USERNAME`/`FYERS_TOTP_KEY`/`FYERS_PIN` to `.env` (never shared
    in chat) before this can be verified against a real login

- [x] **Phase 9 — Dashboard redesign + order placement moves off the CLI**
  — done
  - **Visual redesign**: dark charcoal-navy "terminal ledger" design
    (amber accent, tabular monospace figures for every price/quantity/P&L,
    hairline-separated panels instead of rounded SaaS cards) — grounded in
    real trading-terminal visual history (Bloomberg/Reuters amber-phosphor
    lineage) rather than generic dashboard defaults. Palette validated:
    profit/loss colors are the `dataviz` skill's fixed status green/red
    (not reinvented), amber accent at 8.9:1 contrast against the
    background. Added `HistoryPanel`'s P&L bar chart (rounded data-ends,
    square baseline, direct labels in neutral text, position-relative-to-
    zero as the non-color polarity cue) following the skill's mark specs.
  - **Order placement moved into the dashboard**, replacing the CLI's
    confirm-and-place flow entirely per explicit decision:
    `scripts/place_strategy_order.py` and `execution/confirm.py` (and its
    tests) were deleted rather than kept as unused/parallel code.
  - The CLI's typed-`CONFIRM` gate doesn't translate to a browser one-to-
    one; per explicit choice, the browser flow is a single-click "Place
    order" button after a review screen, not a re-typed confirmation
    phrase. To keep this safe despite the lower client-side friction, the
    **server is the actual gate, not the button**: `POST
    /api/orders/place` re-evaluates the strategy and re-runs
    `run_pre_trade_checks` itself and refuses (400) if they fail,
    regardless of what the client sends — a disabled button alone would be
    trivially bypassable. `GET /api/strategies/{name}` returns the same
    `pre_trade_checks`/`can_place` fields so the button reflects reality
    rather than guessing.
  - Test `test_place_order_blocked_when_pre_trade_checks_fail` is the
    load-bearing safety test: asserts zero orders reach the broker when
    checks fail, not just that the HTTP call returns an error.
  - Centralized the placeholder margin/risk-limit constants (previously
    duplicated in `scripts/pre_trade_check.py`) into
    `risk/pre_trade_check.py` as `DEFAULT_*` constants, shared by the CLI
    script and the API.
  - Checkpoint: backend verified against real live Fyers data (`GET
    /api/strategies/iron_condor` returned correct legs/checks/`can_place`).
    The `POST /api/orders/place` route was confirmed registered but
    deliberately **not** invoked against the real account — placing a real
    order is the user's click to make, not something to trigger via curl
    on their behalf.

- [x] **Phase 10 — Basket/strategy tracking (in progress)**
  - Motivation: the user has a real live "45 DTE" multi-leg strategy and
    wants the dashboard to show it as one grouped thing (combined payoff,
    combined Greeks) rather than a flat position list - the "grouping of
    my strategy" ask, inspired by a Stitch-generated institutional-terminal
    concept design the user shared (screens + `DESIGN.md` design tokens
    checked into `stitch_options_trading_desk_analytics/` for reference).
  - Rejected approach: auto-grouping live broker positions by
    underlying+expiry. The user caught the flaw themselves - a calendar
    spread has legs at *different* expiries, so an expiry-matching
    heuristic would split its two legs into separate groups.
  - Adopted approach instead: a **basket** is a first-class thing *we*
    track in our own storage (`storage/basket_repo.py`), not inferred from
    live broker state. It holds every leg ever part of it - including ones
    since closed - with each leg's entry (price, time) and, once closed,
    its exit (price, time). This is the point: a strategy's payoff should
    reflect P&L already banked from a leg you've exited (e.g. an early
    adjustment), not just a fresh `analyze()` over whatever's still open.
  - [x] `analytics/payoff.analyze()` gained an optional `realized_offset`
    parameter (default `0.0`, so all prior behavior is unchanged) - shifts
    max profit/loss/breakevens by a constant, letting a basket's payoff
    equal its open legs' curve plus closed legs' banked P&L. `payoff_at()`
    applies the same shift. Tested including a deliberately-chosen edge
    case (an exact-cancellation coincidental double-root at the S=0
    boundary) that the first draft of the test got wrong before the math
    was double-checked by hand.
  - [x] `storage/basket_repo.py` — `Basket`/`BasketLeg`/`NewBasketLeg` +
    `create_basket`/`get_basket`/`list_baskets`/`close_leg`. Depends only
    on `broker.models` (like every other `storage/` module) to stay at the
    bottom of the dependency direction.
  - [x] `execution/basket_status.py` — `get_basket_payoff()`: converts a
    basket's open legs to `analytics.payoff.Leg`s, sums closed legs'
    realized P&L (`leg_realized_pnl`) as the `realized_offset`, and calls
    `analyze()`. A fully-closed basket (no open legs) can't call `analyze()`
    (it requires at least one leg) so is special-cased to report the flat
    realized total.
  - [x] API: `POST /api/orders/place` now also creates a basket from the
    legs it just placed (real entry prices, real order); `POST
    /api/baskets` creates one manually (for retroactively grouping
    positions that predate this system); `GET /api/baskets`,
    `GET /api/baskets/{id}`, `POST /api/baskets/{id}/legs/{leg_id}/close`.
  - [x] Checkpoint: created a basket from the user's real 4 open October
    positions via `POST /api/baskets` (combined payoff: max profit
    11,927.50, max loss -14,072.50, breakevens 22,716.5/23,983.5 - all
    correct for that 4-leg structure), then closed one real leg via
    `POST .../close` and confirmed the realized P&L matched that leg's
    already-known unrealized P&L exactly (932.75), and that the remaining
    3 legs correctly became unbounded upside (no short call left to cap
    it) - `max_profit` flipped to `null`.
  - [ ] Not yet built: frontend basket list/detail UI, "create basket from
    current positions" flow, "close leg" UI action. Also still pending
    from the same conversation: pinned+searchable underlyings (via a
    downloaded Fyers symbol master file), strategy preset pills, the
    enhanced strategy-builder view (risk profile, net credit/debit,
    strategy-level SL), India VIX display, OI distribution + PCR, and the
    straddle-by-tenor matrix.

**Deferred:** multi-broker adapters, backtesting engine, full automation
beyond the single-click dashboard confirm, and (per the Stitch design
discussion) anything requiring data we don't have - GEX with dealer
positioning, Vanna/Charm/Volga, automated hedge execution, FIX/smart order
routing. Phase 5's original CLI live-order checkpoint is moot now that
order placement moved to the dashboard (Phase 9).
