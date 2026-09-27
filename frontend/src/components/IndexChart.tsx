import { useState } from "react";
import { Chart } from "./Chart";
import type { Frame } from "./Chart";

interface Props {
  /** The underlying being looked at, in the broker's spelling. */
  symbol: string;
  /** What to call it. */
  name: string;
  /** Spot, drawn as the current level so the chart agrees with the header. */
  last: number | null;
}

//: A closed bar is the only thing that moves this chart, and the smallest size
//: here is fifteen minutes.
const CANDLES_MS = 120000;

/** Bars at every size, which is also the window a structure reading covers. */
const BARS = 180;

/**
 * Sizes for an index.
 *
 * Nothing shorter than fifteen minutes: NSE bars are backfilled daily and at
 * fifteen minutes, and a five-minute chart would be the only one on the desk
 * with nothing behind it.
 *
 * The same count of bars at each size, which is the point — a hundred and
 * eighty weeks is three and a half years, a hundred and eighty days is nine
 * months, and a hundred and eighty fifteen-minute bars is about a week. Each is
 * a reasonable horizon for the size it belongs to.
 */
const FRAMES: Frame[] = [
  { label: "1w", interval: "1w", bars: BARS },
  { label: "1d", interval: "1d", bars: BARS },
  { label: "4h", interval: "4h", bars: BARS },
  { label: "1h", interval: "1h", bars: BARS },
  { label: "15m", interval: "15m", bars: BARS },
];

/**
 * The options desk's chart.
 *
 * It was called "Structure", which had it backwards: this is a chart, and
 * structure is one of the things that can be read off it — like an EMA, and
 * switched on the same way. Calling the panel after the feature meant the desk
 * appeared to have no chart at all, and that the feature could not exist
 * anywhere else. It now does: the perpetuals chart has the same switch.
 */
export function IndexChart({ symbol, name, last }: Props) {
  const [frame, setFrame] = useState<Frame>(FRAMES[1]);

  return (
    <Chart
      className="a-structure"
      title={name}
      sub={last === null ? "no price yet" : last.toFixed(2)}
      source="fyers"
      symbol={symbol}
      scope={`fyers:${symbol}`}
      frames={FRAMES}
      frame={frame}
      onFrame={setFrame}
      bars={BARS}
      last={last}
      dp={1}
      everyMs={CANDLES_MS}
    />
  );
}
