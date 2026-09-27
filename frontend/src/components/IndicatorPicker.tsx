import { useEffect } from "react";
import type { IndicatorName } from "../api";

/** One line somebody has asked for. */
export interface Pick {
  name: IndicatorName;
  length: number;
  /** A longer timeframe than the chart's, when the line should come from one. */
  tf?: string;
}

const CHOICES: { id: IndicatorName; name: string; length: number; period: boolean }[] = [
  { id: "ema", name: "EMA", length: 20, period: true },
  { id: "sma", name: "SMA", length: 50, period: true },
  { id: "rsi", name: "RSI", length: 14, period: true },
  { id: "atr", name: "ATR", length: 14, period: true },
  { id: "pivot_gap", name: "Pivot gap %", length: 0, period: false },
  { id: "pivot_gap_rank", name: "Pivot gap percentile", length: 60, period: true },
];

/** Where the choice is remembered, so a chart comes back the way it was left. */
const KEY = "optiondesk-chart-indicators";

export function remembered(): Pick[] {
  try {
    const saved = localStorage.getItem(KEY);
    if (!saved) return [];
    const parsed: unknown = JSON.parse(saved);
    if (!Array.isArray(parsed)) return [];
    // Validated rather than trusted: a shape stored by an older version would
    // otherwise be sent to the backend on every poll.
    return parsed.filter(
      (p): p is Pick =>
        typeof p === "object" &&
        p !== null &&
        CHOICES.some((c) => c.id === (p as Pick).name) &&
        typeof (p as Pick).length === "number",
    );
  } catch {
    return [];
  }
}

function remember(picks: Pick[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(picks));
  } catch {
    // forgetting which lines were on is not worth failing over
  }
}

/** The query the backend takes: "ema:20,ema:50:4h,rsi:14". */
export function asQuery(picks: Pick[]): string {
  return picks.map((p) => `${p.name}:${p.length}${p.tf ? `:${p.tf}` : ""}`).join(",");
}

/**
 * The control that opens the list, for the chart header.
 *
 * Separate from the menu because the header scrolls sideways when it runs out of
 * room, and anything absolutely positioned inside a scrolling box is clipped by
 * it - the first version opened and was invisible. So the button sits in the
 * header and the panel is drawn over the chart itself.
 */
export function IndicatorButton({
  count,
  open,
  onToggle,
}: {
  count: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      className={count || open ? "xbtn on" : "xbtn"}
      onClick={onToggle}
      title="Indicators on this chart"
      aria-expanded={open}
    >
      ƒ {count || ""}
    </button>
  );
}

interface MenuProps {
  picks: Pick[];
  onChange: (next: Pick[]) => void;
  /** Timeframes above the one being charted, which is all a line may read. */
  higher: readonly string[];
  onClose: () => void;
}

/**
 * Which indicators to draw.
 *
 * The lines themselves are computed by the backend, not here. That is not
 * laziness: a rule in the backtester reads its EMA through one piece of code, and
 * a chart that computed its own would eventually disagree with it - which is the
 * one thing a chart beside an order ticket must never do.
 */
export function IndicatorMenu({ picks, onChange, higher, onClose }: MenuProps) {
  useEffect(() => {
    remember(picks);
  }, [picks]);

  const add = (id: IndicatorName) => {
    const choice = CHOICES.find((c) => c.id === id);
    if (choice) onChange([...picks, { name: choice.id, length: choice.length }]);
  };

  const replace = (at: number, pick: Pick) =>
    onChange(picks.map((p, i) => (i === at ? pick : p)));

  return (
    <div className="indmenu">
      <header>
        <b>Indicators</b>
        <span className="sp" />
        <button className="drop" onClick={onClose} title="Done">
          ×
        </button>
      </header>

      <div className="rows">
        {picks.length === 0 && <p className="off">None on this chart yet.</p>}
        {picks.map((pick, i) => {
          const choice = CHOICES.find((c) => c.id === pick.name);
          return (
            <div className="indrow" key={`${pick.name}-${i}`}>
              <span className={`dot i${i % 5}`} aria-hidden="true" />
              <select
                value={pick.name}
                onChange={(e) => {
                  const name = e.target.value as IndicatorName;
                  const next = CHOICES.find((c) => c.id === name);
                  replace(i, { ...pick, name, length: next?.length ?? pick.length });
                }}
              >
                {CHOICES.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
              {choice?.period && (
                <input
                  className="num"
                  type="number"
                  min={1}
                  value={pick.length}
                  onChange={(e) =>
                    replace(i, { ...pick, length: Math.max(1, Number(e.target.value)) })
                  }
                />
              )}
              <select
                value={pick.tf ?? ""}
                onChange={(e) => replace(i, { ...pick, tf: e.target.value || undefined })}
                title="Read it on a longer timeframe than this chart's"
              >
                <option value="">this chart</option>
                {higher.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
              <button
                className="drop"
                onClick={() => onChange(picks.filter((_, k) => k !== i))}
                title="Remove"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      <div className="adds">
        {CHOICES.map((c) => (
          <button key={c.id} onClick={() => add(c.id)}>
            + {c.name}
          </button>
        ))}
      </div>

      <p className="say">
        Computed by the backend, through the same code the backtester reads them
        with — so a line here and a rule's line are the same number.
      </p>
    </div>
  );
}
