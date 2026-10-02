import { useState } from "react";
import type { OptbtTrade } from "../../api";
import { PIVOT_ZONES } from "../../api";
import type { Explore, Row } from "./explore";
import { DTE_ORDER, VIX_ORDER, WEEKDAYS, breakdown, dteBucket, vixBucket } from "./explore";
import { dir, signedRupees } from "../../format";

interface Props {
  trades: OptbtTrade[];
  filter: Explore;
  onFilter: (next: Explore) => void;
}

type Tab = "weekday" | "dte" | "vix" | "opened" | "exit";

const TABS: [Tab, string][] = [
  ["weekday", "Weekday"],
  ["dte", "Days to expiry"],
  ["vix", "VIX percentile"],
  ["opened", "Opened"],
  ["exit", "Exit"],
];

/** The trades cut one way at a time. Click a row to filter to it. */
export function Breakdowns({ trades, filter: f, onFilter }: Props) {
  const [tab, setTab] = useState<Tab>("weekday");
  const toggle = (list: string[], item: string) =>
    list.includes(item) ? list.filter((x) => x !== item) : [...list, item];

  const views: Record<Tab, { rows: Row[]; on: (k: string) => boolean; pick: (k: string) => void }> = {
    weekday: {
      rows: breakdown(trades, (t) => t.tags?.weekday ?? "?", WEEKDAYS),
      on: (k) => f.weekdays.includes(k),
      pick: (k) => onFilter({ ...f, weekdays: toggle(f.weekdays, k) }),
    },
    dte: {
      rows: breakdown(trades, dteBucket, DTE_ORDER),
      on: (k) => f.dte === k,
      pick: (k) => onFilter({ ...f, dte: f.dte === k ? "" : k }),
    },
    vix: {
      rows: breakdown(trades, vixBucket, VIX_ORDER),
      on: (k) => f.vix === k,
      pick: (k) => k !== "unknown" && onFilter({ ...f, vix: f.vix === k ? "" : k }),
    },
    opened: {
      rows: breakdown(trades, (t) => t.tags?.open_zone ?? "?", PIVOT_ZONES),
      on: (k) => f.opened === k,
      pick: (k) => onFilter({ ...f, opened: f.opened === k ? "" : k }),
    },
    exit: {
      rows: breakdown(trades, (t) => t.ended).sort((a, b) => b.trades - a.trades),
      on: (k) => f.ended === k,
      pick: (k) => onFilter({ ...f, ended: f.ended === k ? "" : k }),
    },
  };
  const view = views[tab];
  const largest = Math.max(1, ...view.rows.map((r) => Math.abs(r.net)));

  return (
    <section className="ob-break" aria-label="Breakdown">
      <div className="ob-tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "on" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <table>
        <thead>
          <tr>
            <th />
            <th className="n">Trades</th>
            <th className="n">Won</th>
            <th className="n">Net</th>
            <th className="n">Per trade</th>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => (
            <tr
              key={r.key}
              className={view.on(r.key) ? "on" : ""}
              onClick={() => view.pick(r.key)}
              tabIndex={0}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && view.pick(r.key)}
            >
              <th>{r.label.replaceAll("+", " + ")}</th>
              <td className="n">{r.trades}</td>
              <td className="n">{r.trades ? Math.round((r.wins / r.trades) * 100) : 0}%</td>
              <td className={`n bar ${dir(r.net)}`}>
                <i style={{ width: `${(Math.abs(r.net) / largest) * 100}%` }} />
                <span>{signedRupees(r.net)}</span>
              </td>
              <td className={`n ${dir(r.net)}`}>{signedRupees(r.trades ? r.net / r.trades : 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
