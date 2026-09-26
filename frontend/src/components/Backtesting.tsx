import { useEffect, useState } from "react";
import { getBarSeries } from "../api";
import type { BarSeries } from "../api";

interface Props {
  onHome: () => void;
}

/** Where a series came from, said in words rather than a slug. */
const SOURCE: Record<string, string> = {
  shark: "Shark — the venue you trade",
  binance: "Binance — deep crypto history",
  yahoo: "Yahoo Finance — metals and energy",
  fyers: "Fyers — Indian options and indices",
};

/**
 * What history exists.
 *
 * This is the first page of backtesting rather than a placeholder for it, because
 * the question it answers is the one that comes before any strategy: a run over a
 * window the store does not hold is not a poor result, it is a meaningless one, and
 * nothing on screen would say so unless this page did.
 *
 * Bars accumulate as the desks are used - a chart opened on gold stores gold - so
 * this list grows on its own and is worth coming back to.
 */
export function Backtesting({ onHome }: Props) {
  const [series, setSeries] = useState<BarSeries[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getBarSeries()
      .then(setSeries)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const total = (series ?? []).reduce((n, s) => n + s.bars, 0);

  return (
    <main className="bt">
      <header>
        <button className="uphome" onClick={onHome} title="Back to the three desks">
          ←
        </button>
        <h1>Backtesting</h1>
        <p>
          {series === null
            ? "Reading what the store holds…"
            : total === 0
              ? "No bars stored yet. Open a chart on either desk and its history is kept here."
              : `${total.toLocaleString()} bars across ${series.length} series.`}
        </p>
      </header>

      {error && <p className="bad">Could not read the store: {error}</p>}

      {series !== null && series.length > 0 && (
        <table className="held">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Size</th>
              <th>Source</th>
              <th>Bars</th>
              <th>From</th>
              <th>To</th>
            </tr>
          </thead>
          <tbody>
            {series.map((s) => (
              <tr key={`${s.source}/${s.symbol}/${s.interval}`}>
                <td>
                  <b>{s.symbol}</b>
                </td>
                <td>{s.interval}</td>
                <td>{SOURCE[s.source] ?? s.source}</td>
                <td className="n">{s.bars.toLocaleString()}</td>
                <td>{day(s.first)}</td>
                <td>{day(s.last)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="note">
        A run is only as honest as the series under it. The stored bars are whatever a
        source served at the time and are not restated, so two runs over the same window
        read the same data — which is the point of keeping them here rather than fetching
        each time.
      </p>
    </main>
  );
}

/** A date, or an em dash when the store holds no window for a series. */
function day(iso: string | null): string {
  if (!iso) return "—";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toISOString().slice(0, 10);
}
