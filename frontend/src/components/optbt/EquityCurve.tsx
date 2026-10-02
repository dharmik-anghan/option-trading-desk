import { useState } from "react";
import { LineChart } from "../../charts/LineChart";
import { fromIsoPairs } from "../../charts/series";
import { day, rupees, signedRupees } from "../../format";

interface Props {
  /** [day, cumulative net] at each close. */
  equity: [string, number][];
}

const PAD = { top: 12, right: 12, bottom: 22, left: 64 };

/**
 * Cumulative net P&L, a point a day, against zero.
 *
 * Zero is always in view, so the line crossing it means the run was losing
 * money, and the drawdown from the running peak is shaded beneath - the part of
 * the curve a trader lives through, not just its end point. Hover reads a day.
 */
export function EquityCurve({ equity }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  if (equity.length < 2) return null;

  const peaks: number[] = [];
  for (const [, v] of equity) peaks.push(Math.max(peaks.at(-1) ?? v, v));
  const years = equity
    .map(([d], i) => ({ i, text: d.slice(0, 4) }))
    .filter((p, k, all) => k === 0 || p.text !== all[k - 1].text);
  const h = hover === null ? null : equity[hover];

  return (
    <figure className="ocurve">
      <figcaption>
        Equity
        {h && hover !== null && (
          <span className="readout">
            {day(h[0])} · <b>{signedRupees(h[1])}</b>
            {peaks[hover] > h[1] && ` · ${rupees(h[1] - peaks[hover])} from the peak`}
          </span>
        )}
      </figcaption>
      <LineChart
        points={fromIsoPairs(equity)}
        height={220}
        pad={PAD}
        baseline={0}
        underwater
        yAxis={rupees}
        xLabels={years}
        onHover={setHover}
        ariaLabel="Cumulative net profit and loss by day"
      />
    </figure>
  );
}
