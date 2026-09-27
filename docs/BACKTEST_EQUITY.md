# Plan — the backtest module for Indian equities

The engine was built for a perpetual and validated against one. Nothing in the
loop knows that, which was the point: `Market` is the seam where an instrument's
money lives. This plan uses that seam rather than widening the engine, and adds
four things the current module cannot express at all — what a candle *looks*
like, what volume did, which calendar days paid, and more than one symbol at
once.

Five steps, each shippable on its own. The order is not arbitrary; the rationale
is at the end.

---

## 1. An equity market, and the Indian charge stack

**Answer to "do we need separation":** yes, but only one file of it. The `Market`
protocol already carries `opening` and `side` on `fee()` precisely because
"securities transaction tax falls on one leg only" was anticipated. Nothing in
`engine.py`, `spec.py`, `rules.py` or `metrics.py` needs to change for equities
except where costs are reported.

### `backtest/equity.py` — `EquityMarket`

| Protocol member | Perpetual | Equity |
| --- | --- | --- |
| `round_quantity` | `floor` to 3 dp | `floor` to whole shares |
| `min_quantity` | 0.001 | 1 |
| `multiplier` | 1.0 | 1.0 |
| `carry` | funding, both signs | 0 for CNC long; MTF interest or borrow when short/leveraged |
| `liquidation` | price-based, real | `None` — see below |
| `margin` | notional / leverage | notional / broker's MIS multiple |

`liquidation()` returns `None` and that is the honest answer, not a shortcut. A
broker's intraday square-off is a *clock*, not a price: MIS is flattened around
15:20 IST whatever the position is worth. Modelling it as a price level would
invent liquidations that never happened and miss the one that always does. The
clock is already expressible — `sessions` with an end of 15:20 plus
`close_outside_session: true`. Document that pairing as the way to model MIS.

### `product: "MIS" | "CNC"` is part of the market, not a footnote

It changes five things at once, so it has to be one field rather than five:
STT rate and which legs it falls on, stamp duty, leverage available, whether a
short is possible at all, and whether the position can survive the close.

A CNC market cannot be sold short. `StrategySpec.check()` should refuse a spec
with `short_entry` against a CNC market rather than run it — a backtest that
happily shorts delivery stock is describing trades no broker would accept, which
is the same class of lie `skipped_too_small` exists to prevent.

### `Charges` — dated, configured, never baked in

A frozen dataclass holding the whole stack, with an `as_of` date on it, because
every one of these numbers has changed in the last two years and a result
computed under last year's rates has to be able to say so:

- brokerage: flat-or-percent-whichever-lower, per executed order
- STT: rate, and which legs (intraday sell-only; delivery both)
- exchange transaction charge, both legs
- SEBI turnover fee, both legs
- stamp duty, buy leg only
- GST on (brokerage + exchange + SEBI)
- DP charge: flat, per scrip, on a delivery sell
- IPFT

The defaults go in with a comment naming the date they were taken from and the
broker they belong to. Treat them the way `PerpetualMarket` treats Shark's
schedule: written down, attributed, overridable per run.

### The thing that will surprise the result: flat brokerage

Every cost in the engine today scales with notional. A flat ₹20 per order does
not, and the interaction with equal-capital-across-N-symbols (step 5) is severe:
split ₹1,00,000 twenty ways and each ₹5,000 sleeve pays ~₹40 of brokerage on a
round trip before a single charge that scales — about 0.8%, which is larger than
most intraday edges. A rule that looks fine on one symbol with full capital can
be uniformly unprofitable the moment the capital is divided, for reasons that
have nothing to do with the rule.

So this step is not finished until the result *says* that. Two additions:

- `Costs` gains `breakdown: Mapping[str, float]` (brokerage, stt, exchange,
  sebi, stamp, gst, dp) while `fees` stays the total, so `metrics.measure` and
  the API keep working untouched.
- `Metrics` gains cost per trade in rupees and in basis points of notional, and
  a caveat when the flat component exceeds some share of the average gross —
  the message being "this rule is paying per ticket, not per rupee; trade
  bigger or trade less".

### Tests

