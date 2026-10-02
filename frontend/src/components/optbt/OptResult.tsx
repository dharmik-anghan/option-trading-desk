import { useMemo, useState } from "react";
import type { OptbtResult, OptbtTrade } from "../../api";
import { Breakdowns } from "./Breakdowns";
import { EquityCurve } from "./EquityCurve";
import { FilterBar } from "./FilterBar";
import { MonthGrid } from "./MonthGrid";
import { Replay } from "./Replay";
import { TradeTable } from "./TradeTable";
import type { Explore } from "./explore";
import { NO_FILTER, apply, byMonth, curve, figures } from "./explore";
import { rupees, signed, tone } from "./format";

interface Props {
  result: OptbtResult;
  /** A newer run is in progress: this one is shown dimmed until it lands. */
  stale: boolean;
  /** When this result arrived, and how long the run took. */
  finishedAt: Date;
  seconds: number;
}

/**
 * A finished run, and any slice of it. Filters only ever narrow the trades
 * already run - nothing here runs the backtest again.
 */
export function OptResult({ result, stale, finishedAt, seconds }: Props) {
  const [picked, setPicked] = useState<OptbtTrade | null>(null);
  const [filter, setFilter] = useState<Explore>(NO_FILTER);

  const shown = useMemo(() => apply(result.trades, filter), [result.trades, filter]);
  // The month grid leaves out its own picks, so the other months stay clickable.
  const grid = useMemo(
    () => byMonth(apply(result.trades, { ...filter, months: [], years: [] })),
    [result.trades, filter],
  );
  const s = useMemo(() => figures(shown), [shown]);
  const equity = useMemo(() => curve(shown), [shown]);
  const endings = useMemo(() => [...new Set(result.trades.map((t) => t.ended))].sort(), [result.trades]);
  const toggle = (list: string[], item: string) =>
    list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
  const skipped = Object.values(result.skipped).reduce((a, n) => a + n, 0);
  const c = result.charges;

  return (
    <section className={`ob-results${stale ? " stale" : ""}`} aria-busy={stale}>
      <header className="ob-rhead">
        <h2>Results</h2>
        <span
          className="ob-when"
          title={
            skipped
              ? Object.entries(result.skipped)
                  .map(([why, n]) => `${n} days not traded: ${why}`)
                  .join("\n")
              : undefined
          }
        >
          {stale
            ? "Updating…"
            : `Updated ${finishedAt.toLocaleTimeString("en-IN", { hour12: false })} · ${seconds < 1 ? "under 1" : seconds.toFixed(0)} s · ${result.days} days${skipped ? `, ${skipped} not traded` : ""}`}
        </span>
      </header>

      <div className="ob-kpis">
        <div className="ob-kpi hero">
          <span>Net</span>
          <b className={tone(s.net)}>{signed(s.net)}</b>
        </div>
        <div className="ob-kpi">
          <span>Trades</span>
          <b>{s.trades.toLocaleString()}</b>
          <small>{s.trades ? `${Math.round((s.wins / s.trades) * 100)}% won` : ""}</small>
        </div>
        <div className="ob-kpi" title={`Median ${signed(s.median)}`}>
          <span>Per trade</span>
          <b className={tone(s.average)}>{signed(s.average)}</b>
        </div>
        <div className="ob-kpi" title="Deepest fall from a peak, on closed trades day by day">
          <span>Max drawdown</span>
          <b className="dn">{rupees(-s.maxDrawdown)}</b>
        </div>
        <div className="ob-kpi" title="Money won divided by money lost">
          <span>Profit factor</span>
          <b>{s.profitFactor === null ? "—" : s.profitFactor.toFixed(2)}</b>
        </div>
        <div className="ob-kpi" title={`Best trade ${signed(s.best)}`}>
          <span>Worst trade</span>
          <b className={tone(s.worst)}>{signed(s.worst)}</b>
        </div>
      </div>

      <p
        className="ob-money"
        title={
          `STT ${rupees(c.stt)} · exchange ${rupees(c.exchange)} · brokerage ${rupees(c.brokerage)}` +
          ` · GST ${rupees(c.gst)} · stamp and SEBI ${rupees(c.stamp + c.sebi)} (whole run)`
        }
      >
        Gross <b className={tone(s.gross)}>{signed(s.gross)}</b> − charges{" "}
        <b>{rupees(s.charges)}</b> = <b className={tone(s.net)}>{signed(s.net)}</b>
      </p>

      <FilterBar
        value={filter}
        onChange={setFilter}
        endings={endings}
        shown={shown.length}
        total={result.trades.length}
      />

      <EquityCurve equity={equity} />

      <div className="ob-split">
        {Object.keys(grid).length > 0 && (
          <MonthGrid
            byMonth={grid}
            months={filter.months}
            years={filter.years}
            onMonth={(m) => setFilter({ ...filter, months: toggle(filter.months, m) })}
            onYear={(y) => setFilter({ ...filter, years: toggle(filter.years, y) })}
          />
        )}
        <Breakdowns trades={shown} filter={filter} onFilter={setFilter} />
      </div>

      {picked && <Replay key={picked.id} trade={picked} onClose={() => setPicked(null)} />}

      {shown.length > 0 ? (
        <TradeTable trades={shown} picked={picked} onPick={setPicked} />
      ) : (
        <p className="ob-empty">No trades match these filters.</p>
      )}
    </section>
  );
}
