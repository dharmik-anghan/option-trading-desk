import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { StructureFrame } from "../api";
import { getChart, getStructure } from "../api";
import { num } from "../format";
import type { Overlay } from "./CandleChart";
import { useLive } from "../hooks/useLive";
import { CandleChart } from "./CandleChart";
import { IndicatorButton, IndicatorMenu, asQuery, remembered } from "./IndicatorPicker";
import type { Pick } from "./IndicatorPicker";

//: How often the structure reading is refetched. It changes when a bar closes,
//: and the smallest size any desk offers is five minutes.
const STRUCTURE_MS = 120000;

/** Bars each size is read over, when the caller names no window. */
const DEFAULT_LOOKBACK = 180;

/** Bars either side a turn has to beat. Two is about a swing a week on a daily
    series; larger means fewer and more significant turns, at the price of
    waiting longer for any of them to be confirmed. */
const DEFAULT_K = 2;

/** How each reading looks on its tab: a colour class and a mark. Up and down
    take the candles' colours; the two mixed states get the market hue, because
    neither side is winning. */
const TONE: Record<string, [string, string]> = {
  uptrend: ["rise", "▲"],
  downtrend: ["fall", "▼"],
  broadening: ["mixed", "◆"],
  contracting: ["mixed", "◆"],
  unclear: ["none", "–"],
};

const STRUCTURE_KEY = "optiondesk-chart-structure";

function rememberedStructure(scope: string): boolean {
  try {
    return localStorage.getItem(`${STRUCTURE_KEY}:${scope}`) === "on";
  } catch {
    return false;
  }
}

function rememberStructure(scope: string, on: boolean): void {
  try {
    localStorage.setItem(`${STRUCTURE_KEY}:${scope}`, on ? "on" : "off");
  } catch {
    // forgetting whether structure was on is not worth failing over
  }
}

/**
 * The breaks, which are what structure draws on the price.
 *
 * Each is the level price closed through, dotted, from the swing that set it to
 * the bar that took it — not across the whole chart. A break is a span between
 * two moments; a full-width line states the level at times before it existed
 * and long after it was gone.
 *
 * All of them, across the window, rather than the most recent one. Only the
 * last was drawn at first, and on a market in a clean trend — which takes out a
 * level every few bars — that meant a chart reading "HH + HL" with nothing on
 * it at all, because the latest swing high had not been broken *yet*. The
 * history of where the market gave way is the thing worth seeing.
 *
 * An earlier version marked every swing with a dot and drew the last high and
 * low as their own lines. Both were wrong. The dots were borrowed from the
 * backtest chart, where a marker means a fill, so they read as trades on a
 * chart where nothing was bought. And the swing levels are already in the
 * candles — drawing lines through the highs says nothing the price had not.
 *
 * A break is different: it is the one mark here that is a judgement rather than
 * an observation.
 */
function breakOf(frame: StructureFrame | undefined): NonNullable<Overlay["segments"]> {
  const all = frame?.breaks ?? [];
  return all.map((br, i) => ({
      from: br.from_at,
      to: br.at,
      price: br.level,
      // Only the newest carries its level in the label. Twenty of them with a
      // price each is a wall of digits over the candles, and the price is on
      // the axis anyway - what the label is for is saying which kind of break
      // this was.
      label: i === all.length - 1
        ? `${br.continuation ? "BOS" : "CHoCH"} ${num(br.level, 0)}`
        : br.continuation ? "BOS" : "CHoCH",
      // Coloured by which way price went, in the candles' own colours. Whether
      // it continued the structure or changed it is in the label, which is
      // where a judgement belongs rather than in a colour.
      kind: br.price > br.level ? ("rise" as const) : ("fall" as const),
  }));
}

function Reading({
  frame,
  lookback,
}: {
  frame: StructureFrame | undefined;
  lookback: number | undefined;
}) {
  if (!frame || frame.bars === 0) return null;
  const br = frame.breaks.at(-1);
  const provisional = frame.swings.filter((s) => !s.confirmed).length;
  return (
    <p className="reading-structure">
      <b>{frame.interval}</b> — {frame.says}, from the last{" "}
      {frame.bars.toLocaleString()} bars ({frame.covers})
      {lookback && frame.bars < lookback ? ", which is all that is stored" : ""}.
      {br && (
        <>
          {" "}
          Last break {br.continuation ? "went with" : "went against"} it, closing{" "}
          {br.price > br.level ? "above" : "below"} {num(br.level, 0)}.
        </>
      )}
      {provisional > 0 && <> A turn is forming that the next bar can still take away.</>}
    </p>
  );
}

