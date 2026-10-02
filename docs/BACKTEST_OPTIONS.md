# Plan — backtesting option structures

Options are not a third instrument behind the `Market` seam. Equities were: one
file, integer shares, a different charge stack, and the engine untouched. Options
break three of the engine's load-bearing assumptions at once, and the honest
answer is a **sibling engine** that shares the discipline rather than a widened
one that shares the loop.

More importantly: **this is a data problem first and an engine problem second.**
There is a half-day spike at the top of this plan whose answer decides whether
the rest of it is built now or in nine months. Do not skip it, and do not build
past it.

---

## Step 0 — the feasibility spike, before anything else

### What the repo already has, and it is more than it looks

- **`broker/fyers.get_history(symbol, resolution, from, to)`** takes *any* Fyers
  symbol. Nothing restricts it to indices. Per-contract OHLC for
  `NSE:NIFTY25O0925000CE` is one call away.
- **`broker/symbols.expiry_infix()`** already *constructs* the expiry fragment
  from a date plus a weekly/monthly flag, and it was written against the real
  convention after guessing failed — including the reason October cannot be
  "10". Contract-symbol synthesis is therefore solved: underlying + expiry +
  strike + CE/PE → a fetchable symbol.
- **`marketdata.VenueBars`** already wraps `get_history` as a `BarSource`, and
  `BarStore` is keyed by `(source, symbol, interval)`. An option contract is
  just another symbol in a store that already holds thousands.
- **`storage/vol_repo`** has been accumulating one real ATM straddle reading per
  underlying per day, unattended, via `vol_recorder`. That is genuine history.
- **`storage/option_chain_repo`** holds full snapshots with **bid and ask** —
  the only place in the system where the spread is recorded at all.

So the pipeline is: **synthesise contract symbols → fetch per-contract bars →
store under source `fyers` → read them like any other series.** Almost nothing
new is needed to *get* the data. The question is whether the data is there.

### The one thing to find out

**Does Fyers serve historical candles for an expired option contract, and how
far back?**

Take a contract that expired three months ago and one that expired a year ago,
construct their symbols with `expiry_infix`, and ask for daily and 5-minute
bars. Also establish the per-request depth cap for intraday, since weeklies will
need many small requests.

Three possible answers and three different projects:

| Answer | What to build |
| --- | --- |
| **Expired contracts served, with useful depth** | The whole plan below, now. |
| **Only live/near-dated contracts** | Build the **recorder** first, not the engine. A chain snapshotter on the `vol_recorder` pattern accumulates real history forward from today. A usable backtest arrives in 6–12 months. Unwelcome, but it is the truth, and it changes what to write this month. |
| **Nothing usable** | Same as above, plus source the history elsewhere. |

There is a fourth path people reach for and it should be named in order to be
refused: **synthetic pricing from Black-Scholes.** `analytics/black_scholes.py`
and `payoff.theoretical_curve` would make it easy. It is worthless for the
question being asked. The entire edge in option selling is the gap between
implied and realised volatility, so a backtest that *supplies* the implied vol is
a backtest of your own assumption with the answer written into the input. It is
legitimate for sanity-checking how a structure behaves as spot and time move —
and only that. Say so in the code if it is ever added.

---

## Why a sibling engine and not the existing one

`engine.py` says of itself that one position at a time is "a real restriction and
a deliberate one". Three assumptions break, not one:

**1. A structure is not a position.** `Position` carries one `side`, one
`quantity`, one `entry`. A strangle is two contracts with two price series and
opposite signs; an iron condor is four. `_ended_during` tests a stop against
`bar.low`/`bar.high` of a single series — a structure has no single series, and
synthesising one would throw away exactly the per-leg detail that makes an
options result readable.

**2. The instrument changes identity as the run proceeds.** An equity backtest
walks one symbol for three years. An options backtest *rolls*: this week's
25000CE stops existing and next week the same rule trades 25200CE. The traded
thing is a **selection rule** — ATM, 16-delta, 200 points out — resolved fresh at
each entry against the chain that existed at that moment. `run(bars, rule,
market)` takes one bar list and one market; that signature cannot express it.