- 0.4 shares rounds to 0, not 1; ₹10,000 on a ₹3,000 share buys 3
- the same two prices cost differently long vs short, by exactly the sell-leg STT
- the same percentage move nets less on a small notional than a large one
- a CNC spec with a short entry is refused by `check()`, with a message saying why

---

## 2. Candle anatomy, candle types, and volume

Pure additions. No engine change, no loop change, and nothing here can look
ahead because everything is read through `Frame`.

### Two layers, deliberately

**(a) Numbers.** A new operand kind `candle`, reading one bar's geometry:

    body, range, upper_wick, lower_wick          in price
    body_pct, upper_wick_pct, lower_wick_pct     as % of that bar's range
    range_pct                                    range as % of close
    gap_pct                                      open against the previous close

These compose with every comparison and every arithmetic operator already in
`spec.py` for free, on any timeframe, with `ago`. "Body more than 70% of range
on the hourly" needs no new condition type. Geometry goes in a new
`analytics/candles.py` — pure functions of a `Bar` — so the live desk and the
chart can draw the same classification the rule traded, which is the same reason
`lines.py` exists.

**(b) Names.** A new condition kind `shape`, for the sentences you actually
think in. Each is a *named threshold set*, not a classifier:

| shape | definition |
| --- | --- |
| `full_body` | body ≥ 80% of range, each wick ≤ 10% |
| `doji` | body ≤ 5% of range |
| `indecision` — the "confusing candle" | body ≤ 15%, **or** both wicks ≥ 30% with body ≤ 35% |
| `hammer` / `shooting_star` | one wick ≥ 60%, body ≤ 30%, on the right end |
| `inside` / `outside` | range contained by / containing the previous bar's |
| `engulfing_bull` / `engulfing_bear` | body contains the previous body, opposite colour |

Plus a `direction: bull | bear | either` qualifier, and every threshold
overridable per spec.

The non-negotiable part: `describe()` prints the thresholds it used — *"full
body (body ≥ 80% of range, wicks ≤ 10%)"*, not *"full body"*. A trade log that
says a strategy traded "full body candles" without saying what that meant is
unauditable, and "confusing candle" especially is a judgement, not a fact. The
whole module already works this way; shapes must not be the exception.

Name it `shape`, not `pattern` — there is no intent to ship a
candlestick-pattern library, and the word would promise one.

### Volume

`FIELDS` is `open/high/low/close` and volume is reachable nowhere. Additions:

- A `SERIES_FIELDS = FIELDS + ("volume",)` for the *price* operand only.
  `FIELDS` stays as-is for stops and triggers: a stop at "volume" is nonsense
  and should stay unsayable.
- In `analytics/indicators.py`: `volume_sma`, `relative_volume`
  (volume ÷ its average, ×100), `volume_rank` (percentile — reuse the existing
  `percentile_rank`). Session-anchored VWAP is genuinely wanted for Indian
  intraday but needs step 3's session anchor, so it lands there.

**"Volume greater than the previous day's volume" needs nothing further.** With
`1d` in the spec's context — which `StrategySpec.context` derives on its own —
it is `volume[1d] above volume[1d, ago 1]`. That it falls out of the existing
shape rather than needing a special case is the argument that the shape is
right. And because it goes through `Frame`, the daily volume it compares against
is yesterday's *closed* daily bar, never today's partial one.

### The index-volume trap

NIFTY and BANKNIFTY bars carry no meaningful volume; Fyers returns 0. A volume
condition on an index does not error — it silently never fires, and the run
reports a strategy that took no trades. Add: a `check()`-time warning where
resolvable, and a run caveat when a spec reads volume on a series whose stored
volume is entirely zero.

### Tests

- a hand-built bar with body 90% of range is `full_body` and not `indecision`
- the bar sitting exactly on a threshold resolves one way, and the test states
  which, so the boundary is a decision rather than an accident
- `volume[1d] > volume[1d, ago 1]` fires on the correct base bars and never
  before the daily bar has closed — the lookahead test, in `test_view.py`'s style
- an all-zero-volume series produces the caveat

---

## 3. Session alignment, and the equity data traps

This is the correctness step, and it is the one most likely to make results
disagree with what you see on any Indian chart.

