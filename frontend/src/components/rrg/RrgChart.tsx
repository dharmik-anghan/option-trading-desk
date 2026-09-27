import { useMemo } from "react";
import type { Quadrant, RrgSeries } from "../../api";

interface Props {
  series: RrgSeries[];
  /** How many periods of each path to draw behind the head. */
  tail: number;
  /** How far back the head sits, in periods. Zero is the newest. */
  back: number;
  /** Which one is being pointed at, so the list and the graph agree. */
  hovered: string | null;
  onHover: (symbol: string | null) => void;
}

const QUADRANTS: { id: Quadrant; name: string; at: [number, number] }[] = [
  { id: "improving", name: "Improving", at: [0.25, 0.25] },
  { id: "leading", name: "Leading", at: [0.75, 0.25] },
  { id: "lagging", name: "Lagging", at: [0.25, 0.75] },
  { id: "weakening", name: "Weakening", at: [0.75, 0.75] },
];

const SIZE = 560;
const PAD = 26;
const PLOT = SIZE - PAD * 2;

/**
 * A path curving through every point, rather than straight lines between them.
 *
 * Catmull-Rom, converted to the cubic Béziers an SVG path speaks. The curve
 * passes through each point exactly — it is not a smoothing that moves them,
 * which on this chart would be drawing a rotation that did not happen. What it
 * removes is the corner at each period, and those corners are an artefact of
 * sampling rather than anything the market did.
 *
 * The control points are a sixth of the way along the neighbouring chord, which
 * is the standard tension: enough to round a corner, not enough to overshoot
 * into a loop on a sharp reversal — and sharp reversals are exactly what this
 * chart is for.
 */
function smoothPath(xs: number[], ys: number[]): string {
  const n = xs.length;
  if (n === 0) return "";
  if (n === 1) return `M${xs[0]} ${ys[0]}`;
  if (n === 2) return `M${xs[0]} ${ys[0]} L${xs[1]} ${ys[1]}`;

  const at = (i: number) => {
    const k = Math.max(0, Math.min(n - 1, i));
    return [xs[k], ys[k]] as const;
  };

  let d = `M${xs[0].toFixed(1)} ${ys[0].toFixed(1)}`;
  for (let i = 0; i < n - 1; i += 1) {
    const [x0, y0] = at(i - 1);
    const [x1, y1] = at(i);
    const [x2, y2] = at(i + 1);
    const [x3, y3] = at(i + 2);
    const c1x = x1 + (x2 - x0) / 6;
    const c1y = y1 + (y2 - y0) / 6;
    const c2x = x2 - (x3 - x1) / 6;
    const c2y = y2 - (y3 - y1) / 6;
    d +=
      ` C${c1x.toFixed(1)} ${c1y.toFixed(1)}` +
      ` ${c2x.toFixed(1)} ${c2y.toFixed(1)}` +
      ` ${x2.toFixed(1)} ${y2.toFixed(1)}`;
  }
  return d;
}

/** Which quadrant a point is in. Mirrors the backend, so a head drawn mid-replay
    is coloured by where it was then rather than by where the series ends up. */
function quadrantOf(ratio: number, momentum: number): Quadrant {
  if (ratio >= 100) return momentum >= 100 ? "leading" : "weakening";
  return momentum >= 100 ? "improving" : "lagging";
}

/**
 * The rotation itself.
 *
 * Square, centred on 100/100, with the same span each way — because the picture
 * is about which quadrant something is in and how far out, and the quadrant
 * lines are the only fixed thing on it.
 *
 * The span is computed from every point of every path, not from the ones
 * currently drawn. Sizing it to the visible slice made the axes breathe on every
 * frame of a replay: dots drifted because the scale moved under them, which
 * reads as lag and makes the rotation impossible to follow. A fixed scale also
 * means two frames of a replay are comparable, which is the entire point of
 * replaying it.
 *
 * Screen coordinates are computed once per snapshot rather than per frame, so
 * stepping through a replay slices numbers that already exist.
 */
export function RrgChart({ series, tail, back, hovered, onHover }: Props) {
  // Every path in screen space, and the scale they share. Recomputed only when
  // the data changes - not when the replay moves.
  const laid = useMemo(() => {
    let furthest = 2.5;
    for (const s of series) {
      for (const p of s.path) {
        furthest = Math.max(furthest, Math.abs(p.ratio - 100), Math.abs(p.momentum - 100));
      }
    }
    const span = furthest * 1.12;
    const x = (ratio: number) => PAD + ((ratio - 100 + span) / (span * 2)) * PLOT;
    // Momentum grows upward, and an SVG's y grows downward.
    const y = (momentum: number) => PAD + PLOT - ((momentum - 100 + span) / (span * 2)) * PLOT;

    return {
      centre: { x: x(100), y: y(100) },
      paths: series.map((s) => ({
        series: s,
        xs: s.path.map((p) => x(p.ratio)),
        ys: s.path.map((p) => y(p.momentum)),
      })),
    };
  }, [series]);

  const drawn = useMemo(
    () =>
      laid.paths
        .map(({ series: s, xs, ys }) => {
          const end = s.path.length - back;
          const from = Math.max(0, end - tail);
          if (end <= 0) return null;
          const head = s.path[end - 1];
          const cutX = xs.slice(from, end);
          const cutY = ys.slice(from, end);
          return {
            symbol: s.symbol,
            label: s.label,
            xs: cutX,
            ys: cutY,
            path: smoothPath(cutX, cutY),
            where: quadrantOf(head.ratio, head.momentum),
          };
        })
        .filter((d): d is NonNullable<typeof d> => d !== null && d.xs.length > 0),
    [laid, tail, back],
  );

  return (
    <svg
      className="rrg"
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      role="img"
      aria-label="Relative rotation graph"
    >
      {QUADRANTS.map((q) => (
        <g key={q.id} className={`quad ${q.id}`}>
          <rect
            x={PAD + (q.at[0] - 0.25) * PLOT}
            y={PAD + (q.at[1] - 0.25) * PLOT}
            width={PLOT / 2}
            height={PLOT / 2}
          />
          <text
            x={PAD + q.at[0] * PLOT}
            y={PAD + (q.at[1] === 0.25 ? 0.02 : 0.52) * PLOT + 14}
          >
            {q.name}
          </text>
        </g>
      ))}

      <line x1={laid.centre.x} x2={laid.centre.x} y1={PAD} y2={PAD + PLOT} className="rrgaxis" />
      <line x1={PAD} x2={PAD + PLOT} y1={laid.centre.y} y2={laid.centre.y} className="rrgaxis" />

      {drawn.map((d) => {
        const last = d.xs.length - 1;
        const dim = hovered !== null && hovered !== d.symbol;
        return (
          <g
            key={d.symbol}
            className={`dot ${d.where}${dim ? " dim" : ""}${hovered === d.symbol ? " on" : ""}`}
            onMouseEnter={() => onHover(d.symbol)}
            onMouseLeave={() => onHover(null)}
          >
            {/* One path rather than an element per step: the fade along the
                tail is worth less than the frames it costs with fifty
                securities on screen, and the head says which end is which. */}
            {last > 0 && <path d={d.path} className="tail" />}
            <circle cx={d.xs[last]} cy={d.ys[last]} r={4.5} className="head" />
            <text x={d.xs[last] + 7} y={d.ys[last] + 3.5} className="tag">
              {d.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
