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

const QUADRANTS: { id: Quadrant; name: string; corner: "tl" | "tr" | "bl" | "br" }[] = [
  { id: "improving", name: "Improving", corner: "tl" },
  { id: "leading", name: "Leading", corner: "tr" },
  { id: "lagging", name: "Lagging", corner: "bl" },
  { id: "weakening", name: "Weakening", corner: "br" },
];

// Room on the left and below for the axis values; the plot itself is square.
const LEFT = 34;
const TOP = 8;
const PLOT = 560;
const BOTTOM = 36;
const RIGHT = 8;
const W = LEFT + PLOT + RIGHT;
const H = TOP + PLOT + BOTTOM;

/** Width of a label in viewBox units, near enough to keep two from touching. */
const CHAR = 5.6;
const TAG_H = 11;

/** Which quadrant a point is in. Mirrors the backend, so a head drawn mid-replay
    is coloured by where it was then rather than by where the series ends up. */
export function quadrantOf(ratio: number, momentum: number): Quadrant {
  if (ratio >= 100) return momentum >= 100 ? "leading" : "weakening";
  return momentum >= 100 ? "improving" : "lagging";
}

/** Round steps for the axis: 1, 2 or 5 times a power of ten, about four a side. */
function ticks(span: number): number[] {
  const raw = span / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = -Math.floor(span / step) * step; v <= span + 1e-9; v += step) {
    out.push(100 + v);
  }
  return out;
}

type Box = { x: number; y: number; w: number; h: number };

function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * The rotation itself.
 *
 * Square, centred on 100/100, with the same span each way — the picture is
 * about which quadrant something is in and how far out, and the quadrant lines
 * are the only fixed thing on it.
 *
 * The span is computed from every point of every path, not from the ones
 * currently drawn, so the axes do not breathe during a replay and two frames
 * can be compared.
 *
 * Tails are quiet on purpose. Fourteen of them in four quadrant colours, each
 * curving through its points, was a tangle that hid the heads; drawn thin,
 * neutral and fading toward the oldest point, they still show the direction of
 * travel, and the one being pointed at is drawn in full with a mark per period.
 */
