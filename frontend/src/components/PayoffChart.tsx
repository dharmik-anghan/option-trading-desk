interface PayoffPoint {
  spot: number;
  payoff: number;
}

const WIDTH = 640;
const HEIGHT = 220;
const PAD = 32;

export function PayoffChart({
  points,
  breakevens,
  currentSpot,
}: {
  points: PayoffPoint[];
  breakevens: number[];
  currentSpot?: number;
}) {
  if (points.length === 0) {
    return <p className="empty-note">No open legs to chart (fully closed).</p>;
  }

  const xs = points.map((p) => p.spot);
  const ys = points.map((p) => p.payoff);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yAbsMax = Math.max(1, ...ys.map((y) => Math.abs(y))) * 1.15;
  const yMin = -yAbsMax;
  const yMax = yAbsMax;

  const plotW = WIDTH - PAD * 2;
  const plotH = HEIGHT - PAD * 2;

  const toX = (spot: number) =>
    PAD + ((spot - xMin) / (xMax - xMin || 1)) * plotW;
  const toY = (payoff: number) =>
    PAD + ((yMax - payoff) / (yMax - yMin || 1)) * plotH;
  const zeroY = toY(0);

  const screenPoints = points.map((p) => ({
    x: toX(p.spot),
    y: toY(p.payoff),
  }));
  const linePath = screenPoints
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`)
    .join(" ");

  // Each consecutive segment never changes sign mid-segment (breakevens are
  // already vertices in `points`, guaranteed server-side), so a segment's
  // midpoint sign tells us which color its area fill gets.
  const segments = points.slice(1).map((p, i) => {
    const prev = points[i];
    const mid = (prev.payoff + p.payoff) / 2;
    const x1 = toX(prev.spot);
    const x2 = toX(p.spot);
    const y1 = toY(prev.payoff);
    const y2 = toY(p.payoff);
    return {
      path: `M${x1},${zeroY} L${x1},${y1} L${x2},${y2} L${x2},${zeroY} Z`,
      positive: mid >= 0,
    };
  });

  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width="100%"
      height={HEIGHT}
      role="img"
      aria-label="Payoff at expiry"
    >
      {segments.map((seg, i) => (
        <path
          key={i}
          d={seg.path}
          fill={seg.positive ? "var(--profit)" : "var(--loss)"}
          fillOpacity={0.18}
        />
      ))}
      <line
        x1={PAD}
        x2={WIDTH - PAD}
        y1={zeroY}
        y2={zeroY}
        stroke="var(--border-strong)"
        strokeDasharray="3,3"
      />
      {breakevens.map((be) => (
        <g key={be}>
          <line
            x1={toX(be)}
            x2={toX(be)}
            y1={PAD}
            y2={HEIGHT - PAD}
            stroke="var(--accent-dim)"
            strokeDasharray="2,3"
          />
          <text
            x={toX(be)}
            y={HEIGHT - PAD + 14}
            textAnchor="middle"
            fontFamily="var(--font-mono)"
            fontSize="9"
            fill="var(--text-faint)"
          >
            {Math.round(be)}
          </text>
        </g>
      ))}
      {currentSpot !== undefined &&
        currentSpot >= xMin &&
        currentSpot <= xMax && (
          <line
            x1={toX(currentSpot)}
            x2={toX(currentSpot)}
            y1={PAD}
            y2={HEIGHT - PAD}
            stroke="var(--accent)"
            strokeWidth={1.5}
          />
        )}
      <path
        d={linePath}
        fill="none"
        stroke="var(--accent)"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      <text
        x={PAD}
        y={HEIGHT - 6}
        fontFamily="var(--font-mono)"
        fontSize="9"
        fill="var(--text-faint)"
      >
        {Math.round(xMin)}
      </text>
      <text
        x={WIDTH - PAD}
        y={HEIGHT - 6}
        textAnchor="end"
        fontFamily="var(--font-mono)"
        fontSize="9"
        fill="var(--text-faint)"
      >
        {Math.round(xMax)}
      </text>
    </svg>
  );
}
