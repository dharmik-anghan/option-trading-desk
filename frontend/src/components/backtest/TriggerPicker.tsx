import type { Trigger } from "../../api";

interface Props {
  value: Trigger | null;
  onChange: (next: Trigger | null) => void;
}

/**
 * When the conditions are a setup rather than a decision.
 *
 * The conditions above say a setup exists. They do not say to buy — and until
 * this existed the engine had to guess, which it did by always filling at the
 * next candle's open. "Enter if the next few candles break the high of the one
 * that fired" is a different strategy, usually a much more selective one, and
 * it was not expressible at all.
 *
 * A setup that is never confirmed costs nothing, which is the point: the result
 * reports how many armed against how many filled, and that ratio says as much
 * about a rule as its win rate does.
 */
export function TriggerPicker({ value, onChange }: Props) {
  return (
    <section className="trigger">
      <header>
        <h3>Enter</h3>
        <select
          value={value ? "break" : "open"}
          onChange={(e) =>
            onChange(
              e.target.value === "break"
                ? { kind: "break", field: "high", within: 3, ago: 0, buffer_bps: 0 }
                : null,
            )
          }
        >
          <option value="open">at the next candle's open</option>
          <option value="break">only if price breaks a level</option>
        </select>
      </header>

      {value && (
        <div className="row">
          <label>Break the</label>
          <select
            value={value.field}
            onChange={(e) => onChange({ ...value, field: e.target.value as Trigger["field"] })}
          >
            <option value="high">high</option>
            <option value="low">low</option>
            <option value="close">close</option>
            <option value="open">open</option>
          </select>
          <small>of the candle</small>
          <input
            className="num"
            type="number"
            min={0}
            value={value.ago ?? 0}
            onChange={(e) => onChange({ ...value, ago: Math.max(0, Number(e.target.value)) })}
            title="0 is the candle the conditions fired on"
          />
          <small>back, within</small>
          <input
            className="num"
            type="number"
            min={1}
            max={500}
            value={value.within}
            onChange={(e) =>
              onChange({ ...value, within: Math.max(1, Number(e.target.value)) })
            }
          />
          <small>candles, plus</small>
          <input
            className="num"
            type="number"
            min={0}
            step="1"
            value={value.buffer_bps ?? 0}
            onChange={(e) =>
              onChange({ ...value, buffer_bps: Math.max(0, Number(e.target.value)) })
            }
            title="A cushion past the level, in basis points. Always against you."
          />
          <small>bp</small>
        </div>
      )}

      <p className="say">
        {value
          ? `A long rests at the ${value.field}; a short mirrors it, so one strategy works both ways. The fill is that level — or the open, when a candle gaps straight through it.`
          : "The conditions are the decision, and the fill is the next candle's open."}
      </p>
    </section>
  );
}
