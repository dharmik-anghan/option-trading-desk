import type { PerpPosition } from "../api";
import { dir, num, pct, signed } from "../format";

interface Props {
  positions: readonly PerpPosition[];
  error: string | null;
  /** What the prices are in. */
  quoteCurrency: string;
  /** What the margin is in, which is not the same on this venue. */
  moneyCurrency: string;
}

/** Below this, liquidation is close enough to say so loudly. */
const CLOSE_TO_LIQUIDATION = 0.1;

/**
 * What is open on the perpetuals desk.
 *
 * Leverage changes what a position list is for. On the options desk the question
 * is what a structure is worth; here the first question is how far this is from
 * being closed out for you, because at 75× a one-percent move is most of the
 * margin. So liquidation distance is a column rather than something to work out,
 * and it is a percentage of price so that gold at 4,300 and Bitcoin at 84,000 can
 * be read side by side.
 */
export function PerpsPositions({ positions, error, quoteCurrency, moneyCurrency }: Props) {
  return (
    <section className="panel a-positions">
      <div className="ph">
        <h2>Open positions</h2>
        <span className="sub">
          {positions.length ? `${positions.length} open` : "none open"}
        </span>
      </div>

      <div className="pb">
        {/* An empty list because a request failed looks exactly like an empty
            list because nothing is open, and those are very different here. */}
        {error && <p className="err">Positions unavailable: {error}</p>}
        {!error && !positions.length && (
          <p className="empty">
            Nothing open. A position taken on the venue appears here on the next
            refresh.
          </p>
        )}

        {positions.length > 0 && (
          <table>
            <thead>
              <tr>
                <th className="l">Position</th>
                <th>Size</th>
                <th>Entry</th>
                <th>Now</th>
                <th>P&amp;L</th>
                <th>To liq.</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const near =
                  p.liquidation_distance !== null &&
                  p.liquidation_distance < CLOSE_TO_LIQUIDATION;
                return (
                  <tr key={p.position_id || p.symbol}>
                    <td className="l">
                      {/* Direction in words, not a sign: "short 0.01" is read
                          faster than "-0.01" and cannot be mistaken for a loss. */}
                      <span className={p.side === "LONG" ? "pside long" : "pside short"}>
                        {p.side === "LONG" ? "Long" : "Short"}
                      </span>{" "}
                      {p.name}
                      <span className="dim"> {num(p.leverage, 0)}× {p.margin_type.toLowerCase()}</span>
                    </td>
                    <td>{num(p.quantity, 3)}</td>
                    <td className="dim">{num(p.entry_price, 2)}</td>
                    <td>{p.price === null ? "—" : num(p.price, 2)}</td>
                    <td
                      className={p.unrealized_pnl === null ? undefined : dir(p.unrealized_pnl)}
                      title={
                        p.pnl_is_ours
                          ? `Worked out from the price — the venue reported none. In ${quoteCurrency}.`
                          : `As the venue reports it, in ${quoteCurrency}.`
                      }
                    >
                      {p.unrealized_pnl === null ? "—" : signed(p.unrealized_pnl, 2)}
                      {p.pnl_is_ours && <span className="dim">*</span>}
                    </td>
                    <td className={near ? "dn" : undefined} title={
                      p.liquidation_price === null
                        ? "The venue reported no liquidation price"
                        : `Liquidates at ${num(p.liquidation_price, 2)}`
                    }>
                      {p.liquidation_distance === null
                        ? "—"
                        : pct(p.liquidation_distance, 1).replace("+", "")}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {positions.length > 0 && (
        <p className="dim" style={{ margin: 0, padding: "4px 9px 8px", lineHeight: 1.4 }}>
          Prices and P&amp;L in {quoteCurrency}; margin in {moneyCurrency}. An
          asterisk marks a P&amp;L we worked out rather than one the venue
          reported.
        </p>
      )}
    </section>
  );
}
