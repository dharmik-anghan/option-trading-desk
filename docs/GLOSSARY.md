# Glossary — key numbers this system computes

Filled in as each concept is implemented.

- **DTE** — Days to expiry.
- **Delta / Gamma / Theta / Vega** — option Greeks. Sourced directly from
  Fyers' option chain response (`greeks=1` param) per strike as of Phase 1 —
  see `broker/models.Greeks` and `broker/fyers.parse_option_chain`.
  Per-strategy/portfolio aggregation and a self-computed fallback (for
  brokers that don't supply Greeks) land in Phase 2.
- **IV** — Implied volatility, sourced from the broker's option chain where
  available (Fyers provides it alongside Greeks).
- **POP** — Probability of profit, approximated from delta/IV (Phase 2).
- **Max profit / max loss / breakeven** — derived from a strategy's payoff
  diagram at expiry (Phase 2, since no broker computes this for multi-leg
  strategies).