**3. Expiry is an exit, and settlement is not the last traded price.** `Exit` has
`END_OF_DATA` but no `EXPIRY`, though `market.py`'s docstring already anticipates
an instrument that "stops existing on a date". A contract settles at **intrinsic
value against the exchange's settlement price**, not at its own last trade —
which for a deep OTM weekly is noise, often a stale print from hours earlier.

What the sibling engine inherits unchanged, because these are the parts worth
keeping: decide on a close and fill on the next bar; never offer an unclosed bar;
assume the worse of two ambiguous outcomes within one bar; charge costs as they
are incurred rather than netting at the end; count what could not be traded
instead of quietly dropping it.

---

## The structure of `backtest/options/`

### `contracts.py`

`Contract(underlying, expiry, strike, option_type, lot_size)`, symbol synthesis
on top of `broker/symbols.py`, and — the part that will be forgotten otherwise —
a **dated lot-size table**.

There is no lot size anywhere in this repo today. NIFTY has been 75, then 50,
then 25, then 75 again; BANKNIFTY 25, 15, 35. Each change has a date. Using
today's lot size across three years mis-sizes **every single trade** in the run,
and it does so silently because the arithmetic all works. Dated table, and a
refusal rather than a default when a date is not covered.

### `chain.py` — the `View` of this engine

`ChainAt(ts)`: which strikes were quoted at that instant, pivoted out of stored
per-contract bars. This is the options analogue of `view.Frame` and it carries
the same absolute rule — **only closed bars, never a price from later.** Every
argument in `view.py`'s docstring about the hourly-bar lookahead applies here
identically, and the failure mode is the same beautiful meaningless curve.

One addition with no equivalent on the equity side: a strike that stopped being
quoted. Deep wings come back with zero price and zero IV, and
`strategies/selection.is_quoted` already knows that a zero-priced option is "no
quote", not "a free option". That check must be enforced in history too, or the
backtest will happily sell an option for ₹0 and report the margin as pure profit.

### `structure.py`

A `Structure` is a list of `LegSpec` — *selection rule* plus side plus lots — not
a list of strikes. Resolved into concrete legs at entry against `ChainAt`.
Reuses `analytics.payoff.Leg` and `analyze()`, which already handle multi-leg
payoff, unbounded max loss as `-inf`, and breakevens.

**A snag worth knowing before it bites:** `strategies/selection.select_by_delta`
cannot be reused as-is. It requires broker-supplied Greeks, and historical
per-contract bars have none. Delta at a past instant has to be *computed* — solve
implied vol from the contract's own traded price, then `black_scholes.greeks`.
That is real work and it is circular enough to be worth deferring.

So: selection **by distance from spot** and **by premium** are first-class in v1,
because both work from price alone. Delta selection lands after an IV solver, and
`select_by_delta` stays the live desk's path until then.

### `market.py` — `OptionMarket`

**The charge base is premium, not notional.** This is the single easiest way to
get an options backtest wrong by two orders of magnitude. A NIFTY 25000CE at ₹80
with lot size 75 has a notional of ₹18.75 lakh and a premium of ₹6,000. Charges
are on the ₹6,000.

- STT: 0.0625% of premium, **sell side only**
- Exchange transaction charge: ~0.0495% of premium — far heavier than equity's
  0.00297%, and on premium rather than turnover
- stamp duty on buy, SEBI turnover fee, GST on (brokerage + exchange + SEBI)
- brokerage: flat per order, so a four-leg condor pays four tickets in and four
  out — eight flat charges against one credit

**The ITM expiry trap, which is a real and expensive one:** an option allowed to
expire in the money is *exercised*, and STT on exercise is **0.125% of
intrinsic value** — on the notional-ish intrinsic, not on the premium. A ₹50
credit on a strike that expires ₹200 in the money pays STT on the ₹200×75, which
can exceed the entire loss the payoff diagram predicted. A backtest that settles
ITM options at intrinsic without this charge understates the worst trades
precisely where they matter. It goes in from day one, not as a refinement.

