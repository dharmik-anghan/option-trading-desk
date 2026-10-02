import { useState } from "react";
import { day, rupees, signed } from "./format";
import { useWidth } from "./useWidth";

interface Props {
  /** [day, cumulative net] at each close. */
  equity: [string, number][];
}

const HEIGHT = 220;
const PAD = { top: 12, right: 12, bottom: 22, left: 64 };

/**
 * Cumulative net P&L, a point a day, against zero.
 *
 * Zero is always in view, so the line crossing it means the run was losing
 * money, and the drawdown from the running peak is shaded beneath - the part of
 * the curve a trader lives through, not just its end point. Hover reads a day.
 */
export function EquityCurve({ equity }: Props) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  if (equity.length < 2) return null;

  const values = equity.map(([, v]) => v);
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const span = top - bottom || 1;
  const plotW = width - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (i / (equity.length - 1)) * plotW;
  const y = (v: number) => PAD.top + ((top - v) / span) * plotH;

  const peaks: number[] = [];
  for (const v of values) peaks.push(Math.max(peaks.at(-1) ?? v, v));

  const line = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  // The gap between the running peak and the curve, closed back along the peak.
  const underwater =
    line.join(" ") +
    " " +
    peaks
      .map((p, i) => [x(i), y(p)] as const)
      .reverse()
      .map(([px, py]) => `L${px.toFixed(1)},${py.toFixed(1)}`)
      .join(" ") +
    " Z";

  const ticks = [top, (top + bottom) / 2, bottom].filter(
    (t, i, all) => all.indexOf(t) === i,
  );
  const years = equity
    .map(([d], i) => ({ i, y: d.slice(0, 4) }))
    .filter((p, k, all) => k === 0 || p.y !== all[k - 1].y);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - box.left;
    const i = Math.round(((px - PAD.left) / plotW) * (equity.length - 1));
    setHover(i >= 0 && i < equity.length ? i : null);
  };

  const h = hover === null ? null : equity[hover];

  return (
    <figure className="ocurve" ref={ref}>
      <figcaption>
        Equity
        {h && hover !== null && (
          <span className="readout">
            {day(h[0])} · <b>{signed(h[1])}</b>
            {peaks[hover] > h[1] && ` · ${rupees(h[1] - peaks[hover])} from the peak`}
          </span>
        )}
      </figcaption>
      <svg
        width={width}
        height={HEIGHT}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label="Cumulative net profit and loss by day"
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={PAD.left - 8} y={y(t) + 4} className="axis" textAnchor="end">
              {rupees(t)}
            </text>
          </g>
        ))}
        <line x1={PAD.left} x2={width - PAD.right} y1={y(0)} y2={y(0)} className="zero" />
        {years.map(({ i, y: yr }) => (
          <text key={yr} x={x(i)} y={HEIGHT - 6} className="axis">
            {yr}
          </text>
        ))}
        <path d={underwater} className="underwater" />
        <path d={line.join(" ")} className="eq" />
        {hover !== null && (
          <g>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={PAD.top}
              y2={HEIGHT - PAD.bottom}
              className="cross"
            />
            <circle cx={x(hover)} cy={y(values[hover])} r={4} className="dot" />
          </g>
        )}
      </svg>
    </figure>
  );
}
