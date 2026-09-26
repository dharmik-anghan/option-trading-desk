import { useMemo } from "react";
import type { Candle } from "../api";

interface Props {
  candles: readonly Candle[];
  /** Live price, drawn as the current level so the chart agrees with the tile. */
  last: number | null;
  /** Decimal places the venue quotes in, so the axis invents no precision. */
  dp: number;
  height?: number;
}

const PAD = { top: 8, right: 54, bottom: 18, left: 6 };

/**
 * Candles, drawn as candles.
 *
 * A line would be smaller and less honest: the range within a bar is the part a
 * level-watcher cares about, because a wick through 24,000 is a test of 24,000
 * whether or not the bar closed there.
 *
 * Direction uses the same two hues as P&L. That is the one place the desk's
 * colour law bends, and deliberately: up and down mean the same thing to a
 * reader here as they do on a position, and inventing a third pair for price
 * would make the screen say that they are different ideas.
 */
export function CandleChart({ candles, last, dp, height = 260 }: Props) {
  const view = useMemo(() => {
    if (!candles.length) return null;
    const lows = candles.map((c) => c.low);
    const highs = candles.map((c) => c.high);
    let min = Math.min(...lows, last ?? Infinity);
    let max = Math.max(...highs, last ?? -Infinity);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    if (min === max) {
      // A flat window still needs a band, or every bar collapses onto one line.
      min -= Math.abs(min) * 0.001 || 1;
      max += Math.abs(max) * 0.001 || 1;
    }
    const pad = (max - min) * 0.06;
    return { min: min - pad, max: max + pad };
  }, [candles, last]);

  if (view === null) {
    return <p className="empty">No candles yet.</p>;
  }

  const W = 1000;
  const H = height;
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const y = (price: number) =>
    PAD.top + plotH - ((price - view.min) / (view.max - view.min)) * plotH;
  // Bars share the width; a gap of a fifth keeps them readable when there are
  // few, and disappears when there are many.
  const step = plotW / candles.length;
  const bodyW = Math.max(1, step * 0.8);

  // Four gridlines: enough to read a level off, few enough not to be a net.
  const ticks = [0, 1, 2, 3, 4].map((i) => view.min + ((view.max - view.min) * i) / 4);

  return (
    <svg className="candles" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Price candles">
      {ticks.map((price) => (
        <g key={price}>
          <line x1={PAD.left} x2={PAD.left + plotW} y1={y(price)} y2={y(price)} className="cgrid" />
          <text x={PAD.left + plotW + 5} y={y(price) + 3.5} className="caxis">
            {price.toFixed(dp)}
          </text>
        </g>
      ))}

      {candles.map((c, i) => {
        const cx = PAD.left + i * step + step / 2;
        const rising = c.close >= c.open;
        const top = y(Math.max(c.open, c.close));
        const bottom = y(Math.min(c.open, c.close));
        return (
          <g key={c.at} className={rising ? "up" : "dn"}>
            <line x1={cx} x2={cx} y1={y(c.high)} y2={y(c.low)} className="cwick" />
            <rect
              x={cx - bodyW / 2}
              y={top}
              width={bodyW}
              // A doji has no body; give it a hairline so the bar still exists.
              height={Math.max(1, bottom - top)}
              className="cbody"
            />
          </g>
        );
      })}

      {last !== null && (
        <g>
          <line
            x1={PAD.left}
            x2={PAD.left + plotW}
            y1={y(last)}
            y2={y(last)}
            className="clast"
          />
          <rect
            x={PAD.left + plotW + 1}
            y={y(last) - 8}
            width={PAD.right - 2}
            height={16}
            className="clastbg"
          />
          <text x={PAD.left + plotW + 5} y={y(last) + 3.5} className="clasttext">
            {last.toFixed(dp)}
          </text>
        </g>
      )}
    </svg>
  );
}
