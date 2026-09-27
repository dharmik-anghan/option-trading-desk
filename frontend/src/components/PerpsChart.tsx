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

/** Timeframes worth having: intraday, a day's shape, and a week's. */
const FRAMES: Frame[] = [
  { label: "5m", interval: "5m", days: 1 },
  { label: "15m", interval: "15m", days: 2 },
  { label: "1h", interval: "1h", days: 5 },
  { label: "4h", interval: "4h", days: 20 },
  { label: "1d", interval: "1d", days: 120 },
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
