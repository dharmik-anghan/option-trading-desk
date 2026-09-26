import { useEffect, useRef, useState } from "react";
import type { PayoffPoint } from "../api";
import { compact, int, num, signed } from "../format";

interface Props {
  /** Payoff at expiry. Sparse vertices are fine — it is piecewise linear. */
  curve: PayoffPoint[];
  /** Mark-to-market now. Empty when the feed could not price it honestly. */
  todayCurve?: PayoffPoint[];
  /** Live underlying price. null when it isn't known for this underlying, in
      which case no marker is drawn — a marker in the wrong place is worse
      than none, because it silently misreads as "where the market is". */
  spot: number | null;
  breakevens: number[];
  height?: number;
  /** Stroke for the expiry line; defaults to the foreground ink. */
  color?: string;
  /** Tighter padding and smaller type for the card version. */
  compactMode?: boolean;
  daysToExpiry?: number | null;
  /** Show zoom controls. Off on the small card charts. */
  zoomable?: boolean;
}

const MAX_ZOOM = 12;

/**
 * The visible slice of a curve, with the cut ends interpolated rather than
 * dropped - otherwise a zoomed view would start and end mid-air.
 */
function clip(curve: PayoffPoint[], lo: number, hi: number): PayoffPoint[] {
  if (curve.length < 2) return curve;
  const out: PayoffPoint[] = [];
  const at = (x: number) => ({ spot: x, payoff: valueAt(curve, x) ?? 0 });
  if (curve[0].spot < lo) out.push(at(lo));
  for (const p of curve) if (p.spot > lo && p.spot < hi) out.push(p);
  if (curve[curve.length - 1].spot > hi) out.push(at(hi));
  return out.length >= 2 ? out : curve;
}

/** Linear read-off. Exact on the expiry curve, close enough on the sampled one. */
function valueAt(curve: PayoffPoint[], x: number): number | null {
  if (curve.length === 0) return null;
  if (x <= curve[0].spot) return curve[0].payoff;
  const last = curve[curve.length - 1];
  if (x >= last.spot) return last.payoff;
  for (let i = 1; i < curve.length; i++) {
    const a = curve[i - 1];
    const b = curve[i];
    if (x <= b.spot) {
      const span = b.spot - a.spot;
      if (span === 0) return b.payoff;
      return a.payoff + ((x - a.spot) / span) * (b.payoff - a.payoff);
    }
  }
  return last.payoff;
}

export function PayoffChart(props: Props) {
  const { height = 250, zoomable = false } = props;
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(e.contentRect.width);
    });
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="payoffwrap">
      {zoomable && (
        <div className="zoom">
          <button
            className="xbtn"
            onClick={() => setZoom((z) => Math.max(1, z / 1.6))}
            disabled={zoom <= 1}
            aria-label="Zoom out"
            title="Zoom out"
          >
            &minus;
          </button>
          <button
            className="xbtn"
            onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z * 1.6))}
            disabled={zoom >= MAX_ZOOM}
            aria-label="Zoom in"
            title="Zoom in"
          >
            +
          </button>
          <button className="xbtn" onClick={() => setZoom(1)} disabled={zoom === 1}>
            Fit
          </button>
          <span className="dim">{zoom === 1 ? "full range" : `${zoom.toFixed(1)}\u00d7`}</span>
        </div>
      )}
      <div className="payoff" ref={box} style={{ height }}>
        {w > 0 && props.curve.length > 1 && <Plot {...props} w={w} h={height} zoom={zoom} />}
      </div>
    </div>
  );
}

