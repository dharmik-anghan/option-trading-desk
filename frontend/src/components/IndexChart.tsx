import { useEffect, useMemo, useState } from "react";
import { getFuturesOi, getOptionChain } from "../api";
import type { MarketContext, OptionChain } from "../api";
import type { OiRow } from "../charts/oiProfile";
import { useLive } from "../hooks/useLive";
import type { Overlay } from "./CandleChart";
import { Chart } from "./Chart";
import type { Frame } from "./Chart";

interface Props {
  /** The underlying being looked at, in the broker's spelling. */
  symbol: string;
  /** What to call it. */
  name: string;
  /** Spot, drawn as the current level so the chart agrees with the header. */
  last: number | null;
  /** The desk's reading of the nearest expiry: where its walls and max pain are. */
  walls: MarketContext | null;
  /** Whether the NSE is trading. Closed, the chart loads once and stops polling. */
  live: boolean;
}

//: A closed bar is the only thing that moves this chart, and the smallest size
//: here is fifteen minutes.
const CANDLES_MS = 120000;

//: OI moves through the session but not by the second; once a minute is
//: plenty, and the chain is the heaviest call the desk makes.
const OI_MS = 60000;

/** Calendar days of futures OI to read for each size: enough to cover the
    chart's bars, which are a fixed count per size. */
const OI_DAYS: Record<string, number> = { "15m": 14, "1h": 45, "4h": 150, "1d": 280, "1w": 1300 };

/** Strikes either side of the money for the profile. */
const OI_STRIKES = 20;

const OI_KEY = "optiondesk-chart-oi";

function rememberedOi(symbol: string): boolean {
  try {
    return localStorage.getItem(`${OI_KEY}:${symbol}`) === "on";
  } catch {
    return false;
  }
}

function rememberOi(symbol: string, on: boolean): void {
  try {
    localStorage.setItem(`${OI_KEY}:${symbol}`, on ? "on" : "off");
  } catch {
    // forgetting whether OI was on is not worth failing over
  }
}

/** The chain folded into one row per strike, calls and puts side by side. */
function profileOf(chain: OptionChain | null): OiRow[] {
  const byStrike = new Map<number, OiRow>();
  for (const r of chain?.rows ?? []) {
    const row = byStrike.get(r.strike) ?? { strike: r.strike, call: 0, callPrev: 0, put: 0, putPrev: 0 };
    if (r.option_type === "CE") {
      row.call = r.oi;
      row.callPrev = r.prev_oi;
    } else {
      row.put = r.oi;
      row.putPrev = r.prev_oi;
    }
    byStrike.set(r.strike, row);
  }
  return [...byStrike.values()].sort((a, b) => a.strike - b.strike);
}

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
export function IndexChart({ symbol, name, last, walls, live }: Props) {
  const [frame, setFrame] = useState<Frame>(FRAMES[1]);

  // Open interest over the price: the walls as lines, and every strike as a
  // profile against the axis. Remembered per underlying, like structure.
  const [oiOn, setOiOn] = useState(() => rememberedOi(symbol));
  useEffect(() => setOiOn(rememberedOi(symbol)), [symbol]);
  useEffect(() => rememberOi(symbol, oiOn), [symbol, oiOn]);
  const chain = useLive(
    () => getOptionChain(symbol, OI_STRIKES),
    live ? OI_MS : 0,
    [symbol],
    !oiOn,
    400,
  );

  // Futures OI across the near, next and far months, bar by bar, for the
  // buildup pane under the candles. The near month is the one the strip shows.
  const future = walls?.futures_symbol ?? null;
  const futuresOi = useLive(
    () => getFuturesOi(future ?? "", frame.interval, OI_DAYS[frame.interval] ?? 280),
    live ? OI_MS : 0,
    [future, frame.interval],
    !oiOn || future === null,
    700,
  );

  // Taken out as numbers, so a context refresh that moved no wall does not
  // rebuild the chart.
  const resistance = walls?.resistance ?? null;
  const support = walls?.support ?? null;
  const maxPain = walls?.max_pain ?? null;
  // Short titles: the line's price is on the axis, and a long one would sit
  // across the profile.
  const overlay = useMemo((): Overlay | undefined => {
    if (!oiOn) return undefined;
    const levels: NonNullable<Overlay["levels"]> = [];
    if (resistance !== null) {
      levels.push({ price: resistance, label: "R", kind: "wall" });
    }
    if (support !== null) {
      levels.push({ price: support, label: "S", kind: "wall" });
    }
    if (maxPain !== null) {
      levels.push({ price: maxPain, label: "MP", kind: "wall" });
    }
    return { levels, profile: profileOf(chain.data), futuresOi: futuresOi.data ?? undefined };
  }, [oiOn, resistance, support, maxPain, chain.data, futuresOi.data]);

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
      everyMs={live ? CANDLES_MS : 0}
      overlay={overlay}
      controls={
        <button
          className={oiOn ? "xbtn on" : "xbtn"}
          aria-pressed={oiOn}
          onClick={() => setOiOn((on) => !on)}
          title="Open interest by strike for the nearest expiry, with its walls and max pain"
        >
          OI
        </button>
      }
    />
  );
}
