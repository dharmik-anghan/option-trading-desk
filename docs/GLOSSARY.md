# Glossary — key numbers this system computes

Filled in as each concept is implemented.

- **DTE** — Days to expiry.
- **Delta / Gamma / Theta / Vega** — option Greeks. Sourced directly from
  Fyers' option chain response (`greeks=1` param) per strike — see
  `broker/models.Greeks` and `broker/fyers.parse_option_chain`. A
  self-computed Black-Scholes fallback exists in
  `analytics/black_scholes.greeks()` for brokers that don't supply Greeks.
  Portfolio-level aggregation across legs/strategies lands in Phase 4.
- **IV** — Implied volatility, sourced from the broker's option chain where
  available (Fyers provides it alongside Greeks).
- **POP** — Probability of profit, approximated from delta/IV (not yet
  implemented — lands with the strategy framework in Phase 3).
- **Max profit / max loss / breakeven** — computed in
  `analytics/payoff.analyze()` from each leg's intrinsic value at expiry,
  since no broker computes this for a multi-leg combination. Unbounded
  outcomes are represented as `+/-math.inf` rather than a large number, so
  callers can't mistake an unbounded risk for a capped one. Note: a naked
  short *call* has genuinely unbounded max loss (no ceiling on the
  underlying); a naked short *put*'s max loss is large but finite, bounded
  at the underlying price hitting 0 — don't conflate the two.
- **Required margin** — **known gap**: Fyers' Python SDK (`fyers-apiv3`)
  exposes account funds (`Broker.get_funds`) but no pre-trade SPAN/exposure
  margin calculator. `risk/margin.check_sufficient_margin` takes
  `required_margin` as a caller-supplied number, not something computed from
  a real margin API. Needs either a manual estimate, Fyers' margin
  calculator via a different (undocumented-in-SDK) endpoint, or a
  multi-broker margin API before Phase 5's confirm screen can show a real
  figure — currently a placeholder (see `scripts/pre_trade_check.py`).
- **Portfolio Delta/Gamma/Theta/Vega** — aggregated in
  `risk/portfolio_greeks.aggregate_portfolio_greeks()` across a strategy's
  legs. A bought leg contributes its own Greeks x quantity; a sold leg
  contributes the negative (you're short that exposure — e.g. a sold
  option's theta becomes a *positive* contribution, since time decay works
  in your favor when short).
- **Realized / Unrealized / Total P&L** — `execution/portfolio_status.get_portfolio_status()`.
  Realized comes from Fyers' funds response ("Realized Profit and Loss" —
  `Funds.realized_pnl`); unrealized is the sum of `unrealized_pnl` across
  all open positions from `Broker.get_positions()`. Total is the sum of the
  two, and is what `risk.limits.check_daily_kill_switch` acts on. History
  is persisted via `storage/portfolio_repo.py`.
- **Order type** — `execution/manager.ExecutionManager` always places
  `MARKET` orders (immediate execution at the best available price), not
  `LIMIT`. Simplest choice for the Phase 5 checkpoint; means each leg of a
  multi-leg strategy can fill at a slightly different price than the
  chain snapshot showed (slippage), and legs aren't filled atomically as a
  single combo order. Revisit if slippage across legs turns out to matter
  in practice.
