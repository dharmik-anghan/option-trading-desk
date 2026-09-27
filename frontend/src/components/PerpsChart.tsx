import { useState } from "react";
import type { PerpsDesk as Desk } from "../api";
import { Chart } from "./Chart";
import type { Frame } from "./Chart";

interface Props {
  desk: Desk | null;
  selected: string;
  /** Live price for the selected instrument, so the chart agrees with the tile. */
  last: number | null;
}

//: How often the candle series is refetched, so a closed bar appears without a
//: reload. Two requests a minute against a 60-a-minute budget.
const CANDLES_MS = 30000;

/** Bars at every size, which is also the window a structure reading covers. */
const BARS = 180;

/**
 * Timeframes worth having on a market that never closes: intraday, a day's
 * shape, a week's, and a quarter's.
 *
 * Asked for in bars rather than days, at the same count everywhere. Days was
 * arbitrary — five of them is 120 hourly bars and 1,440 five-minute ones, so
 * the chart showed a different amount of history at every size for no reason.
 * A fixed bar count is also what makes the structure reading beside it
 * describe exactly the bars on screen.
 *
 * Weekly is here because there was no reason for it not to be. The venue serves
 * no weekly series, but a week is seven daily bars and the store has those, so
 * it is built from them — the same way the options desk gets four-hour bars.
 */
const FRAMES: Frame[] = [
  { label: "5m", interval: "5m", bars: BARS },
  { label: "15m", interval: "15m", bars: BARS },
  { label: "1h", interval: "1h", bars: BARS },
  { label: "4h", interval: "4h", bars: BARS },
  { label: "1d", interval: "1d", bars: BARS },
  { label: "1w", interval: "1w", bars: BARS },
];

/**
 * The perpetuals chart: the shared one, told which venue it is looking at.
 *
 * What is left here is what belongs to this desk and nowhere else — the
 * timeframes worth offering on a market that never closes, the instrument's own
 * quoting precision, and whether the tick stream is up. The chart itself is the
 * same component the options desk draws, which is the point: an order ticket
 * sits beside this one, and a chart that behaved differently from the one used
 * to read structure would be a trap.
 */
export function PerpsChart({ desk, selected, last }: Props) {
  const [frame, setFrame] = useState<Frame>(FRAMES[2]);
  const instrument = desk?.instruments.find((i) => i.symbol === selected);
  const dp = instrument?.price_dp ?? 2;

  return (
    <Chart
      title={instrument?.name ?? selected}
      sub={last === null ? "no price yet" : `${last.toFixed(dp)} ${desk?.quote_currency ?? ""}`}
      source="shark"
      symbol={selected}
      scope="perps"
      frames={FRAMES}
      frame={frame}
      onFrame={setFrame}
      last={last}
      dp={dp}
      bars={BARS}
      everyMs={CANDLES_MS}
      footer={
        /* Whether prices are moving. The ticket beside this chart places an
           order at the venue, so a stream that has quietly stopped is the one
           thing worth saying along the bottom. */
        desk ? (
          <span className={desk.stream.connected ? "dim" : "streamdown"}>
            {desk.stream.connected
              ? `stream live · ${desk.stream.ticks.toLocaleString("en-IN")} ticks`
              : "stream down — prices are not updating"}
          </span>
        ) : (
          <span className="dim" />
        )
      }
    />
  );
}
