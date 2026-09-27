import type { IndicatorLine } from "../api";

interface Props {
  line: IndicatorLine;
  colour: number;
  /** The window the candles above are showing. Without it this drew the whole
      series while the chart was zoomed into a corner of it, and the two panels
      disagreed about which bar was which - a crosshair on one landing somewhere
      else on the other. */
  start?: number;
  end?: number;
  /** The bar under the cursor, as an index into the whole series. */
  hovered?: number | null;
}

/**
 * An indicator that is not a price, on its own scale under the candles.
 *
 * An RSI runs 0 to 100 and a pivot-gap percentile likewise, while Bitcoin runs
 * in the tens of thousands. Drawn on the price axis either one would be a flat
 * line along the bottom and every candle would collapse into a band - so they
 * get their own panel, one each, because two indicators with different ranges
 * share an axis no better than they share the price's.
 *
 * Bounded indicators keep their own bounds rather than being fitted to what
 * happened to be on screen. An RSI scaled to its window would show a reading of
 * 45 touching the top of the panel, which is exactly the opposite of what an
 * oscillator is for.
 */
const BOUNDED: Record<string, [number, number]> = {
  rsi: [0, 100],
  pivot_gap_rank: [0, 100],
};

/** Lines worth marking on a bounded indicator. */
const GUIDES: Record<string, number[]> = {
  rsi: [30, 70],
  pivot_gap_rank: [10, 90],
};

export function Oscillator({ line, colour, start, end, hovered }: Props) {
  const from = start ?? 0;
  const to = end ?? line.values.length;
  const values = line.values.slice(from, to);
  const drawn = values.filter((v): v is number => typeof v === "number");
  if (!drawn.length) return null;

  const W = 1000;
  const H = 76;
  const PAD = { top: 6, right: 54, bottom: 6, left: 6 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const bounds = BOUNDED[line.name];
  const min = bounds ? bounds[0] : Math.min(...drawn);
  const max = bounds ? bounds[1] : Math.max(...drawn);
  const span = max - min || 1;

  const step = plotW / values.length;
  const y = (v: number) => PAD.top + plotH - ((v - min) / span) * plotH;

  const path: string[] = [];
  let drawing = false;
  values.forEach((v, i) => {
    if (typeof v !== "number") {
      drawing = false;
      return;
    }
    const x = PAD.left + i * step + step / 2;
    path.push(`${drawing ? "L" : "M"}${x.toFixed(1)} ${y(v).toFixed(1)}`);
    drawing = true;
  });

  const guides = GUIDES[line.name] ?? [];
  // The hovered value if the cursor is over this window, and the newest
  // otherwise - so the number on the right is always about a bar you can see.
  const at = hovered !== null && hovered !== undefined ? hovered - from : null;
  const underCursor = at !== null && at >= 0 && at < values.length ? values[at] : null;
  const last =
    typeof underCursor === "number"
      ? underCursor
      : [...values].reverse().find((v): v is number => typeof v === "number");
  const hoverX =
    at !== null && at >= 0 && at < values.length ? PAD.left + at * step + step / 2 : null;

  return (
    <svg className="osc" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img">
      <text x={PAD.left + 2} y={PAD.top + 9} className="osclabel">
        {line.label}
      </text>
      {guides.map((level) => (
        <g key={level}>
          <line
            x1={PAD.left}
            x2={PAD.left + plotW}
            y1={y(level)}
            y2={y(level)}
            className="oscguide"
          />
          <text x={PAD.left + plotW + 4} y={y(level) + 3} className="oscaxis">
            {level}
          </text>
        </g>
      ))}
      {hoverX !== null && (
        <line x1={hoverX} x2={hoverX} y1={PAD.top} y2={PAD.top + plotH} className="cross" />
      )}
      <path d={path.join(" ")} className={`oscline i${colour % 5}`} />
      {last !== undefined && last !== null && (
        <text x={PAD.left + plotW + 4} y={y(last) + 3} className="osclast">
          {last.toFixed(bounds ? 0 : 2)}
        </text>
      )}
    </svg>
  );
}
