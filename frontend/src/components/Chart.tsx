import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { getChart } from "../api";
import type { Overlay } from "./CandleChart";
import { useLive } from "../useLive";
import { CandleChart } from "./CandleChart";
import { IndicatorButton, IndicatorMenu, asQuery, remembered } from "./IndicatorPicker";
import type { Pick } from "./IndicatorPicker";
import { Oscillator } from "./Oscillator";

/** One button on the timeframe row, and what it asks for. */
export interface Frame {
  /** What the button says. */
  label: string;
  /** The bar size, in the store's own names: "15m", "1h", "4h", "1d", "1w". */
  interval: string;
  /** Calendar days to read. Left out, the backend works it out from `bars`. */
  days?: number;
  /** Ask for the last N bars, for a chart that must show a named window. */
  bars?: number;
  /** A class on the button, for a desk that colours them by what they say. */
  tone?: string;
  /** A second and third line, for a button that is a reading rather than a tab. */
  says?: string;
  detail?: string;
  disabled?: boolean;
  title?: string;
}

interface Props {
  /** What the panel is called. */
  title: string;
  /** Beside the title: a price, a reading, whatever the desk wants said. */
  sub?: ReactNode;
  /** Extra controls in the header, after the indicator button. */
  controls?: ReactNode;
  /** Whose candles these are — the venue an order would be placed at. */
  source: string;
  symbol: string;
  frames: readonly Frame[];
  frame: Frame;
  onFrame: (f: Frame) => void;
  /** Which size the cursor is over, for a desk whose buttons are readings —
      hovering one should read it without moving the chart off the one being
      looked at, since comparison is the whole point of the row. */
  onHoverFrame?: (f: Frame | null) => void;
  /** Tabs read as a row of sizes; tiles carry a reading on each button. */
  frameStyle?: "tabs" | "tiles";
  /** Live price, drawn as the current level. Null where nothing streams. */
  last?: number | null;
  /** Decimal places the venue quotes in. */
  dp?: number;
  /** Drawn on top of the price, beyond the indicator lines. */
  overlay?: Overlay;
  /** How often to refetch, so a closed bar appears without a reload. */
  everyMs: number;
  /** Which chart's indicator set this is, so two desks remember their own. */
  scope: string;
  /** Under the chart: a reading, a caveat, whatever belongs to the desk. */
  children?: ReactNode;
  /** The strip along the bottom. */
  footer?: ReactNode;
  className?: string;
}

/**
 * The chart, for every desk.
 *
 * There were two of these. The perpetuals desk had a chart with timeframes,
 * indicators and oscillator panes; the options desk had candles handed to it
 * inside a structure reading, with none of that. Neither could gain what the
 * other had without the work being done twice, and they drifted — which on a
 * screen you trade from is worse than either being plain, because two charts
 * that look alike and behave differently are a trap.
 *
 * So the differences between the desks are the arguments: which sizes the
 * buttons offer, what is drawn on top, and what is said underneath. Everything
 * else — fetching, panning, the crosshair, the indicator menu, the oscillator
 * panes beneath — happens once, here.
 */