function Plot({
  curve: fullCurve,
  todayCurve: fullToday = [],
  spot,
  breakevens,
  color,
  compactMode = false,
  daysToExpiry,
  w,
  h,
  zoom,
}: Props & { w: number; h: number; zoom: number }) {
  const [hoverX, setHoverX] = useState<number | null>(null);

  // Zoom narrows the spot window around where the market actually is, and the
  // P&L axis is then rescaled to what remains - which is the point. Keeping
  // the original y-range would magnify the x-axis and leave the curve a flat
  // line across the middle.
  const fullXs = fullCurve.map((p) => p.spot);
  const fullLo = Math.min(...fullXs);
  const fullHi = Math.max(...fullXs);
  const centre = spot !== null && spot > fullLo && spot < fullHi ? spot : (fullLo + fullHi) / 2;
  const halfSpan = (fullHi - fullLo) / 2 / zoom;
  const lo = zoom === 1 ? fullLo : Math.max(fullLo, centre - halfSpan);
  const hi = zoom === 1 ? fullHi : Math.min(fullHi, centre + halfSpan);

  const curve = zoom === 1 ? fullCurve : clip(fullCurve, lo, hi);
  const todayCurve = zoom === 1 ? fullToday : clip(fullToday, lo, hi);

  const ys = [...curve.map((p) => p.payoff), ...todayCurve.map((p) => p.payoff)];
  let mn = Math.min(0, ...ys);
  let mx = Math.max(0, ...ys);
  const pad = (mx - mn) * 0.1 || 1;
  mn -= pad;
  mx += pad;

  const Lp = compactMode ? 42 : 54;
  const Rp = 8;
  const Tp = compactMode ? 8 : 12;
  const Bp = compactMode ? 15 : 20;
  const pw = Math.max(1, w - Lp - Rp);
  const ph = Math.max(1, h - Tp - Bp);

  const X = (v: number) => Lp + ((v - lo) / (hi - lo || 1)) * pw;
  const Y = (v: number) => Tp + ((mx - v) / (mx - mn || 1)) * ph;
  const path = (pts: PayoffPoint[]) =>
    pts.map((p, i) => `${i ? "L" : "M"}${X(p.spot).toFixed(1)},${Y(p.payoff).toFixed(1)}`).join("");

  const line = path(curve);
  const zero = Y(0);
  const area = `${line}L${X(hi).toFixed(1)},${zero.toFixed(1)}L${X(lo).toFixed(1)},${zero.toFixed(1)}Z`;
  const uid = `${Math.round(lo)}-${Math.round(h)}-${curve.length}`;
  const stroke = color ?? "var(--fg)";
  const inView = breakevens.filter((b) => b >= lo && b <= hi);

  // hover
  const hoverSpot =
    hoverX === null ? null : lo + ((hoverX - Lp) / pw) * (hi - lo);
  const clamped =
    hoverSpot === null ? null : Math.min(hi, Math.max(lo, hoverSpot));
  const atExpiry = clamped === null ? null : valueAt(curve, clamped);
  const atToday = clamped === null ? null : valueAt(todayCurve, clamped);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    // the svg scales to its box, so map client px back into viewBox units
    const x = ((e.clientX - r.left) / r.width) * w;
    setHoverX(x >= Lp && x <= Lp + pw ? x : null);
  };

  // keep the readout inside the plot
  const boxW = 128;
  const readoutX =
    clamped === null ? 0 : Math.min(Lp + pw - boxW, Math.max(Lp, X(clamped) + 8));

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label="Profit and loss against the underlying price, at expiry and now"
      onMouseMove={onMove}
      onMouseLeave={() => setHoverX(null)}
      style={{ cursor: compactMode ? "default" : "crosshair" }}
    >
      <defs>
        <clipPath id={`up-${uid}`}>
          <rect x="0" y="0" width={w} height={Math.max(0, zero)} />
        </clipPath>
        <clipPath id={`dn-${uid}`}>
          <rect x="0" y={zero} width={w} height={Math.max(0, h - zero)} />
        </clipPath>
      </defs>

      <path d={area} fill="var(--fu)" opacity="0.55" clipPath={`url(#up-${uid})`} />
      <path d={area} fill="var(--fd)" opacity="0.55" clipPath={`url(#dn-${uid})`} />

      <line x1={Lp} x2={Lp + pw} y1={zero} y2={zero} stroke="var(--line)" />

      {todayCurve.length > 1 && (
        <path
          d={path(todayCurve)}
          fill="none"
          stroke="var(--you)"
          strokeWidth="1.6"
          strokeDasharray="4 3"
        />
      )}
      <path d={line} fill="none" stroke={stroke} strokeWidth="2" />

      {spot !== null && (
        <line x1={X(spot)} x2={X(spot)} y1={Tp} y2={Tp + ph} stroke="var(--mkt)" strokeWidth="1.2" />
      )}

      {inView.map((b) => (
        <g key={b}>
          <circle cx={X(b)} cy={zero} r="3.5" fill="var(--panel)" stroke={stroke} strokeWidth="1.6" />
          <text
            x={X(b)}
            y={zero - 8}
            textAnchor="middle"
            fontSize={compactMode ? 9 : 10}
            fill="var(--dim)"
          >
            {int(b)}
          </text>
        </g>
      ))}

      {[
        [mx - pad, Y(mx - pad)],
        [0, zero],
        [mn + pad, Y(mn + pad)],
      ].map(([v, y], i) => (
        <text key={i} x={Lp - 5} y={y + 3.5} textAnchor="end" fontSize="10" fill="var(--dim)">
          {v === 0 ? "0" : compact(v)}
        </text>
      ))}

      <text x={Lp} y={h - 4} fontSize="10" fill="var(--dim)">
        {int(lo)}
      </text>
      {spot !== null && (
        <text x={X(spot)} y={h - 4} textAnchor="middle" fontSize="10" fill="var(--mkt)">
          {int(spot)}
        </text>
      )}
      <text x={Lp + pw} y={h - 4} textAnchor="end" fontSize="10" fill="var(--dim)">
        {int(hi)}
      </text>

      {!compactMode && clamped !== null && atExpiry !== null && (
        <g pointerEvents="none">
          <line
            x1={X(clamped)}
            x2={X(clamped)}
            y1={Tp}
            y2={Tp + ph}
            stroke="var(--dim)"
            strokeWidth="1"
            strokeDasharray="2 2"
          />
          <circle cx={X(clamped)} cy={Y(atExpiry)} r="3" fill={stroke} />
          {atToday !== null && <circle cx={X(clamped)} cy={Y(atToday)} r="3" fill="var(--you)" />}
          <rect
            x={readoutX}
            y={Tp + 2}
            width={boxW}
            height={atToday === null ? 34 : 48}
            rx="3"
            fill="var(--panel)"
            stroke="var(--line)"
          />
          <text x={readoutX + 7} y={Tp + 15} fontSize="10" fill="var(--dim)">
            at {int(clamped)}
          </text>
          <text x={readoutX + 7} y={Tp + 28} fontSize="11" fill={stroke}>
            expiry {signed(atExpiry)}
          </text>
          {atToday !== null && (
            <text x={readoutX + 7} y={Tp + 42} fontSize="11" fill="var(--you)">
              today {signed(atToday)}
            </text>
          )}
        </g>
      )}

      {!compactMode && clamped === null && daysToExpiry != null && (
        <text x={Lp + pw} y={Tp + 11} textAnchor="end" fontSize="10" fill="var(--dim)">
          {num(daysToExpiry, 1)} days to expiry
        </text>
      )}
    </svg>
  );
}

export function PayoffLegend({
  color,
  hasSpot = true,
  hasToday = false,
}: {
  color?: string;
  hasSpot?: boolean;
  hasToday?: boolean;
}) {
  return (
    <div className="legend">
      <span>
        <svg width="20" height="8">
          <line x1="0" y1="4" x2="20" y2="4" stroke={color ?? "var(--fg)"} strokeWidth="2" />
        </svg>
        At expiry
      </span>
      {hasToday && (
        <span>
          <svg width="20" height="8">
            <line
              x1="0"
              y1="4"
              x2="20"
              y2="4"
              stroke="var(--you)"
              strokeWidth="2"
              strokeDasharray="4 3"
            />
          </svg>
          If it happened today
        </span>
      )}
      {hasSpot && (
        <span>
          <svg width="8" height="10">
            <line x1="4" y1="0" x2="4" y2="10" stroke="var(--mkt)" strokeWidth="1.5" />
          </svg>
          Spot now
        </span>
      )}
      <span>
        <svg width="10" height="10">
          <circle cx="5" cy="5" r="3.5" fill="var(--panel)" stroke={color ?? "var(--fg)"} strokeWidth="1.6" />
        </svg>
        Breaks even
      </span>
    </div>
  );
}