### `margin.py` — the real capital constraint

`risk/margin.py` says it outright: *"no pre-trade SPAN/exposure margin
calculator, so an accurate per-strategy margin requirement isn't available yet
from this broker."* That gap becomes load-bearing here.

For short options, **margin is the capital, not the premium.** A NIFTY short
strangle blocks roughly ₹1.2–1.5 lakh per lot. A backtest that sizes against
premium collected, or against notional, will report a return on capital the
account never had — inflated by ten times or more, which is not a rounding error
but the difference between a strategy and a fantasy.

v1: approximate SPAN+ELM as a percentage of notional with a floor, calibrated
against the broker's own margin calculator on a handful of real structures,
and **labelled an approximation in every result that uses it.** Benefit of the
existing design: a long-only debit structure needs no margin model at all — the
premium paid *is* the risk — so debit spreads and long options can be backtested
honestly before any of this exists. Worth sequencing around.

### `engine.py`

Per timestamp: mark every open leg → test structure-level exits → handle expiry
settlement → handle rolls → let the rule decide on the close.

Exits options need that have no equivalent today:

- **`Exit.EXPIRY`** — settle at intrinsic against the settlement price
- **stop on net structure P&L**, e.g. "close at 2× the credit received". This is
  *the* standard premium-selling stop and it is currently inexpressible: the
  engine's stop is a price on one series.
- **target as a share of maximum profit**, e.g. "close at 50% of credit
  captured" — the most common real rule in premium selling, likewise
  inexpressible today
- **stop on the underlying's level** rather than on the structure's value —
  a different rule with different behaviour, and both are wanted
- **roll**, on a schedule or a trigger

Deliberately out of v1, and said out loud rather than left as an empty hook:
delta-hedging and adjustment. `strategies/base.py` already makes this call for
live trading, for the same reason, and the plan should stay consistent with it.

### Concurrency

Options desks run overlapping structures — this week's strangle beside next
week's. One structure at a time is more limiting here than it was for equities.

v1 keeps one, because interpretability is worth more than realism at the start
and it matches the existing discipline. Concurrency then comes from the
portfolio machinery in the equity plan's step 5 rather than from a second
mechanism — `pooled` mode with a shared margin pool is very nearly the right
abstraction already.

### `metrics.py`

Reuse `backtest/metrics.py` wholesale for the curve figures. Add what premium
selling specifically requires, and one of these matters more than all the others:

- premium captured against premium paid; credit received against max loss risked
- days held against DTE at entry
- P&L by DTE-at-entry bucket, and by moneyness at entry
- maximum adverse excursion relative to the credit received — how close the
  trade came to the stop before it worked
- **the tail.** A short strangle's Sharpe is beautiful and its 99th-percentile
  loss is what closes the account. Report the worst N trades as a share of total
  P&L, and the ratio of median trade to mean trade. A strategy where three
  trades out of four hundred consume the entire profit is a strategy the
  existing figures would describe as excellent. `metrics.py` already carries
  `buy_and_hold` and `cost_share` for exactly this reason — figures that exist to
  stop self-deception rather than to flatter. This is the options one.

---

## The spread, and why LTP-only backtests of options are fiction

Per-contract `get_history` gives OHLC. No bid, no ask. A NIFTY weekly OTM option
quotes 4.50 / 5.00 — a 10% spread. A structure collecting ₹50 of premium across
two legs, entered and exited, crosses four spreads; at ₹1 each that is ₹4 against
₹50, or 8% of the gross, before a single statutory charge. Backtesting at the
last traded price does not model this at all, and premium-selling results are
*dominated* by it.

But the spread is recoverable, and from data already being collected:
`option_chain_repo` snapshots carry `bid` and `ask`. Fit a spread model from
them — as basis points of premium with an absolute tick floor, widening with
distance from the money and tightening as DTE falls — and charge it as slippage
the way `Execution.slippage_bps` already does for perpetuals.

