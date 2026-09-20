import type { PortfolioHistoryPoint } from "../api";
import { formatNumber } from "../format";

const WIDTH = 640;
const HEIGHT = 180;
const PAD_X = 8;
const LABEL_GAP = 22;
const BAR_RADIUS = 4;
const MAX_BAR_WIDTH = 24;

function roundedBarPath(
  x: number,
  y: number,
  width: number,
  height: number,
  roundTopEnd: boolean,
): string {
  const r = Math.max(0, Math.min(BAR_RADIUS, height, width / 2));
  if (height <= 0) return "";
  if (roundTopEnd) {
    return (
      `M${x},${y + height} L${x},${y + r} Q${x},${y} ${x + r},${y} ` +
      `L${x + width - r},${y} Q${x + width},${y} ${x + width},${y + r} ` +
      `L${x + width},${y + height} Z`
    );
  }
  return (
    `M${x},${y} L${x + width},${y} L${x + width},${y + height - r} ` +
    `Q${x + width},${y + height} ${x + width - r},${y + height} ` +
    `L${x + r},${y + height} Q${x},${y + height} ${x},${y + height - r} Z`
  );
}

function formatLabel(iso: string, showTimeOnly: boolean): string {
  const date = new Date(iso);
  return showTimeOnly
    ? date.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

export function PnlChart({ points }: { points: PortfolioHistoryPoint[] }) {
  if (points.length === 0) return null;

  const values = points.map((p) => p.total_pnl);
  const maxPos = Math.max(0, ...values);
  const maxNeg = Math.max(0, ...values.map((v) => -v));
  const range = maxPos + maxNeg || 1;
  const plotHeight = HEIGHT - LABEL_GAP;
  const baselineY = (plotHeight * maxPos) / range;

  const plotWidth = WIDTH - PAD_X * 2;
  const slot = plotWidth / points.length;
  const barWidth = Math.min(MAX_BAR_WIDTH, slot * 0.55);

  const sameDay = points.every(
    (p) => new Date(p.fetched_at).toDateString() === new Date(points[0].fetched_at).toDateString(),
  );
  const showLabels = points.length <= 10;

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width="100%"
      height={HEIGHT}
      role="img"
      aria-label="Total P&L per snapshot, bars above the line are profit, below are loss"
    >
      <line
        x1={PAD_X}
        x2={WIDTH - PAD_X}
        y1={baselineY}
        y2={baselineY}
        stroke="var(--border-strong)"
        strokeWidth={1}
      />
      {points.map((point, i) => {
        const value = point.total_pnl;
        const barHeight = (Math.abs(value) / range) * plotHeight;
        const x = PAD_X + slot * i + (slot - barWidth) / 2;
        const isPositive = value >= 0;
        const y = isPositive ? baselineY - barHeight : baselineY;
        const path = roundedBarPath(x, y, barWidth, barHeight, isPositive);
        const labelY = isPositive ? y - 6 : y + barHeight + 14;

        return (
          <g key={point.fetched_at}>
            <title>
              {new Date(point.fetched_at).toLocaleString("en-IN")}: {formatNumber(value)}
            </title>
            <path d={path} fill={isPositive ? "var(--profit)" : "var(--loss)"} />
            {showLabels && (
              <text
                x={x + barWidth / 2}
                y={labelY}
                textAnchor="middle"
                fontFamily="var(--font-mono)"
                fontSize="10"
                fill="var(--text-muted)"
              >
                {formatNumber(value)}
              </text>
            )}
            {showLabels && (
              <text
                x={x + barWidth / 2}
                y={HEIGHT - 4}
                textAnchor="middle"
                fontFamily="var(--font-mono)"
                fontSize="9"
                fill="var(--text-faint)"
              >
                {formatLabel(point.fetched_at, sameDay)}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
