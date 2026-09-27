import { useState } from "react";
import type { MarketStructure, StructureFrame } from "../api";
import type { Overlay } from "./CandleChart";
import { Chart } from "./Chart";
import type { Frame } from "./Chart";
import { num } from "../format";

interface Props {
  structure: MarketStructure | null;
  error: Error | null;
  loading: boolean;
  underlying: string;
  k: number;
  onK: (k: number) => void;
  charted: string;
  onCharted: (interval: string) => void;
}

//: Structure changes when a bar closes, and the smallest size shown is fifteen
//: minutes — so this is already several times faster than it can move.
const CANDLES_MS = 120000;

/** How each reading should feel. Up and down borrow the P&L pair; the two
    mixed states get the market hue, because neither side is winning. */
const TONE: Record<string, string> = {
  uptrend: "up",
  downtrend: "dn",
  broadening: "mixed",
  contracting: "mixed",
  unclear: "none",
};

/**
 * What the price has been doing, at every size at once.
 *
 * The sizes usually disagree, and that is the information rather than a fault
 * in it. On NIFTY as this was built: weekly uptrend, daily downtrend, four-hour
 * contracting, hourly downtrend, fifteen-minute uptrend. A panel showing one
 * timeframe would have reported whichever it happened to pick.
 *
 * `k` is how many bars either side a turn has to beat. It is adjustable because
 * it is the only real parameter here: two is about a swing a week on a daily
 * series, and larger means fewer and more significant turns — at the price of
 * waiting longer for any of them to be confirmed.
 *
 * The chart is the shared one, so this desk has the same indicators, the same
 * oscillator panes and the same crosshair as the perpetuals desk. The structure
 * is what this panel adds to it: the size buttons carry their own reading, and
 * the break is drawn on the price.
 */
export function StructurePanel({
  structure,
  error,
  loading,
  underlying,
  k,
  onK,
  charted,
  onCharted,
}: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const frame = structure?.frames.find((f) => f.interval === charted);

  const kInput = (
    <label className="kpick" title="Bars either side a turn has to beat">
      k
      <input
        type="number"
        min={1}
        max={20}
        value={k}
        onChange={(e) => onK(Math.min(20, Math.max(1, Number(e.target.value))))}
      />
    </label>
  );

  if (!structure) {
    return (
      <section className="panel a-structure">
        <div className="ph">
          <h2>Structure</h2>
          <span className="sp" />
          {kInput}
        </div>
        <div className="pb structurepb">
          {error && <p className="err">{error.message}</p>}
          {loading && !error && <p className="empty">Reading the bars…</p>}
        </div>
      </section>
    );
  }

  // The chart asks for the same bar count the reading was taken from. Two
  // windows would put the panel in the position of describing swings that are
  // not on the chart beside it, which is what it used to do.
  const frames: Frame[] = structure.frames.map((f) => ({
    label: f.interval,
    interval: f.interval,
    bars: structure.lookback,
    tone: TONE[f.trend],
    says: f.bars === 0 ? "—" : f.trend,
    detail: f.bars === 0 ? "no bars" : f.says,
    disabled: f.bars === 0,
    title: f.note || `${f.bars.toLocaleString()} bars — ${f.covers} · ${f.says}`,
  }));
  const current = frames.find((f) => f.interval === charted) ?? frames[0];

  return (
    <Chart
      className="a-structure"
      title="Structure"
      sub={
        <span className={`agree ${structure.agreement === "the sizes disagree" ? "mixed" : ""}`}>
          {structure.agreement}
        </span>
      }
      controls={
        <>
          <span className="sub">last {structure.lookback} bars</span>
          {kInput}
        </>
      }
      source="fyers"
      symbol={underlying}
      scope={`structure:${underlying}`}
      frames={frames}
      frame={current}
      onFrame={(f) => onCharted(f.interval)}
      frameStyle="tiles"
      dp={1}
      overlay={frame ? overlayFor(frame) : undefined}
      everyMs={CANDLES_MS}
      onHoverFrame={(f) => setHovered(f?.interval ?? null)}
    >
      <Reading frame={structure.frames.find((f) => f.interval === (hovered ?? charted))} />
      {structure.caveats.map((c) => (
        <p className="volcaveat" key={c}>
          {c}
        </p>
      ))}
    </Chart>
  );
}

/**
 * What this desk draws on the price, beyond the indicator lines.
 *
 * One line: the level price last broke, dotted, labelled BOS when the break
 * went with the structure and CHoCH when it went against it. Nothing else.
 *
 * Drawn from the swing that set the level to the bar that took it, not across
 * the whole chart. A break is a span between two moments; a full-width line
 * states the level at times before it existed and long after it was gone.
 *
 * The first version marked every swing with a dot and the last high and low
 * with their own lines. Both were wrong. The dots were borrowed from the
 * backtest chart, where a marker means a fill - so they were labelled "buy" and
 * "close" on a chart where nothing was ever bought, which is worse than
 * clutter. And the swing levels are already in the candles: a reader looking at
 * a structure chart can see where the highs and lows are, and drawing lines
 * through them says nothing the price had not already said.
 *
 * A break is different. It is the one thing on the chart that is a judgement
 * rather than an observation - the level price closed through, and whether that
 * continued the structure or cracked it.
 */
function overlayFor(frame: StructureFrame): Overlay {
  const br = frame.last_break;
  if (!br) return {};
  return {
    segments: [
      {
        from: br.from_at,
        to: br.at,
        price: br.level,
        label: `${br.continuation ? "BOS" : "CHoCH"} ${num(br.level, 0)}`,
        // Coloured by which way price went, not by whether the break continued
        // the structure. Green for a close above the level and red for below,
        // because on this desk those two hues mean direction and nothing else -
        // a downward break drawn green because it agreed with a downtrend was
        // the first version, and it read as good news.
        //
        // Whether it was continuation or a change of character is in the label,
        // which is where a judgement belongs rather than in a colour that
        // already means something.
        kind: br.price > br.level ? "target" : "stop",
      },
    ],
  };
}

function Reading({ frame }: { frame: StructureFrame | undefined }) {
  if (!frame || frame.bars === 0) return null;
  const br = frame.last_break;
  const provisional = frame.swings.filter((s) => !s.confirmed).length;
  return (
    <p className="reading-structure">
      <b>{frame.interval}</b> — {frame.says}, from the last {frame.bars.toLocaleString()} bars
      ({frame.covers}).
      {br && (
        <>
          {" "}
          Last break {br.continuation ? "went with" : "went against"} it, closing{" "}
          {br.price > br.level ? "above" : "below"} {num(br.level, 0)}.
        </>
      )}
      {provisional > 0 && (
        <> A turn is forming that the next bar can still take away.</>
      )}
    </p>
  );
}