That calibration is a genuinely valuable artifact in its own right, it is
derivable from the desk's own recorded history, and it should be built early:
every result before it exists is optimistic by an unknown and probably large
amount.

---

## Shared with the equity plan

Three things from `BACKTEST_EQUITY.md` are prerequisites rather than parallel
work, and doing them once serves both:

- **Session anchoring in `resample.bucket_for`** (equity step 3). Options are
  traded intraday on the same 09:15 session, so the misaligned hourly buckets
  are the same bug with the same fix.
- **Calendars** (equity step 4). These are *more* valuable for options than for
  equities — expiry-day and expiry-week effects are the most-claimed edge in
  Indian derivatives, and this is the machinery that either finds them or shows
  them to be a small sample. The same defences apply and matter more, because the
  claims are louder.
- **`Costs.breakdown`** (equity step 1). The F&O charge stack needs it for
  exactly the same reason.

---

## Sequence

| | | |
| --- | --- | --- |
| **0** | Data feasibility spike | half a day, gates everything |
| **1** | `contracts.py`: symbol synthesis, dated lot sizes, a backfill script | per-contract bars landing in `BarStore` |
| **2** | `chain.py` + the spread model from recorded snapshots | the honest price of a leg at a past instant |
| **3** | `OptionMarket` charges incl. the ITM-exercise STT; `structure.py` with distance- and premium-based selection | first real runs — **debit structures only**, which need no margin model |
| **4** | Margin approximation, calibrated and labelled | short/credit structures become measurable |
| **5** | Structure-level stops and targets, expiry settlement, rolls | the rules people actually trade |
| **6** | Options metrics, tail figures first | results you can trust enough to act on |
| **7** | IV solver → delta-based selection | `select_by_delta` works on history |

Step 3 before step 4 is the useful bit of sequencing: long options and debit
spreads are fully honest without any margin model, because the premium paid *is*
the risk. That is a real, shippable, trustworthy backtest several weeks before
the hard part is solved.

## What to decide before starting

1. **Step 0's answer.** Everything downstream depends on it.
2. **Which underlyings.** NIFTY and BANKNIFTY weeklies alone are a large backfill
   — every strike, every expiry, every week. Scope it deliberately rather than
   discovering the size mid-fetch.
3. **Whether a recorder starts now regardless.** Even if expired-contract history
   turns out to be available, a forward-recording chain snapshotter costs little,
   runs unattended like `vol_recorder` already does, and is the only source that
   will ever give bid/ask history. Its value compounds with every day it has been
   running, which argues for starting it before it is needed rather than after.

## The engine boundary, and a second engine

What the engine depends on, and nothing else, so it can be reimplemented
(Rust, via PyO3) and checked against the one we have:

- **Market: `optbt/source.py` `MarketSource`.** A narrow, asking interface - a
  chain at a minute, a contract's bars for a day, a lot size, the calendar.
  `optbt/data/history.py` answers it from DuckDB; a second engine answers the
  same questions from the same store. Not a preloaded market: a run reads a
  full chain only when it enters, then only the contracts it holds, and
  preloading four years of weekly windows cost more (18s + 6s for lots) than
  a whole run (17s).
- **Strategy: `optbt/spec.py`.** A `LegsConfig` as versioned JSON - the API's
  request shape - so the request, a saved run and another engine read one
  format.
- **Parity: `tests/optbt/parity/`.** A deterministic synthetic market (rallies,
  a gap, a holiday expiry, a dark stretch, an illiquid strike, VIX), a set of
  spec cases, and the exact results the Python engine produces for them. Any
  engine must reproduce them. `python -m tests.optbt.parity.run --parquet DIR`
  writes the market as Parquet for an engine that reads files; `--write`
  re-records after a deliberate behaviour change, and the diff is the change.

The Python engine stays as the reference: new strategy behaviour lands there
first, with its parity cases, and a second engine follows.