export function RrgChart({ series, tail, back, hovered, onHover }: Props) {
  const laid = useMemo(() => {
    let furthest = 2;
    for (const s of series) {
      for (const p of s.path) {
        furthest = Math.max(furthest, Math.abs(p.ratio - 100), Math.abs(p.momentum - 100));
      }
    }
    const span = furthest * 1.08;
    const x = (ratio: number) => LEFT + ((ratio - 100 + span) / (span * 2)) * PLOT;
    // Momentum grows upward, and an SVG's y grows downward.
    const y = (momentum: number) => TOP + PLOT - ((momentum - 100 + span) / (span * 2)) * PLOT;
    return {
      x,
      y,
      ticks: ticks(span).filter((t) => Math.abs(t - 100) <= span),
      paths: series.map((s) => ({
        series: s,
        xs: s.path.map((p) => x(p.ratio)),
        ys: s.path.map((p) => y(p.momentum)),
      })),
    };
  }, [series]);

  const drawn = useMemo(() => {
    const heads = laid.paths
      .map(({ series: s, xs, ys }) => {
        const end = s.path.length - back;
        if (end <= 0) return null;
        const from = Math.max(0, end - tail - 1);
        const head = s.path[end - 1];
        return {
          symbol: s.symbol,
          label: s.label,
          xs: xs.slice(from, end),
          ys: ys.slice(from, end),
          where: quadrantOf(head.ratio, head.momentum),
        };
      })
      .filter((d): d is NonNullable<typeof d> => d !== null && d.xs.length > 0);

    // Labels placed greedily, furthest from the centre first - those are the
    // ones a reader is looking for. Each tries right, left, above and below its
    // dot and takes the first spot nothing else holds; one with nowhere to go
    // is left for hover rather than printed over a neighbour.
    const cx = laid.x(100);
    const cy = laid.y(100);
    const order = heads
      .map((d, i) => ({ i, far: Math.hypot(d.xs.at(-1)! - cx, d.ys.at(-1)! - cy) }))
      .sort((a, b) => b.far - a.far);
    const taken: Box[] = heads.map((d) => ({
      x: d.xs.at(-1)! - 4,
      y: d.ys.at(-1)! - 4,
      w: 8,
      h: 8,
    }));
    const tags = new Map<number, { x: number; y: number; anchor: "start" | "end" | "middle" }>();
    for (const { i } of order) {
      const d = heads[i];
      const hx = d.xs.at(-1)!;
      const hy = d.ys.at(-1)!;
      const w = d.label.length * CHAR;
      const tries = [
        { x: hx + 7, y: hy + 3.5, anchor: "start" as const, box: { x: hx + 7, y: hy - 5, w, h: TAG_H } },
        { x: hx - 7, y: hy + 3.5, anchor: "end" as const, box: { x: hx - 7 - w, y: hy - 5, w, h: TAG_H } },
        { x: hx, y: hy - 8, anchor: "middle" as const, box: { x: hx - w / 2, y: hy - 17, w, h: TAG_H } },
        { x: hx, y: hy + 15, anchor: "middle" as const, box: { x: hx - w / 2, y: hy + 6, w, h: TAG_H } },
      ];
      const own = taken[i];
      const fit = tries.find(
        (t) =>
          t.box.x >= LEFT &&
          t.box.x + t.box.w <= LEFT + PLOT &&
          t.box.y >= TOP &&
          t.box.y + t.box.h <= TOP + PLOT &&
          !taken.some((b) => b !== own && overlaps(b, t.box)),
      );
      if (fit) {
        taken.push(fit.box);
        tags.set(i, { x: fit.x, y: fit.y, anchor: fit.anchor });
      }
    }
    return heads.map((d, i) => ({ ...d, tag: tags.get(i) ?? null }));
  }, [laid, tail, back]);

  const centre = { x: laid.x(100), y: laid.y(100) };
  // The one being pointed at goes last, so it is drawn over everything else.
  const ordered = hovered
    ? [...drawn.filter((d) => d.symbol !== hovered), ...drawn.filter((d) => d.symbol === hovered)]
    : drawn;

  return (
    <svg
      className="rrg"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="Relative rotation graph"
    >
      {QUADRANTS.map((q) => {
        const right = q.corner.endsWith("r");
        const bottom = q.corner.startsWith("b");
        const x0 = right ? centre.x : LEFT;
        const y0 = bottom ? centre.y : TOP;
        return (
          <g key={q.id} className={`quad ${q.id}`}>
            <rect
              x={x0}
              y={y0}
              width={right ? LEFT + PLOT - centre.x : centre.x - LEFT}
              height={bottom ? TOP + PLOT - centre.y : centre.y - TOP}
            />
          </g>
        );
      })}

      {laid.ticks.map((t) => (
        <g key={t} className="tick">
          {t !== 100 && (
            <>
              <line x1={laid.x(t)} x2={laid.x(t)} y1={TOP} y2={TOP + PLOT} />
              <line x1={LEFT} x2={LEFT + PLOT} y1={laid.y(t)} y2={laid.y(t)} />
            </>
          )}
          <text x={laid.x(t)} y={TOP + PLOT + 15} textAnchor="middle">
            {t.toFixed(t % 1 ? 1 : 0)}
          </text>
          <text x={LEFT - 6} y={laid.y(t) + 3} textAnchor="end">
            {t.toFixed(t % 1 ? 1 : 0)}
          </text>
        </g>
      ))}
      <text className="axisname" x={LEFT + PLOT} y={TOP + PLOT + 30} textAnchor="end">
        Relative strength →
      </text>
      <text
        className="axisname"
        transform={`translate(${LEFT - 26} ${TOP}) rotate(-90)`}
        textAnchor="end"
      >
        Momentum →
      </text>

      <line x1={centre.x} x2={centre.x} y1={TOP} y2={TOP + PLOT} className="rrgaxis" />

      <line x1={LEFT} x2={LEFT + PLOT} y1={centre.y} y2={centre.y} className="rrgaxis" />

      {/* Over the grid, so no line runs through a name. */}
      {QUADRANTS.map((q) => {
        const right = q.corner.endsWith("r");
        const bottom = q.corner.startsWith("b");
        return (
          <text
            key={q.id}
            className={`quadname ${q.id}`}
            x={right ? LEFT + PLOT - 10 : LEFT + 10}
            y={bottom ? TOP + PLOT - 10 : TOP + 20}
            textAnchor={right ? "end" : "start"}
          >
            {q.name}
          </text>
        );
      })}

      {ordered.map((d) => {
        const last = d.xs.length - 1;
        const on = hovered === d.symbol;
        const dim = hovered !== null && !on;
        return (
          <g
            key={d.symbol}
            className={`dot ${d.where}${dim ? " dim" : ""}${on ? " on" : ""}`}
            onMouseEnter={() => onHover(d.symbol)}
            onMouseLeave={() => onHover(null)}
          >
            {/* A segment per period so the trail can fade toward its oldest end. */}
            {d.xs.slice(1).map((x, k) => (
              <line
                key={k}
                className="tail"
                x1={d.xs[k]}
                y1={d.ys[k]}
                x2={x}
                y2={d.ys[k + 1]}
                style={{ strokeOpacity: on ? 0.9 : 0.05 + (0.3 * (k + 1)) / last }}
              />
            ))}
            {on &&
              d.xs
                .slice(0, last)
                .map((x, k) => <circle key={k} cx={x} cy={d.ys[k]} r={2.2} className="step" />)}
            <circle cx={d.xs[last]} cy={d.ys[last]} r={on ? 6 : 4.5} className="head" />
            {(d.tag || on) && (
              <text
                x={d.tag?.x ?? d.xs[last] + 8}
                y={d.tag?.y ?? d.ys[last] + 3.5}
                textAnchor={d.tag?.anchor ?? "start"}
                className="tag"
              >
                {d.label}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
