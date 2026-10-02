import { PIVOT_ZONES } from "../../api";
import type { Explore } from "./explore";
import { DTE_ORDER, NO_FILTER, OPENED, VIX_ORDER, WEEKDAYS, isFiltered } from "./explore";

interface Props {
  value: Explore;
  onChange: (next: Explore) => void;
  /** Every way a trade ended in this run. */
  endings: string[];
  shown: number;
  total: number;
}

/** Filters over the trades already run. Nothing here runs the backtest again. */
export function FilterBar({ value: f, onChange, endings, shown, total }: Props) {
  const set = <K extends keyof Explore>(k: K, v: Explore[K]) => onChange({ ...f, [k]: v });
  const toggle = (list: string[], item: string) =>
    list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
  const picked = [...f.years, ...f.months];

  return (
    <div className="ob-filter" role="toolbar" aria-label="Filter trades">
      <div className="ob-seg days" aria-label="Weekday">
        {WEEKDAYS.map((d) => (
          <button
            key={d}
            className={f.weekdays.includes(d) ? "on" : ""}
            aria-pressed={f.weekdays.includes(d)}
            onClick={() => set("weekdays", toggle(f.weekdays, d))}
          >
            {d}
          </button>
        ))}
      </div>

      <Pick
        label="Expiry"
        value={f.expiry}
        onChange={(v) => set("expiry", v as Explore["expiry"])}
        options={[
          ["any", "Any day"],
          ["only", "Expiry day"],
          ["skip", "Not expiry day"],
        ]}
      />
      <Pick
        label="Days to expiry"
        value={f.dte}
        onChange={(v) => set("dte", v)}
        options={[["", "Any"], ...DTE_ORDER.filter((d) => d !== "?").map((d) => [d, d] as [string, string])]}
      />
      <Pick
        label="VIX percentile"
        value={f.vix}
        onChange={(v) => set("vix", v)}
        options={[
          ["", "Any"],
          ...VIX_ORDER.filter((b) => b !== "unknown").map((b) => [b, b] as [string, string]),
        ]}
      />
      <Pick
        label="Opened"
        value={f.opened}
        onChange={(v) => set("opened", v)}
        options={[
          ["", "Anywhere"],
          ...Object.entries(OPENED).map(([k, o]) => [k, o.label] as [string, string]),
          ...PIVOT_ZONES.map((z) => [z, z] as [string, string]),
        ]}
      />
      <Pick
        label="Result"
        value={f.outcome}
        onChange={(v) => set("outcome", v as Explore["outcome"])}
        options={[
          ["all", "All"],
          ["won", "Winners"],
          ["lost", "Losers"],
        ]}
      />
      <Pick
        label="Exit"
        value={f.ended}
        onChange={(v) => set("ended", v)}
        options={[["", "Any"], ...endings.map((e) => [e, e.replaceAll("+", " + ")] as [string, string])]}
      />

      {picked.length > 0 && (
        <span className="ob-chip">
          {picked.join(", ")}
          <button
            onClick={() => onChange({ ...f, months: [], years: [] })}
            aria-label="Clear the month filter"
          >
            ✕
          </button>
        </span>
      )}

      <span className="ob-count">
        {shown === total ? `${total.toLocaleString()} trades` : `${shown.toLocaleString()} of ${total.toLocaleString()}`}
      </span>
      {isFiltered(f) && (
        <button className="ob-reset" onClick={() => onChange(NO_FILTER)}>
          Reset
        </button>
      )}
    </div>
  );
}

function Pick({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  const active = value !== options[0][0];
  return (
    <label className={`ob-pick${active ? " on" : ""}`}>
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}
