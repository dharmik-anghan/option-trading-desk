import { dir, rupees, signedRupees } from "../../format";

interface Props {
  /** "YYYY-MM" -> net. */
  byMonth: Record<string, number>;
  /** Months and years picked, which the trade list is filtered to. */
  months?: string[];
  years?: string[];
  onMonth?: (month: string) => void;
  onYear?: (year: string) => void;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * Net by month, a year to a row.
 *
 * Diverging on the desk's profit and loss colours with nothing at zero: the
 * strength of a cell is its size against the largest month either way, so one
 * bad month stands out as bad rather than as merely coloured. Every cell also
 * prints its figure - colour is never the only way to read it.
 */
export function MonthGrid({ byMonth, months = [], years: pickedYears = [], onMonth, onYear }: Props) {
  const entries = Object.entries(byMonth);
  const years = [...new Set(entries.map(([m]) => m.slice(0, 4)))];
  const largest = Math.max(1, ...entries.map(([, v]) => Math.abs(v)));

  return (
    <table className="months">
      <caption>Net by month</caption>
      <thead>
        <tr>
          <th />
          {MONTHS.map((m) => (
            <th key={m}>{m}</th>
          ))}
          <th>Year</th>
        </tr>
      </thead>
      <tbody>
        {years.map((year) => {
          const total = entries
            .filter(([m]) => m.startsWith(year))
            .reduce((n, [, v]) => n + v, 0);
          return (
            <tr key={year}>
              <th
                className={`pick${pickedYears.includes(year) ? " picked" : ""}`}
                onClick={() => onYear?.(year)}
                title={`Show ${year}'s trades`}
              >
                {year}
              </th>
              {MONTHS.map((_, i) => {
                const key = `${year}-${String(i + 1).padStart(2, "0")}`;
                const v = byMonth[key];
                if (v === undefined) return <td key={key} className="none" />;
                // 12% to 55% strength: faint is still visible, strong still readable.
                const strength = 12 + Math.round((Math.abs(v) / largest) * 43);
                return (
                  <td
                    key={key}
                    className={`${dir(v)} pick${months.includes(key) ? " picked" : ""}`}
                    style={{ ["--s" as string]: `${strength}%` }}
                    title={`${MONTHS[i]} ${year}: ${signedRupees(v)} - click to see its trades`}
                    onClick={() => onMonth?.(key)}
                  >
                    {compact(v)}
                  </td>
                );
              })}
              <td className={`yr ${dir(total)}`} title={signedRupees(total)}>
                {total > 0 ? "+" : ""}
                {compact(total)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** 12,345 -> "12.3k", so a cell holds its figure. */
function compact(v: number): string {
  const a = Math.abs(v);
  const s = v < 0 ? "−" : "";
  if (a >= 1e5) return `${s}${(a / 1e5).toFixed(1)}L`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(1)}k`;
  return rupees(v).replace("₹", "");
}