/** How many sizes point each way, at the end of the timeframe row. */
function Tally({ frames }: { frames: readonly StructureFrame[] }) {
  const read = frames.filter((f) => f.bars > 0);
  const up = read.filter((f) => f.trend === "uptrend").length;
  const down = read.filter((f) => f.trend === "downtrend").length;
  const other = read.length - up - down;
  return (
    <span className="tally">
      <span className="rise">{up} up</span> · <span className="fall">{down} down</span>
      {other > 0 && <> · {other} mixed</>}
    </span>
  );
}

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
  /** Live price, drawn as the current level. Null where nothing streams. */
  last?: number | null;
  /** Decimal places the venue quotes in. */
  dp?: number;
  /** Drawn on top of the price, beyond the indicator lines. */
  overlay?: Overlay;
  /** How often to refetch, so a closed bar appears without a reload. Zero
      fetches once: a market that is shut has no new bars to show. */
  everyMs: number;
  /** Which chart's indicator set this is, so two desks remember their own. */
  scope: string;
  /** How many bars each size shows, which is also the window structure reads. */
  bars?: number;
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
  last = null,
  dp = 2,
  overlay,
  everyMs,
  scope,
  bars = 0,
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

  // Structure is a reading of the same bars, so it is a thing you switch on
  // over the chart rather than a different chart. Remembered like an indicator,
  // because it is one.
  const [structureOn, setStructureOn] = useState(() => rememberedStructure(scope));
  const [k, setK] = useState(DEFAULT_K);
  const [hoveredFrame, setHoveredFrame] = useState<string | null>(null);
  useEffect(() => setStructureOn(rememberedStructure(scope)), [scope]);
  useEffect(() => rememberStructure(scope, structureOn), [scope, structureOn]);

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
  // The sizes this chart offers, so the reading is about the buttons that are
  // actually there rather than a fixed five.
  const sizes = useMemo(() => frames.map((f) => f.interval), [frames]);
  const structure = useLive(
    () => getStructure(source, symbol, k, sizes, bars || DEFAULT_LOOKBACK),
    // A chart told not to refresh its candles has no new bars to read either.
    everyMs > 0 ? STRUCTURE_MS : 0,
    [source, symbol, k, sizes.join(","), bars],
    !structureOn,
    600,
  );
  const reading = structureOn ? (structure.data ?? null) : null;
  const byInterval = useMemo(() => {
    const m = new Map<string, StructureFrame>();
    for (const f of reading?.frames ?? []) m.set(f.interval, f);
    return m;
  }, [reading]);

  const rows = candles.data?.candles ?? [];
  const lines = useMemo(() => candles.data?.lines ?? [], [candles.data]);
  const onPrice = lines.filter((l) => l.on_price);
  const oscillators = useMemo(
    () => lines.flatMap((line, n) => (line.on_price ? [] : [{ line, colour: n }])),
    [lines],
  );

  // The indicator lines and whatever the desk draws on top, in one overlay.
  // Merged here rather than by the caller, so no desk has to know that the
  // lines it never asked for share a field with the levels it did.
  const drawn: Overlay = useMemo(
    () => {
      const here = byInterval.get(frame.interval);
      return {
        ...overlay,
        lines: onPrice.map((l) => ({ label: l.label, values: l.values })),
        segments: [...(overlay?.segments ?? []), ...breakOf(here)],
      };
    },
    // `onPrice` is rebuilt every render from `lines`; the response is what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [overlay, candles.data, byInterval, frame.interval],
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
        <button
          className={structureOn ? "xbtn on" : "xbtn"}
          aria-pressed={structureOn}
          onClick={() => setStructureOn((on) => !on)}
          title="Read higher highs and lower lows off these bars, at every size"
        >
          Structure
        </button>
        {structureOn && (
          <label className="kpick" title="Bars either side a turn has to beat">
            k
            <input
              type="number"
              min={1}
              max={20}
              value={k}
              onChange={(e) => setK(Math.min(20, Math.max(1, Number(e.target.value))))}
            />
          </label>
        )}
        {structureOn && reading && (
          <span className={`sub agree ${reading.agreement === "the sizes disagree" ? "mixed" : ""}`}>
            {reading.agreement}
          </span>
        )}
        {controls}
      </div>

      <div className="pb chartpb">
        <div className="frames" role="tablist" aria-label="Timeframe">
          {frames.map((f) => {
            const said = structureOn ? byInterval.get(f.interval) : undefined;
            const on = f.interval === frame.interval;
            const [tone, mark] = TONE[said && said.bars ? said.trend : "unclear"] ?? TONE.unclear;
            return (
              <button
                key={f.interval}
                role="tab"
                aria-selected={on}
                disabled={f.disabled}
                className={on ? "xbtn on" : "xbtn"}
                onClick={() => onFrame(f)}
                onMouseEnter={() => setHoveredFrame(f.interval)}
                onMouseLeave={() => setHoveredFrame(null)}
                title={
                  said
                    ? `${said.trend} · ${said.says} · ${said.bars.toLocaleString()} bars, ${said.covers}`
                    : f.title
                }
              >
                {f.label}
                {structureOn && <i className={`trend ${tone}`}>{mark}</i>}
              </button>
            );
          })}
          {structureOn && reading && <Tally frames={reading.frames} />}
        </div>

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
            oscillators={oscillators}
          />
        )}
        {structureOn && (
          <Reading frame={byInterval.get(hoveredFrame ?? frame.interval)} lookback={reading?.lookback} />
        )}
        {structureOn &&
          (reading?.caveats ?? []).map((c) => (
            <p className="volcaveat" key={c}>
              {c}
            </p>
          ))}
        {structureOn && structure.error && (
          <p className="chartnote">Structure unavailable: {structure.error.message}</p>
        )}
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
