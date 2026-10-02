# Architecture

A personal, semi-automated options trading desk. Fyers is the first broker;
the design keeps broker-specific code behind one seam so other brokers
(Zerodha, Upstox, ...) can be added later without touching strategy or risk
logic.

## Layers

```
api/          HTTP: routers, request/response shapes, dependency wiring
jobs/         background loops the app starts: alerts, recorders, daily bars
execution/    the only code that places, closes or protects a position
strategies/ backtest/ optbt/ alerting/ risk/ analytics/   the domain
marketdata/   bar store and bar sources        storage/   SQLite repositories
broker/       capability protocols, per-venue adapter packages, the factory
venues/       the catalogue: what trades where, its calendar. No I/O
paths.py settings.py   where files live; credentials
```

Dependencies point down this list and never back up: there is no package
import cycle, and adding one should be treated as a bug. `venues/` imports
nothing else in the repo, so importing the catalogue never needs a key.

## Adding a venue

A venue is a template with three parts, and nothing above `broker/` names one:

1. An adapter package, `broker/<venue>/` - the broker implementing the
   protocols it can (see below), its auth, and for an options venue a
   `ContractCodec` (`broker/contracts.py`) that reads its contract symbols.
2. A `VenueSpec` in `venues/registry.py` - asset class, currency, calendar,
   capabilities - and its instruments in `venues/instruments.py`.
3. A line in `broker/factory.py`'s `FACTORIES`: how to build it, whether reads
   are cached, its codec, whether settings configure it, its tick stream.

Everything else is shared. The options endpoints take `?venue=` and default to
the venue serving index options; the app registers bar sources and starts tick
streams by looping over the catalogue. `tests/api/test_venues.py` fails when a
catalogue entry has no factory, or an options venue has no codec.

A data source with no account (Yahoo, Binance) is a bar source in
`marketdata/`, raising `marketdata/errors.py`; a source of option history
implements `optbt/data/source.py`'s `ExpiredSource`.

## The broker contract, split by capability

`broker/base.py` defines several `Protocol`s rather than one, because venues
are not the same shape. An option chain is meaningless on a perpetual futures
venue; leverage, funding and a liquidation price are meaningless on an options
one. One fat protocol forces every adapter to implement both and raise for
half, which moves "can this venue do that?" out of the type system and into a
runtime `NotImplementedError`.

- `MarketData` — quotes and candles. Every venue has this.
- `Trading` — funds, positions, placing orders.
- `OptionsData` — option chains. Options venues only.
- `Streaming` — pushed updates.
- `Broker` — `MarketData` + `Trading`, the core any venue provides.
- `OptionsBroker` — what the index options desk needs.

Callers ask for the narrowest protocol they need, so a function that reads
prices works on any venue and one that reads a chain will not typecheck
against a venue that has none. Contract tests in
`tests/broker/test_contract.py` run against *any* implementation — including
the `FakeBroker` test double — so a new adapter is verified against the same
behaviour Fyers is before it is wired into anything.

A venue declares its capabilities as data in `venues/registry.py` so the API
can report them without building an adapter (which would need a credential).
`tests/venues/test_registry.py` asserts each declaration against the protocols
the adapter actually satisfies, so the claim cannot quietly rot into a lie.

## The API is routers, not one module

`api/app.py` is composition only: the app, middleware, the broker error
handler, the routers, the background jobs and the built frontend. Endpoints
live in `api/routers/`, one module per area, and hold request validation and
response mapping - the work is in `execution/`, `backtest/service.py`, the
repositories and the domain packages. Shared between routers: `api/deps.py`
(providers and annotations - override these in tests), `api/pricing.py`
(marks and payoff curves), `api/basket_view.py` (a basket as the API shows
it, also read by the alert pass), `api/charting.py` (candles and lines on the
wire), and `api/store.py` (opening the database, schema included). No module
imports a router.

## Schema changes

