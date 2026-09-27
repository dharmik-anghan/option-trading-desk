import type { IndicatorName, Operand } from "../../api";

interface Props {
  value: Operand;
  onChange: (next: Operand) => void;
  /** Timeframes offered, beyond the one being traded. */
  intervals: readonly string[];
  traded: string;
}

const INDICATORS = [
  { id: "ema", name: "EMA", period: "Period" },
  { id: "sma", name: "SMA", period: "Period" },
  { id: "rsi", name: "RSI", period: "Period" },
  { id: "atr", name: "ATR", period: "Period" },
  { id: "pivot_gap", name: "Pivot gap %", period: "" },
  {
    id: "pivot_gap_rank",
    name: "Pivot gap percentile",
    period: "How many previous periods to rank against",
  },
] as const;

/** Which indicators take no period at all. */
const NO_PERIOD = new Set(["pivot_gap"]);

const FIELDS = ["close", "open", "high", "low"] as const;
const LEVELS = ["S3", "S2", "S1", "P", "R1", "R2", "R3"] as const;

/**
 * One side of a comparison.
 *
 * Four kinds, chosen first, because what the rest of the row means depends on it:
 * an indicator needs a period, a price needs which of the four, a pivot needs a
 * level, and a number needs nothing else. Showing all of those at once and
 * greying out the irrelevant ones would be a wider control that says less.
 *
 * "Bars back" is the quiet one that earns its place. At zero it is the bar that
 * just closed; at one it is the one before, which is what "the previous candle's
 * low" means - the commonest stop there is.
 */
export function OperandPicker({ value, onChange, intervals, traded }: Props) {
  const kind = value.kind;

  const setKind = (next: Operand["kind"]) => {
    if (next === kind) return;
    // Sensible defaults per kind, rather than carrying fields that no longer mean
    // anything. Switching to a number and back should not resurrect a period.
    if (next === "indicator") onChange({ kind: "indicator", name: "ema", length: 9 });
    else if (next === "price") onChange({ kind: "price", field: "close" });
    else if (next === "pivot") onChange({ kind: "pivot", level: "P" });
    else onChange({ kind: "value", value: 0 });
  };

  const tf = "tf" in value ? (value.tf ?? "") : "";
  const ago = "ago" in value ? (value.ago ?? 0) : 0;

  return (
    <span className="operand">
      <select value={kind} onChange={(e) => setKind(e.target.value as Operand["kind"])}>
        <option value="indicator">Indicator</option>
        <option value="price">Price</option>
        <option value="pivot">Pivot</option>
        <option value="value">Number</option>
      </select>

      {kind === "indicator" && (
        <>
          <select
            value={value.name}
            onChange={(e) => {
              const picked = e.target.value as IndicatorName;
              // Sixty when switching to the percentile: a quarter's worth of
              // daily pivots. Carrying a 9 across from an EMA would rank today
              // against nine days, which is too short a window to call anything
              // a low.
              const length = picked === "pivot_gap_rank" ? 60 : value.length || 14;
              onChange({ ...value, name: picked, length });
            }}
          >
            {INDICATORS.map((i) => (
              <option key={i.id} value={i.id}>
                {i.name}
              </option>
            ))}
          </select>
          {!NO_PERIOD.has(value.name) && (
            <input
              className="num"
              type="number"
              min={1}
              value={value.length}
              onChange={(e) =>
                onChange({ ...value, length: Math.max(1, Number(e.target.value)) })
              }
              title={INDICATORS.find((i) => i.id === value.name)?.period ?? "Period"}
            />
          )}
        </>
      )}

      {kind === "price" && (
        <select
          value={value.field}
          onChange={(e) =>
            onChange({ ...value, field: e.target.value as "open" | "high" | "low" | "close" })
          }
        >
          {FIELDS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      )}

      {kind === "pivot" && (
        <select
          value={value.level}
          onChange={(e) => onChange({ ...value, level: e.target.value })}
        >
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      )}

      {kind === "value" && (
        <input
          className="num wide"
          type="number"
          value={value.value}
          onChange={(e) => onChange({ kind: "value", value: Number(e.target.value) })}
        />
      )}

      {kind !== "value" && (
        <>
          <select
            value={tf}
            onChange={(e) => onChange({ ...value, tf: e.target.value || undefined })}
            title="Which timeframe to read it on"
          >
            <option value="">{traded}</option>
            {intervals.map((i) => (
              <option key={i} value={i}>
                {i}
              </option>
            ))}
          </select>
          <input
            className="num"
            type="number"
            min={0}
            value={ago}
            onChange={(e) => onChange({ ...value, ago: Math.max(0, Number(e.target.value)) })}
            title="Bars back. 0 is the bar that just closed, 1 the one before it"
          />
        </>
      )}
    </span>
  );
}
