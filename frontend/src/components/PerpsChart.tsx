import { useMemo, useState } from "react";
import type { PerpsDesk as Desk } from "../api";
import { getPerpCandles } from "../api";
import { useLive } from "../useLive";
import { CandleChart } from "./CandleChart";
import { IndicatorButton, IndicatorMenu, asQuery, remembered } from "./IndicatorPicker";
import type { Pick } from "./IndicatorPicker";
import { Oscillator } from "./Oscillator";

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
const FRAMES: { label: string; resolution: string; days: number }[] = [
  { label: "5m", resolution: "5", days: 1 },
  { label: "15m", resolution: "15", days: 2 },
  { label: "1h", resolution: "60", days: 5 },
  { label: "4h", resolution: "240", days: 20 },
  { label: "1d", resolution: "D", days: 120 },
];

/**
 * The chart, and what the stream is doing.
 *
 * Named for what it is rather than for the desk it sits on: `PerpsDesk` is the
 * shape the API returns, and one name for both a component and a payload is how
 * an import quietly resolves to the wrong one.
 *
 * Candles are fetched on a timeframe change rather than on every tick: a bar does
 * not move until it closes, and the live price is drawn as a level on top, which
 * is the part that does move.
 */
export function PerpsChart({ desk, selected, last }: Props) {
  const [frame, setFrame] = useState(FRAMES[2]);
  // Remembered across reloads: an indicator set is a way of looking at a market
  // rather than a per-visit choice, and having to put the same two EMAs back on
  // every morning is the kind of small friction that stops a chart being used.
  const [picks, setPicks] = useState<Pick[]>(remembered);
  const [picking, setPicking] = useState(false);
  // What the candle chart is showing and where the cursor is, so the panels
  // underneath draw the same bars and the same moment.
  const [view, setView] = useState<{
    start: number;
    end: number;
    hovered: number | null;
  }>({ start: 0, end: 0, hovered: null });
  const indicators = useMemo(() => asQuery(picks), [picks]);

  // Only timeframes above this chart's, because a line cannot be read on a
  // shorter one than the bars it is drawn against.
  const higher = useMemo(() => {
    const here = FRAMES.findIndex((f) => f.label === frame.label);
    return FRAMES.slice(here + 1).map((f) => f.label);
  }, [frame.label]);

  // Through the same hook everything else polls with, rather than a hand-rolled
  // effect: it drops a response that arrived after its request was superseded,
  // which is what stops a slow 1d fetch repainting a chart the user has since
  // switched to 5m.
  //
  // Refetched on an interval as well as on a change of frame, so a new bar
  // appears without a reload. Cheap at this rate, and the live price line covers
  // everything that happens inside the current bar.
  const candles = useLive(
    () => getPerpCandles(selected, frame.resolution, frame.days, indicators),
    CANDLES_MS,
    [selected, frame.resolution, frame.days, indicators],
  );
  const rows = candles.data?.candles ?? [];
  const lines = candles.data?.lines ?? [];
  const onPrice = lines.filter((l) => l.on_price);
  const oscillators = lines.filter((l) => !l.on_price);

  const instrument = desk?.instruments.find((i) => i.symbol === selected);
  const dp = instrument?.price_dp ?? 2;

  return (
    <section className="panel a-chart">
      <div className="ph">
        <h2>{instrument?.name ?? selected}</h2>
        <span className="sub">
          {last === null ? "no price yet" : `${last.toFixed(dp)} ${desk?.quote_currency ?? ""}`}
        </span>
        <span className="sp" />
        <IndicatorButton
          count={picks.length}
          open={picking}
          onToggle={() => setPicking((p) => !p)}
        />
        <div className="frames" role="tablist" aria-label="Timeframe">
          {FRAMES.map((f) => (
            <button
              key={f.label}
              role="tab"
              aria-selected={f.label === frame.label}
              className={f.label === frame.label ? "xbtn on" : "xbtn"}
              onClick={() => setFrame(f)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="pb chartpb">
        {/* Over the chart rather than under the button: the header scrolls
            sideways when it runs out of room, and anything positioned inside a
            scrolling box is clipped by it. */}
        {picking && (
          <IndicatorMenu
            picks={picks}
            onChange={setPicks}
            higher={higher}
            onClose={() => setPicking(false)}
          />
        )}
        {candles.error && !rows.length && (
          <p className="err">Candles unavailable: {candles.error.message}</p>
        )}
        {candles.data?.note && !candles.error && (
          <p className="empty warnish">{candles.data.note}</p>
        )}
        {!candles.error && !rows.length && candles.loading && (
          <p className="empty">Loading candles…</p>
        )}
        {rows.length > 0 && (
          <CandleChart
            candles={rows}
            seriesId={`${selected}:${frame.resolution}`}
            last={last}
            dp={dp}
            overlay={{
              lines: onPrice.map((l) => ({ label: l.label, values: l.values })),
            }}
            onView={setView}
          />
        )}
        {rows.length > 0 &&
          oscillators.map((line) => (
            <Oscillator
              key={line.label}
              line={line}
              colour={lines.indexOf(line)}
              start={view.start}
              end={view.end}
              hovered={view.hovered}
            />
          ))}
      </div>

      <div className="chartfoot">
        {/* Whose price this is. The ticket beside this chart places an order at
            the venue, so a chart drawn from anywhere else would be the wrong
            instrument - Yahoo's gold sits 0.8% from this one. */}
        <span className="dim">
          {rows.length} bars · {frame.label}
          {candles.data?.source ? ` · ${candles.data.source}` : ""}
        </span>
        <span className="sp" />
        {desk && (
          <span className={desk.stream.connected ? "dim" : "streamdown"}>
            {desk.stream.connected
              ? `stream live · ${desk.stream.ticks.toLocaleString("en-IN")} ticks`
              : "stream down — prices are not updating"}
          </span>
        )}
      </div>
    </section>
  );
}
