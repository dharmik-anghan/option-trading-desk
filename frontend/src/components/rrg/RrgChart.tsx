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

/** Which quadrant a point is in. Mirrors the backend so a head drawn mid-replay
    is coloured by where it was then, not by where the series ends up. */
function quadrantOf(ratio: number, momentum: number): Quadrant {
  if (ratio >= 100) return momentum >= 100 ? "leading" : "weakening";
  return momentum >= 100 ? "improving" : "lagging";
}

/**
 * The rotation itself.
 *
 * Square, centred on 100/100 with the same span each way, because the picture is
 * about which quadrant something is in and how far out — and an axis scaled to
 * the data would move the quadrant lines, which are the only fixed thing on it.
 * The span grows to fit whatever is furthest out and never shrinks below a
 * readable minimum, or a quiet week would magnify noise into rotation.
 *
 * Tails fade towards the oldest end. A tail is a direction, and reading
 * direction off a line of identical dots means finding the head first.
 */
export function RrgChart({ series, tail, back, hovered, onHover }: Props) {
  // The visible slice of each path: `tail` points ending `back` periods ago.
  const drawn = useMemo(
    () =>
      series
        .map((s) => {
          const end = s.path.length - back;
          const from = Math.max(0, end - tail);
          return { series: s, points: end > 0 ? s.path.slice(from, end) : [] };
        })
        .filter((d) => d.points.length > 0),
    [series, tail, back],
  );

  const span = useMemo(() => {
    let furthest = 2.5;
    for (const { points } of drawn) {
      for (const p of points) {
        furthest = Math.max(furthest, Math.abs(p.ratio - 100), Math.abs(p.momentum - 100));
      }
    }
    return furthest * 1.12;
  }, [drawn]);

  const x = (ratio: number) => PAD + ((ratio - 100 + span) / (span * 2)) * PLOT;
  // Momentum grows upward, and an SVG's y grows downward.
  const y = (momentum: number) => PAD + PLOT - ((momentum - 100 + span) / (span * 2)) * PLOT;

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

      <line x1={x(100)} x2={x(100)} y1={PAD} y2={PAD + PLOT} className="rrgaxis" />
      <line x1={PAD} x2={PAD + PLOT} y1={y(100)} y2={y(100)} className="rrgaxis" />

      {drawn.map(({ series: s, points }) => {
        const head = points[points.length - 1];
        const where = quadrantOf(head.ratio, head.momentum);
        const dim = hovered !== null && hovered !== s.symbol;
        return (
          <g
            key={s.symbol}
            className={`dot ${where}${dim ? " dim" : ""}${hovered === s.symbol ? " on" : ""}`}
            onMouseEnter={() => onHover(s.symbol)}
            onMouseLeave={() => onHover(null)}
          >
            {points.slice(0, -1).map((p, i) => (
              <line
                key={p.at}
                x1={x(p.ratio)}
                y1={y(p.momentum)}
                x2={x(points[i + 1].ratio)}
                y2={y(points[i + 1].momentum)}
                className="tail"
                opacity={0.1 + 0.6 * ((i + 1) / points.length)}
              />
            ))}
            <circle cx={x(head.ratio)} cy={y(head.momentum)} r={4.5} className="head" />
            <text x={x(head.ratio) + 7} y={y(head.momentum) + 3.5} className="tag">
              {s.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
