import { useEffect, useMemo, useState } from "react";
import { getOptbtReplay } from "../../api";
import type { OptbtReplay, OptbtTrade } from "../../api";
import { day, dir, hhmm, premium, signedRupees } from "../../format";
import { useWidth } from "../../hooks/useWidth";

interface Props {
  trade: OptbtTrade;
  onClose: () => void;
}

const PREMIUM_H = 230;
const SPOT_H = 120;
const PAD = { top: 14, right: 64, bottom: 20, left: 56 };

/**
 * One trade, minute by minute: what each leg's premium did, where its stop sat,
 * and where the index was.
 *
 * Two panels on a shared time axis rather than one chart with two scales. A
 * premium in tens of rupees and an index in tens of thousands on one plot would
 * make whichever is drawn second look like it tracks the first by construction.
 *
 * Legs are told apart by line style and a label at each line's end, not by
 * colour - on this desk colour is reserved for profit and loss: calls solid,
 * puts dashed, bought legs (the hedges) thinner than sold ones.
 */
export function Replay({ trade, onClose }: Props) {
  const [data, setData] = useState<OptbtReplay | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  // Mounted afresh for each trade (keyed by id), so there is no stale replay to clear.
  useEffect(() => {
    const last = trade.closed ?? trade.opened;
    void getOptbtReplay({
      start: trade.opened.slice(0, 10),
      end: last.slice(0, 10),
      legs: trade.legs.map((l) => ({ expiry: l.expiry, strike: l.strike, kind: l.kind })),
    })
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [trade]);

  const chart = useMemo(() => {
    if (!data || data.spot.points.length < 2) return null;
    const times = data.spot.points.map(([t]) => t);
    const index = new Map(times.map((t, i) => [t, i]));
    const legs = data.legs.map((s) => {
      const byIndex = new Map<number, number>();
      for (const [t, v] of s.points) {
        const i = index.get(t);
        if (i !== undefined) byIndex.set(i, v);
      }
      return byIndex;
    });
    const stops = trade.legs.map((l) => l.stop).filter((v): v is number => v !== null);
    const all = [
      ...legs.flatMap((m) => [...m.values()]),
      ...stops,
      ...trade.legs.map((l) => l.entry),
    ];
    return {
      times,
      index,
      legs,
      spot: data.spot.points.map(([, v]) => v),
      pTop: Math.max(...all) * 1.04,
      pBottom: Math.max(0, Math.min(...all) * 0.9),
    };
  }, [data, trade]);

  const plotW = width - PAD.left - PAD.right;

  return (
    <section className="replay" ref={ref}>
      <header>
        <h3>
          {day(trade.opened)}
          {trade.closed && trade.closed.slice(0, 10) !== trade.opened.slice(0, 10)
            ? ` – ${day(trade.closed)}`
            : ""}{" "}
          · {trade.legs.length} legs · <span className={dir(trade.net)}>{signedRupees(trade.net)}</span>{" "}
          net
        </h3>
        <button onClick={onClose}>Close</button>
      </header>

      {error && <p className="bad">{error}</p>}
      {!data && !error && <p className="dim">Reading the minutes…</p>}

      {chart && (
        <>
          <Legend trade={trade} hover={hover} chart={chart} />
          <svg
            width={width}
            height={PREMIUM_H + SPOT_H}
            onMouseMove={(e) => {
              const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
              const i = Math.round(((px - PAD.left) / plotW) * (chart.times.length - 1));
              setHover(i >= 0 && i < chart.times.length ? i : null);
            }}
            onMouseLeave={() => setHover(null)}
            role="img"
            aria-label="Each leg's premium and the index, minute by minute"
          >
            <PremiumPanel trade={trade} chart={chart} width={width} />
            <SpotPanel trade={trade} chart={chart} width={width} />
            {hover !== null && (
              <line
                x1={PAD.left + (hover / (chart.times.length - 1)) * plotW}
                x2={PAD.left + (hover / (chart.times.length - 1)) * plotW}
                y1={PAD.top}
                y2={PREMIUM_H + SPOT_H - PAD.bottom}
                className="cross"
              />
            )}
          </svg>
        </>
      )}

      <ol className="events">
        {trade.events.map((e) => (
          <li key={e}>{e}</li>
        ))}
      </ol>
    </section>
  );
}

type Chart = {
  times: string[];
  index: Map<string, number>;
  legs: Map<number, number>[];
  spot: number[];
  pTop: number;
  pBottom: number;
};

function Legend({ trade, hover, chart }: { trade: OptbtTrade; hover: number | null; chart: Chart }) {
  const at = hover ?? chart.times.length - 1;
  return (
    <div className="legend">
      <span className="time">{hover === null ? "at the close" : hhmm(chart.times[at])}</span>
      {trade.legs.map((l, k) => (
        <span key={k} className={`key ${l.kind === "CE" ? "ce" : "pe"} ${l.side}`}>
          <i />
          {legName(l)} {premium(nearest(chart.legs[k], at))}
        </span>
      ))}
      <span className="key spot">
        <i />
        NIFTY {chart.spot[at].toLocaleString("en-IN", { maximumFractionDigits: 2 })}
      </span>
    </div>
  );
}

/** "S 23550 CE". */
function legName(l: OptbtTrade["legs"][number]): string {
  return `${l.side === "sell" ? "S" : "B"} ${l.strike} ${l.kind}`;
}

/** A leg's price at a minute, or the last one before it if that minute is missing. */
function nearest(series: Map<number, number>, i: number): number | null {
  for (let k = i; k >= 0; k--) {
    const v = series.get(k);
    if (v !== undefined) return v;
  }
  return null;
}

function PremiumPanel({ trade, chart, width }: { trade: OptbtTrade; chart: Chart; width: number }) {
  const plotW = width - PAD.left - PAD.right;
  const n = chart.times.length - 1;
  const x = (i: number) => PAD.left + (i / n) * plotW;
  const span = chart.pTop - chart.pBottom || 1;
  const y = (v: number) => PAD.top + ((chart.pTop - v) / span) * (PREMIUM_H - PAD.top - 8);
  const at = (ts: string | null) => (ts ? chart.index.get(ts.slice(0, 19)) : undefined);

  return (
    <g>
      {[chart.pTop, (chart.pTop + chart.pBottom) / 2, chart.pBottom].map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
          <text x={PAD.left - 8} y={y(t) + 4} className="axis" textAnchor="end">
            {t.toFixed(0)}
          </text>
        </g>
      ))}
      <text x={PAD.left} y={PAD.top - 3} className="axis">
        premium, ₹
      </text>
      {trade.legs.map((leg, k) => {
        const pts = [...chart.legs[k].entries()].sort((a, b) => a[0] - b[0]);
        const path = pts.map(([i, v], j) => `${j ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
        const entryI = at(leg.entry_at);
        const exitI = at(leg.exit_at);
        const last = pts[pts.length - 1];
        return (
          <g key={k} className={`${leg.kind === "CE" ? "ce" : "pe"} ${leg.side}`}>
            <path d={path.join(" ")} className="leg" />
            {leg.stop !== null && entryI !== undefined && (
              <g>
                <line
                  x1={x(entryI)}
                  x2={x(exitI ?? n)}
                  y1={y(leg.stop)}
                  y2={y(leg.stop)}
                  className="stopline"
                />
                <text x={x(exitI ?? n) + 4} y={y(leg.stop) + 4} className="tag">
                  stop {leg.stop.toFixed(1)}
                </text>
              </g>
            )}
            {entryI !== undefined && (
              <path
                d={
                  leg.side === "sell"
                    ? `M${x(entryI)},${y(leg.entry) - 7} l-5,-8 h10 z`
                    : `M${x(entryI)},${y(leg.entry) + 7} l-5,8 h10 z`
                }
                className={leg.side === "sell" ? "sold" : "bought"}
              >
                <title>
                  {legName(leg)} {leg.side === "sell" ? "sold" : "bought"} {premium(leg.entry)} at{" "}
                  {hhmm(leg.entry_at)}
                </title>
              </path>
            )}
            {exitI !== undefined && leg.exit !== null && (
              <circle cx={x(exitI)} cy={y(leg.exit)} r={5} className={`out ${dir(leg.pnl)}`}>
                <title>
                  {legName(leg)} closed {premium(leg.exit)} at {hhmm(leg.exit_at)} ({leg.ended}),{" "}
                  {signedRupees(leg.pnl)}
                </title>
              </circle>
            )}
            {last && (
              <text x={width - PAD.right + 4} y={y(last[1]) + 4} className="tag">
                {leg.side === "sell" ? "S" : "B"} {leg.strike} {leg.kind}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

function SpotPanel({ trade, chart, width }: { trade: OptbtTrade; chart: Chart; width: number }) {
  const plotW = width - PAD.left - PAD.right;
  const n = chart.times.length - 1;
  const top0 = PREMIUM_H + 6;
  // The sold strikes are where the position is tested, so they are the lines drawn.
  const sold = [...new Set(trade.legs.filter((l) => l.side === "sell").map((l) => l.strike))];
  const strikes = sold.length ? sold : [...new Set(trade.legs.map((l) => l.strike))];
  const hi = Math.max(...strikes, ...chart.spot);
  const lo = Math.min(...strikes, ...chart.spot);
  const span = hi - lo || 1;
  const x = (i: number) => PAD.left + (i / n) * plotW;
  const y = (v: number) => top0 + ((hi - v) / span) * (SPOT_H - PAD.bottom - 8);
  const days = chart.times
    .map((t, i) => ({ i, d: t.slice(0, 10) }))
    .filter((p, k, all) => k === 0 || p.d !== all[k - 1].d);
  const hours = chart.times
    .map((t, i) => ({ i, t: t.slice(11, 16) }))
    .filter((p) => p.t.endsWith(":00") || p.t === "09:15");

  return (
    <g className="spot">
      {strikes.map((k) => (
        <g key={k}>
          <line x1={PAD.left} x2={width - PAD.right} y1={y(k)} y2={y(k)} className="strike" />
          <text x={width - PAD.right + 4} y={y(k) + 4} className="tag">
            {k}
          </text>
        </g>
      ))}
      <path
        d={chart.spot.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}
        className="index"
      />
      <text x={PAD.left - 8} y={y(hi) + 4} className="axis" textAnchor="end">
        {hi.toFixed(0)}
      </text>
      <text x={PAD.left - 8} y={y(lo) + 4} className="axis" textAnchor="end">
        {lo.toFixed(0)}
      </text>
      {(days.length > 1 ? days.map((d) => ({ i: d.i, t: d.d.slice(5) })) : hours).map((p) => (
        <text key={p.i} x={x(p.i)} y={PREMIUM_H + SPOT_H - 4} className="axis" textAnchor="middle">
          {p.t}
        </text>
      ))}
    </g>
  );
}
