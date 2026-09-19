# Glossary — key numbers this system computes

Filled in as each concept is implemented (mostly Phase 2+). Placeholder now
so the doc structure exists from Phase 0.

- **DTE** — Days to expiry.
- **Delta / Gamma / Theta / Vega** — option Greeks; computed per-leg and
  aggregated across a strategy/portfolio (Phase 2).
- **IV** — Implied volatility, sourced from the broker's option chain where
  available.
- **POP** — Probability of profit, approximated from delta/IV.
- **Max profit / max loss / breakeven** — derived from a strategy's payoff
  diagram at expiry.
