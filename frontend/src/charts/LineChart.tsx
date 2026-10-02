import { useState, type ReactNode } from "react";
import { useWidth } from "../hooks/useWidth";
import { linePath, linear, type Pad } from "./scale";
import type { Point } from "./series";

export interface Scales {
  /** The x of the i-th point. */
  x: (i: number) => number;
  y: (value: number) => number;
  width: number;
}

interface Props {
  points: Point[];
  height: number;
  ariaLabel: string;
  /** Styles the chart: the line is `.eq`, the baseline `.base`, grid `.grid`. */
  className?: string;
  pad?: Pad;
  /** A level always in view and drawn - zero for a P&L, the capital for an account. */
  baseline?: number;
  /** Shade the fall from the running peak beneath the line: `.underwater`. */
  underwater?: boolean;
  /** Label the top, middle and bottom of the range on the left axis. */
  yAxis?: (value: number) => string;
  /** Points to label along the bottom, by index. */
  xLabels?: { i: number; text: string }[];
  /** Hover is on when this is given: a crosshair and a dot follow the pointer. */
  onHover?: (i: number | null) => void;
  /** Anything else drawn on the same scales - a marker, a label. */
  children?: (scales: Scales) => ReactNode;
}

const NO_PAD: Pad = { top: 4, right: 4, bottom: 4, left: 4 };

/**
 * A value over time, against a baseline. Every line chart on the desk is this.
 *
 * Drawn in real pixels at the width it is given, so text is never squashed and
 * the pointer maps to the point under it. Points are spaced by index, not time:
 * the series here are sessions and snapshots, and a weekend is not a gap in one.
 */
export function LineChart({
  points,
  height,
  ariaLabel,
  className,
  pad = NO_PAD,
  baseline,
  underwater = false,
  yAxis,
  xLabels = [],
  onHover,
  children,
}: Props) {
  const { ref, width } = useWidth<HTMLDivElement>(600, 120);
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;

  const values = points.map((p) => p.value);
  const held = baseline === undefined ? values : [...values, baseline];
  const top = Math.max(...held);
  const bottom = Math.min(...held);
  const right = width - pad.right;
  const x = linear(0, points.length - 1, pad.left, right);
  const y = top === bottom ? () => height / 2 : linear(top, bottom, pad.top, height - pad.bottom);

  const xs = values.map((_, i) => x(i));
  const line = linePath(
    xs,
    values.map((v) => y(v)),
  );

  let shade: string | null = null;
  if (underwater) {
    const peaks: number[] = [];
    for (const v of values) peaks.push(Math.max(peaks.at(-1) ?? v, v));
    // The gap between the running peak and the curve, closed back along the peak.
    const back = peaks
      .map((p, i) => `L${xs[i].toFixed(1)},${y(p).toFixed(1)}`)
      .reverse()
      .join(" ");
    shade = `${line} ${back} Z`;
  }

  const ticks = yAxis
    ? [top, (top + bottom) / 2, bottom].filter((t, i, all) => all.indexOf(t) === i)
    : [];

  const pick = (i: number | null) => {
    setHover(i);
    onHover?.(i);
  };
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const px = e.clientX - e.currentTarget.getBoundingClientRect().left;
    const i = Math.round(((px - pad.left) / (right - pad.left)) * (points.length - 1));
    pick(i >= 0 && i < points.length ? i : null);
  };

  return (
    <div ref={ref} className={className}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={ariaLabel}
        onMouseMove={onHover ? onMove : undefined}
        onMouseLeave={onHover ? () => pick(null) : undefined}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.left} x2={right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.left - 8} y={y(t) + 4} className="axis" textAnchor="end">
              {yAxis?.(t)}
            </text>
          </g>
        ))}
        {baseline !== undefined && (
          <line x1={pad.left} x2={right} y1={y(baseline)} y2={y(baseline)} className="base" />
        )}
        {xLabels.map(({ i, text }) => (
          <text key={`${i}-${text}`} x={x(i)} y={height - 6} className="axis">
            {text}
          </text>
        ))}
        {shade && <path d={shade} className="underwater" />}
        <path d={line} className="eq" />
        {onHover && hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={height - pad.bottom} className="cross" />
            <circle cx={x(hover)} cy={y(values[hover])} r={4} className="dot" />
          </g>
        )}
        {children?.({ x, y, width })}
      </svg>
    </div>
  );
}