`storage/migrations.py` holds an append-only list of numbered steps, with the
current version in SQLite's own `user_version` pragma. `init_schema` creates
anything missing and then applies whatever is pending, so every entry point
that opens the database gets a current schema. A step that raises leaves the
version where it was, so it is retried rather than skipped.

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
- `broker/fyers/adapter.py` — `FyersBroker`; parsing logic (`parse_quotes`,
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
- `execution/manager.py` (**removed 2026-10**, with the order and strategy
  review endpoints, once nothing called them) — `ExecutionManager`: the *only* code allowed to
  call `Broker.place_order`, converting `Leg`s to `OrderRequest`s
- `execution/confirm.py` — `confirm_and_place`: the human-in-the-loop gate.
  Requires the exact string `CONFIRM`; never even prompts if Phase 4's
  pre-trade checks failed. The `confirm` callable is injected so this whole
  flow is unit-testable without a real terminal.
  **Removed in Phase 9** along with `scripts/place_strategy_order.py` below,
  once order placement moved into the dashboard — see that section.
- `scripts/place_strategy_order.py` — the end-to-end CLI: fetch chain, build
  strategy, run pre-trade checks, confirm, place, reconcile against
  `get_positions()`. **Removed in Phase 9.**
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

React + Vite + TypeScript frontend, FastAPI backend. Originally read-only
(order placement in the CLI's `CONFIRM` flow); per explicit later decision
(Phase 9), order placement moved into the dashboard and the CLI
confirm-and-place flow (`scripts/place_strategy_order.py`,
`execution/confirm.py`) was deleted rather than kept as parallel dead code.

```
frontend/  (React + Vite + TS - fetches from / posts to the API below)
     |
api/        (FastAPI: app.py composes, routers/ holds the endpoints)
     |
(same broker/analytics/risk/strategies/storage/execution stack as everything else)
```

- `api/deps.py` — `get_broker()`/`get_db_path()` as FastAPI
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

### Order placement safety model (Phase 9)

The CLI's typed-`CONFIRM` gate doesn't translate one-to-one to a browser;
the dashboard uses a single-click "Place order" button on a review screen
instead (explicit choice — less friction than the CLI). Because a
disabled-button-only gate is trivially bypassable (anyone can call the API
directly), **the server is the real gate** (this flow was **removed in
2026-10**; `execution/perps.py` follows the same rule for perpetuals): `POST /api/orders/place`
re-evaluates the strategy and re-runs `risk.pre_trade_check.run_pre_trade_checks`
itself, and refuses with `400` if any check fails, regardless of what the
client sends. `GET /api/strategies/{name}` returns the same
`pre_trade_checks`/`can_place` fields so the button reflects what the
server will actually enforce, not a guess. The load-bearing test is
`test_place_order_blocked_when_pre_trade_checks_fail`, which asserts zero
orders reach the broker when checks fail — not just that the HTTP call
errors.

`_evaluate()` in `api/app.py` is shared between the preview (`GET`) and
placement (`POST`) endpoints so they can never drift apart on what counts
as passing.

## Baskets: strategy tracking (Phase 10)

A basket is how "this group of legs is one strategy" gets represented -
tracked in our own storage, not inferred from live broker positions
(auto-grouping by underlying+expiry breaks for a calendar spread, whose two
legs sit at *different* expiries by definition).

```
storage/basket_repo.py    (Basket, BasketLeg, NewBasketLeg - persistence only,
                            depends only on broker.models like every storage/ module)
     |
execution/basket_status.py  (get_basket_payoff: open legs -> analyze(),
                              closed legs -> summed realized P&L as the offset)
     |
api/app.py  (POST /api/orders/place creates one automatically;
             POST /api/baskets creates one manually;
             GET /api/baskets, GET /api/baskets/{id},
             POST /api/baskets/{id}/legs/{leg_id}/close)
```

A basket keeps every leg ever part of it, including closed ones, so its
payoff is: current open legs' theoretical curve **plus** P&L already banked
from legs you've since exited. `analytics.payoff.analyze()` gained a
`realized_offset` parameter (default `0.0`, backward compatible) for
exactly this - it shifts max profit/max loss/breakevens by a constant
without needing a second, parallel payoff calculation.

Closing a leg (`close_leg`) only updates our own record - it does not touch
the broker. If you close a position in your broker's app directly instead
of through this dashboard, the basket won't know until you tell it (via the
close-leg action) - there's no reconciliation against live broker state
(yet).

Frontend: `frontend/src/components/BasketsPanel.tsx` lists baskets,
expandable to legs + payoff chart; "+ Create basket from current positions"
parses strike/option_type from the Fyers symbol format client-side to
retroactively group positions from before this system existed.

## Payoff chart

`analytics/payoff.payoff_curve_points(result)` returns the exact vertices
needed to draw a piecewise-linear payoff chart: each leg's strike, each
breakeven, and padded domain edges beyond the outermost one. Breakevens are
included as vertices specifically so a renderer never needs to guess where
a segment's sign changes - each segment between consecutive points is
guaranteed single-sign, which is what lets
`frontend/src/components/PayoffChart.tsx` fill profit/loss areas per
segment without a clipPath. Both `GET /api/strategies/{name}` and the
basket endpoints expose this as `payoff_curve`; the underlying math is
computed once, server-side - the frontend never re-derives payoff numbers.

## TOTP auto-login

`broker/fyers/auth.py` replicates Fyers' manual login flow (OTP -> TOTP
verify -> PIN verify -> auth code -> token exchange) against undocumented
endpoints, so the daily token refresh doesn't need a browser. Ported and
re-audited from the `multi-broker-sdk` PyPI package rather than taken on as
a dependency (see `docs/PHASES.md` Phase 8 for the reasoning) — same
technique, but as code we own and can read, for a flow that handles a TOTP
secret and PIN.

`scripts/fyers_login.py` tries this first when
`FYERS_USERNAME`/`FYERS_TOTP_KEY`/`FYERS_PIN` are all set, and falls back to
the manual OAuth flow (`fyersModel.SessionModel`) on any `AutoLoginError` —
that fallback matters because these are undocumented endpoints Fyers could
change without notice, unlike the official OAuth flow.

### Token lifetime

`broker/fyers/token_store.py` owns the token, and nothing else reads
`FYERS_ACCESS_TOKEN` directly. Callers ask for `get_access_token(settings)`,
which returns the cached token from `.env` while it has time left and
otherwise re-runs the auto-login above and persists the new one (to `.env`
and to `os.environ`, since real environment variables outrank `.env` in
pydantic-settings).

This exists because Fyers tokens have no refresh grant and expire at 06:00
IST the morning after they are issued — not 24 hours after. Before this,
`scripts/fyers_login.py` was the only thing that ever minted a token and
nothing scheduled it, so the day after a login every call returned
`-16 Could not authenticate the user`. Expiry is checked per request (API
dependencies build the broker per request), with a 15-minute skew so a
request starting just under the wire doesn't land past it, and refreshes are
serialised on a lock so a burst after expiry triggers one login rather than
one each.

Refresh needs the three auto-login secrets. Without them there is no
non-interactive path, so `get_access_token` raises `TokenRefreshError`
naming the script to run by hand rather than failing deep inside a broker
call.

## Status

See `docs/PHASES.md` for what's built vs. planned.
