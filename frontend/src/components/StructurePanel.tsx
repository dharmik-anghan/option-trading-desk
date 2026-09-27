import { useState } from "react";
import type { MarketStructure, StructureFrame } from "../api";
import { CandleChart } from "./CandleChart";
import type { Overlay } from "./CandleChart";
import { num } from "../format";

interface Props {
  structure: MarketStructure | null;
  error: Error | null;
  loading: boolean;
  k: number;
  onK: (k: number) => void;
  charted: string;
  onCharted: (interval: string) => void;
}

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
 */
export function StructurePanel({
  structure,
  error,
  loading,
  k,
  onK,
  charted,
  onCharted,
}: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const frame = structure?.frames.find((f) => f.interval === charted);

  return (
    <section className="panel a-structure">
      <div className="ph">
        <h2>Structure</h2>
        {structure && (
          <span className={`sub agree ${structure.agreement === "the sizes disagree" ? "mixed" : ""}`}>
            {structure.agreement}
          </span>
        )}
        <span className="sp" />
        {structure && (
          <span className="sub">
            last {structure.lookback} bars
          </span>
        )}
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
      </div>

      <div className="pb structurepb">
        {error && <p className="err">{error.message}</p>}
        {!structure && loading && <p className="empty">Reading the bars…</p>}

        {structure && (
          <>
            <div className="frames" role="tablist">
              {structure.frames.map((f) => (
                <button
                  key={f.interval}
                  role="tab"
                  aria-selected={f.interval === charted}
                  disabled={f.bars === 0}
                  className={`frame ${TONE[f.trend]}${f.interval === charted ? " on" : ""}`}
                  onClick={() => onCharted(f.interval)}
                  onMouseEnter={() => setHovered(f.interval)}
                  onMouseLeave={() => setHovered(null)}
                  title={
                    f.note || `${f.bars.toLocaleString()} bars — ${f.covers} · ${f.says}`
                  }
                >
                  <b>{f.interval}</b>
                  <span>{f.bars === 0 ? "—" : f.trend}</span>
                  <em>{f.bars === 0 ? "no bars" : f.says}</em>
                </button>
              ))}
            </div>

            {frame && frame.candles.length > 0 ? (
              <CandleChart
                candles={frame.candles.map((c) => ({ ...c, volume: 0 }))}
                seriesId={`${structure.underlying}-${charted}-${k}`}
                last={null}
                dp={1}
                height={300}
                overlay={overlayFor(frame)}
              />
            ) : (
              <p className="empty">
                {frame?.note || "No bars stored at this size."}
              </p>
            )}

            <Reading frame={structure.frames.find((f) => f.interval === (hovered ?? charted))} />

            {structure.caveats.map((c) => (
              <p className="volcaveat" key={c}>
                {c}
              </p>
            ))}
          </>
        )}
      </div>
    </section>
  );
}

/**
 * What goes on the chart.
 *
 * One line: the level price last broke, dotted, labelled BOS when the break
 * went with the structure and CHoCH when it went against it. Nothing else.
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
    levels: [
      {
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
