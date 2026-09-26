import { INDIA_VIX, UNDERLYINGS } from "../api";
import type { Quote } from "../api";
import { dir, num, pct } from "../format";

interface Props {
  symbol: string;
  onSymbol: (s: string) => void;
  quotes: Record<string, Quote> | null;
  error: Error | null;
}

/**
 * The watchlist, and the only place an underlying is chosen.
 *
 * India VIX sits at the bottom, below a rule: it is quoted like the rest but
 * has no option chain behind it, so it is not selectable. Putting it here
 * rather than in the chain's statistics keeps it visible whichever underlying
 * is in focus — it is a read on the whole market, not on one index.
 */
export function MarketWatch({ symbol, onSymbol, quotes, error }: Props) {
  const row = (id: string, name: string, selectable: boolean) => {
    const q = quotes?.[id];
    const chg = q ? q.ltp - q.prev_close : null;
    const on = selectable && id === symbol;
    return (
      <tr
        key={id}
        className={`${on ? "on" : ""} ${selectable ? "pick" : "novix"}`.trim()}
        onClick={selectable ? () => onSymbol(id) : undefined}
        tabIndex={selectable ? 0 : undefined}
        role={selectable ? "button" : undefined}
        aria-pressed={selectable ? on : undefined}
        onKeyDown={
          selectable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSymbol(id);
                }
              }
            : undefined
        }
      >
        <td className="l">{name}</td>
        <td>{q ? num(q.ltp) : "—"}</td>
        <td className={chg === null ? undefined : dir(chg)}>
          {q && chg !== null ? pct(chg / q.prev_close, 2) : "—"}
        </td>
      </tr>
    );
  };

  return (
    <section className="panel">
      <div className="ph">
        <h2>Market watch</h2>
        <span className="sp" />
        <span className="sub">pick one to trade</span>
      </div>
      {error && <p className="err">{error.message}</p>}
      <div className="pb">
        <table className="watch">
          <thead>
            <tr>
              <th className="l">Symbol</th>
              <th>LTP</th>
              <th>%Chg</th>
            </tr>
          </thead>
          <tbody>
            {UNDERLYINGS.map((u) => row(u.id, u.name, true))}
            {row(INDIA_VIX.id, INDIA_VIX.name, false)}
          </tbody>
        </table>
      </div>
    </section>
  );
}