### `bucket_for` is epoch-aligned, and `resample.py` says so itself

> *"An instrument with a session — an Indian index, a stock — needs its day to
> start when its exchange opens, and `bucket_for` is where that belongs when the
> time comes."*

The time has come. Current behaviour against NSE's 09:15–15:30 IST
(03:45–10:00 UTC):

- **`1d`** — accidentally correct. The midnight-UTC bucket contains the whole
  IST session. Keep it, and pin it with a test, because it is luck rather than
  design and a change to the session hours would break it silently.
- **`1w`** — correct. Monday-aligned.
- **`1h`, `4h`, `30m`, `15m`** — **wrong.** The session opens at 09:15, so the
  first epoch-hourly bucket holds 45 minutes of trading and is labelled as an
  hour, and every boundary after it sits 15 minutes away from where every
  Indian chart draws it. An hourly trend filter tested this way is reading a
  different hour than the one you looked at.

Fix: an optional `anchor` — session open time plus IANA zone — threaded through
`bucket_for` and `resample`, derived from the market rather than hardcoded. The
existing `Session` type and `PRESETS["india"]` already hold the hours.

`closes_at` needs a second look but is, on inspection, safe: a daily NSE bar
stamped 00:00 UTC is treated as closing at 00:00 the next day rather than at
10:00 UTC when the session actually ended, which makes yesterday's daily
context available *later* than it truly was — 05:30 IST, still before the next
open. Pessimistic, therefore fine. Worth a comment saying so, so nobody
"fixes" it into a lookahead.

### Corporate actions — the highest-risk data problem here

Unadjusted history turns a 1:5 split into an 80% overnight gap down, and a
short-biased rule will find it and report a spectacular, entirely fictional
profit. **Verify first whether Fyers' historical equity bars are
split/bonus-adjusted.** I could not establish that from this repo, and it should
be settled before anything in this step is built on top of it.

Either way, add a detector: an overnight gap beyond a threshold that matches a
plausible ratio (1:2, 1:5, 1:10, common bonus ratios) raises a loud caveat, and
optionally halts the run. Cheap, and it is the difference between a backtest and
a trap.

### Circuit limits and liquidity

- A bar locked at a circuit could not be traded at any price. Without circuit
  band data, at minimum flag a bar with zero range and non-zero volume.
- A fill above `max_participation` × that bar's volume is not a fill. Refuse or
  scale it, and count it — `skipped_illiquid`, alongside the existing
  `skipped_too_small` and `skipped_unaffordable`. This matters far more than it
  sounds once capital is divided across twenty symbols, several of which will
  be thin.

### Tests

- an hourly resample under the NSE anchor puts the first bar at 09:15, not 09:00
- the daily bucket still contains the whole session (the luck, pinned)
- a synthetic 1:5 split raises the caveat
- a signal wanting 10% of a bar's volume is skipped and counted

---

## 4. Calendars — which days actually paid

`backtest/calendars.py`, built from the trade list, bucketed by **entry time in
IST**. A calendar breakdown in UTC for an Indian market is meaningless, and
entry rather than exit because the entry is the decision the calendar is
supposed to be about.

Buckets: weekday · time-of-day in 30-minute slices across the session (the first
and last slices are the ones anybody cares about) · month-of-year · calendar
month · day-of-month · days-to-monthly-expiry, expiry day, expiry week · the
session before and after a market holiday (`feeds/holidays.py` already fetches
these).

Each bucket reports trades, wins, net, and net per trade.

**This is the part of the plan most likely to lose money, so it ships with its
defences built in, not added later.** Slicing an in-sample trade list by
calendar and keeping the good slices is textbook overfitting, and it produces
the most persuasive-looking table in the whole app.

- Trade count printed beside every bucket, always, at equal visual weight to
  the P&L.
- Buckets under a floor (≈20 trades) marked low-confidence in the output itself,
  not in a tooltip.
- A shuffle test: re-bucket the same trades under randomly permuted labels a few
  hundred times and report where the real spread falls in that distribution.
  Crude, cheap, and enough to kill "Tuesdays are great" off six trades.
- A header caveat on the whole panel: these are in-sample slices; filtering on
  them is fitting, not finding.