export function Chart({
  title,
  sub,
  controls,
  source,
  symbol,
  frames,
  frame,
  onFrame,
  onHoverFrame,
  frameStyle = "tabs",
  last = null,
  dp = 2,
  overlay,
  everyMs,
  scope,
  children,
  footer,
  className = "a-chart",
}: Props) {
  // Remembered across reloads, and per chart: an indicator set is a way of
  // looking at a market rather than a per-visit choice, and having to put the
  // same two EMAs back on every morning is the kind of small friction that
  // stops a chart being used.
  const [picks, setPicks] = useState<Pick[]>(() => remembered(scope));
  const [picking, setPicking] = useState(false);
  useEffect(() => setPicks(remembered(scope)), [scope]);

  // What the candle chart is showing and where the cursor is, so the panes
  // underneath draw the same bars and the same moment.
  const [view, setView] = useState<{ start: number; end: number; hovered: number | null }>({
    start: 0,
    end: 0,
    hovered: null,
  });
  const indicators = useMemo(() => asQuery(picks), [picks]);

  // Only timeframes above this chart's, because a line cannot be read on a
  // shorter one than the bars it is drawn against.
  const higher = useMemo(() => {
    const here = frames.findIndex((f) => f.interval === frame.interval);
    return frames.slice(here + 1).map((f) => f.interval);
  }, [frames, frame.interval]);

  // Through the same hook everything else polls with, rather than a hand-rolled
  // effect: it drops a response that arrived after its request was superseded,
  // which is what stops a slow daily fetch repainting a chart the user has
  // since switched to five minutes.
  const candles = useLive(
    () =>
      getChart(source, symbol, {
        interval: frame.interval,
        days: frame.days,
        bars: frame.bars,
        indicators,
      }),
    everyMs,
    [source, symbol, frame.interval, frame.days, frame.bars, indicators],
  );
  const rows = candles.data?.candles ?? [];
  const lines = candles.data?.lines ?? [];
  const onPrice = lines.filter((l) => l.on_price);
  const oscillators = lines.filter((l) => !l.on_price);

  // The indicator lines and whatever the desk draws on top, in one overlay.
  // Merged here rather than by the caller, so no desk has to know that the
  // lines it never asked for share a field with the levels it did.
  const drawn: Overlay = useMemo(
    () => ({ ...overlay, lines: onPrice.map((l) => ({ label: l.label, values: l.values })) }),
    // `onPrice` is rebuilt every render from `lines`; the response is what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [overlay, candles.data],
  );

  return (
    <section className={`panel ${className}`}>
      <div className="ph">
        <h2>{title}</h2>
        {sub !== undefined && <span className="sub">{sub}</span>}
        <span className="sp" />
        <IndicatorButton
          count={picks.length}
          open={picking}
          onToggle={() => setPicking((p) => !p)}
        />
        {controls}
      </div>

      <div className="pb chartpb">
        {frameStyle === "tiles" ? (
          <div className="frames" role="tablist">
            {frames.map((f) => (
              <button
                key={f.interval}
                role="tab"
                aria-selected={f.interval === frame.interval}
                disabled={f.disabled}
                className={`frame ${f.tone ?? ""}${f.interval === frame.interval ? " on" : ""}`}
                onClick={() => onFrame(f)}
                onMouseEnter={() => onHoverFrame?.(f)}
                onMouseLeave={() => onHoverFrame?.(null)}
                title={f.title}
              >
                <b>{f.label}</b>
                <span>{f.says}</span>
                <em>{f.detail}</em>
              </button>
            ))}
          </div>
        ) : (
          <div className="frames" role="tablist" aria-label="Timeframe">
            {frames.map((f) => (
              <button
                key={f.interval}
                role="tab"
                aria-selected={f.interval === frame.interval}
                disabled={f.disabled}
                className={f.interval === frame.interval ? "xbtn on" : "xbtn"}
                onClick={() => onFrame(f)}
                title={f.title}
              >
                {f.label}
              </button>
            ))}
          </div>
        )}

        {/* Over the chart rather than under the button: the header scrolls
            sideways when it runs out of room, and anything positioned inside a
            scrolling box is clipped by it. */}
        {picking && (
          <IndicatorMenu
            picks={picks}
            onChange={setPicks}
            higher={higher}
            scope={scope}
            onClose={() => setPicking(false)}
          />
        )}
        {candles.error && !rows.length && (
          <p className="err">Candles unavailable: {candles.error.message}</p>
        )}
        {/* A note about the series. Loud only when there is nothing to draw:
            "built from 15m bars" is how three of the five structure sizes are
            made, and a banner saying so on every one of them is noise that
            teaches a reader to stop reading banners. */}
        {candles.data?.note && !candles.error && (
          <p className={rows.length ? "chartnote" : "empty warnish"}>{candles.data.note}</p>
        )}
        {!candles.error && !rows.length && candles.loading && (
          <p className="empty">Loading candles…</p>
        )}
        {rows.length > 0 && (
          <CandleChart
            candles={rows}
            seriesId={`${source}:${symbol}:${frame.interval}`}
            last={last}
            dp={dp}
            overlay={drawn}
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
        {children}
      </div>

      {footer && (
        <div className="chartfoot">
          <span className="dim">
            {rows.length} bars · {frame.label}
            {candles.data?.source ? ` · ${candles.data.source}` : ""}
          </span>
          <span className="sp" />
          {footer}
        </div>
      )}
    </section>
  );
}
