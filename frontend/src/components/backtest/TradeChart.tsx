import { useEffect, useState } from "react";
import { getBacktestCandles } from "../../api";
import type { BacktestTrade, Candle, IndicatorLine, StrategySpec } from "../../api";
import { CandleChart } from "../CandleChart";
import { Oscillator } from "./Oscillator";
import type { Overlay } from "../CandleChart";

interface Props {
  trade: BacktestTrade;
  source: string;
  symbol: string;
  interval: string;
  /** The strategy, so its own indicators are drawn on the chart. Without them a
      crossover chart cannot show the crossing that caused the trade. */
  spec: StrategySpec;
  onClose: () => void;
}

/** Bars of context either side of the trade, so the entry has something to be
    early or late against. A trade held for two bars against a backdrop of two
    bars says nothing. */
const CONTEXT = 40;

const SECONDS: Record<string, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
};

/**
 * One trade, on the chart, where it happened.
 *
 * The point is verification rather than decoration. A row in a log saying a long
 * made 126 is a claim; this is where you see whether the entry was at a sensible
 * place, whether the exit gave most of it back, and whether the stop sat
 * somewhere the market was always going to reach. It is also how a suspicious
 * result gets caught - an entry printed at the exact low of a bar is the shape of
 * a lookahead bug, and no summary statistic would ever show it.
 */
export function TradeChart({ trade, source, symbol, interval, spec, onClose }: Props) {
  const [candles, setCandles] = useState<Candle[] | null>(null);
  const [lines, setLines] = useState<IndicatorLine[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const width = (SECONDS[interval] ?? 3600) * 1000;
    const start = new Date(Date.parse(trade.opened_at) - CONTEXT * width).toISOString();
    const end = new Date(Date.parse(trade.closed_at) + CONTEXT * width).toISOString();
    let current = true;
    setCandles(null);
    setError(null);
    void getBacktestCandles({ source, symbol, interval, start, end, spec })
      .then((r) => {
        if (!current) return;
        setCandles(r.candles);
        setLines(r.lines);
      })
      .catch((e: unknown) => current && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      current = false;
    };
  }, [trade, source, symbol, interval, spec]);

  const long = trade.side === "long";
  const overlay: Overlay = {
    levels: [
      { price: trade.entry, label: `in ${trade.entry.toFixed(1)}`, kind: "entry" },
      { price: trade.exit_price, label: `out ${trade.exit_price.toFixed(1)}`, kind: "exit" },
    ],
    marks: [
      { at: trade.opened_at, price: trade.entry, kind: "entry", side: long ? "long" : "short" },
      { at: trade.closed_at, price: trade.exit_price, kind: "exit", side: long ? "long" : "short" },
    ],
    band: { from: trade.opened_at, to: trade.closed_at },
    // Only the ones that are prices. The rest get their own panel below, or a
    // percentile running 0 to 100 would sit on the floor of a chart scaled to
    // Bitcoin and flatten every candle above it.
    lines: lines.filter((l) => l.on_price).map((l) => ({ label: l.label, values: l.values })),
  };
  const oscillators = lines.filter((l) => !l.on_price);

  return (
    <section className="tradechart">
      <header>
        <h3>
          {trade.side} · {trade.opened_at.slice(0, 16).replace("T", " ")}
        </h3>
        <span className={trade.net >= 0 ? "up" : "dn"}>
          {trade.net >= 0 ? "+" : ""}
          {trade.net.toFixed(2)} ({(trade.net_pct * 100).toFixed(1)}%)
        </span>
        <span className="sp" />
        {lines.length > 0 && (
          <span className="keys">
            {lines.map((l, n) => (
              <span key={l.label} className={`key i${n % 5}`}>
                {l.label}
              </span>
            ))}
          </span>
        )}
        <span className="dim">
          opened because {trade.entry_reason || "—"} · closed because {trade.exit_reason || "—"}
        </span>
        <button onClick={onClose} title="Close this chart">
          ×
        </button>
      </header>

      {error && <p className="bad">{error}</p>}
      {candles === null && !error && <p className="empty">Reading the bars…</p>}
      {candles !== null && candles.length === 0 && (
        <p className="empty">
          No bars stored over this window, so there is nothing to show.
        </p>
      )}
      {candles !== null && candles.length > 0 && (
        <CandleChart
          candles={candles}
          seriesId={`${symbol}-${interval}-${trade.opened_at}`}
          last={null}
          dp={1}
          height={300}
          overlay={overlay}
        />
      )}

      {candles !== null &&
        candles.length > 0 &&
        oscillators.map((line) => (
          <Oscillator key={line.label} line={line} colour={lines.indexOf(line)} />
        ))}
    </section>
  );
}
