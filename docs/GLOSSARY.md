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
  outcomes (e.g. a naked short strangle's max loss) are represented as
  `+/-math.inf` rather than a large number, so callers can't mistake an
  unbounded risk for a capped one.