The genuinely useful follow-on is a calendar *filter* in `StrategySpec`
(`only_weekdays`, `skip_expiry_day`, and the like), so a pattern spotted here can
be re-run over a different window — the difference between discovering something
and admiring it.

---

## 5. More than one symbol

`backtest/portfolio.py`. Two modes, named separately because they give
different answers and a single "portfolio backtest" that quietly picked one
would hide which.

### `sleeves` — default, ship first

N independent `run()` calls, each with `capital / N`, each fully inspectable on
its own. Merge the equity curves on a union time axis, forward-filling each
sleeve's last known value plus its idle cash.

This is the *faithful* model of "equal capital distributed": cash sitting idle
in one sleeve genuinely cannot fund another, and a sleeve that draws down is not
rescued. It also requires no engine change at all, which means step 5 can ship
without risking anything that already works.

The union time axis is the only subtle part — symbols differ on listing dates,
halts and per-symbol gaps, and a naive `zip` of curves of different lengths
would silently misalign the whole portfolio.

### `pooled` — second

One cash balance, a `max_concurrent` cap, and an ordering rule when several
symbols signal on the same bar. Requirements:

- a deterministic tie-break (alphabetical by symbol is fine) **and** a stated
  acknowledgement that the choice changes the result
- a `skipped_no_capital` counter, in the spirit of the existing skip counters —
  a portfolio that could only afford a third of its signals did not return what
  its curve says

This needs the per-bar step drivable from outside, since `run()` owns `equity`
today. Do **not** refactor `run()` to get there. Add a generator-based `walk()`
alongside it that yields each bar's intent and accepts an allocation answer,
get pooled working, and converge the two only if they turn out to be the same
loop in practice.

### Portfolio metrics that do not exist yet

Per-symbol contribution · correlation between sleeve returns · worst
*simultaneous* drawdown (not the worst of the individual drawdowns, which is a
different and much friendlier number) · concurrently-open-positions over time,
because a rule flat 90% of the time across twenty symbols has a completely
different capital requirement from one always in all twenty.

### Survivorship

`universe/nse.py` fetches **current** index membership. Backtesting today's
Nifty 50 over three years excludes everything that was dropped for doing badly,
and that bias is large enough to manufacture an edge by itself. Emit a caveat
on every portfolio run whose symbol list came from a current membership. Fixing
it properly means dated membership history, which is a data problem worth
naming now and scheduling separately.

### Tests

- two symbols, ₹1,000 each, one winner one loser: the merged curve equals the
  sum at every timestamp, *including* timestamps where only one symbol has a bar
- a pooled run with `max_concurrent: 1` and two simultaneous signals takes one,
  counts one skipped, and takes the same one on a re-run
- worst simultaneous drawdown differs from max-of-individual on a constructed case

---

## Surfaces to follow each step

Kept per-step rather than as a phase of its own, so no step ships unusable:

- `RunRequest`: `instrument: "perp" | "equity"`, `product`, charge overrides
  *(step 1)*
- `OperandPicker` / `ConditionList`: candle and volume kinds, shape picker with
  visible thresholds *(step 2)*
- `RunResult`: charge breakdown, cost-per-trade in bps *(step 1)*; calendar
  panels *(step 4)*
- `POST /api/backtest/portfolio` and a per-symbol contribution table *(step 5)*

## Why this order

1 first because a candle-shape rule measured with crypto fees and fractional
sizing tells you nothing about whether it makes money — costs have to be right
before any result means anything.

2 next because it is pure addition with no engine risk, and it is the thing
actually being asked for.

3 before 5 because a portfolio built on misaligned hourly bars multiplies one
bug by twenty, and because the corporate-action question could invalidate
earlier results retroactively.

4 is independent and cheap, and can slot anywhere after 1.

5 last because it is the only step that touches the loop, and because it is the
step whose results are hardest to sanity-check — which is the wrong thing to be
debugging at the same time as a cost model.

## One thing to settle before step 3

Whether Fyers' historical equity bars are adjusted for splits and bonuses.
Everything in step 3 and every equity result before it depends on the answer,
and it is not derivable from this repo.
