import { useState } from "react";
import type { PerpPosition } from "../api";
import { setProtection } from "../api";
import { dir, num, pct, signed } from "../format";

interface Props {
  positions: readonly PerpPosition[];
  error: string | null;
  /** What the prices are in. */
  quoteCurrency: string;
  /** What the margin is in, which is not the same on this venue. */
  moneyCurrency: string;
  /** Called after a stop is attached, so the list redraws with it. */
  onChanged: () => void;
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
export function PerpsPositions({
  positions,
  error,
  quoteCurrency,
  moneyCurrency,
  onChanged,
}: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [target, setTarget] = useState("");
  const [stop, setStop] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const open = (p: PerpPosition) => {
    setEditing(p.position_id);
    setTarget("");
    setStop("");
    setFailed(null);
  };

  const submit = async (p: PerpPosition) => {
    const tp = target.trim() ? Number(target) : null;
    const sl = stop.trim() ? Number(stop) : null;
    if (tp === null && sl === null) return;
    setBusy(true);
    setFailed(null);
    try {
      await setProtection(p.position_id, {
        quantity: p.quantity,
        take_profit: tp,
        stop_loss: sl,
      });
      setEditing(null);
      onChanged();
    } catch (e) {
      // Shown, never swallowed: believing a stop is attached when it is not is
      // worse than knowing there is none.
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

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
                <th>Stop</th>
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
                    <td>
                      {/* The first thing to know about a leveraged position, so
                          it is a column rather than something to go and check.
                          An exchange-held stop works with this app closed; an
                          unprotected position has nothing between it and the
                          market. */}
                      {p.protected ? (
                        <span className="prot on" title={`${p.stop_loss_orders} held by the venue`}>
                          held
                        </span>
                      ) : (
                        <button
                          className="xbtn danger"
                          onClick={() => open(p)}
                          title="Nothing is protecting this position"
                        >
                          none
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {editing !== null && (
        <div className="protform">
          <span className="dim">Held by the exchange, so it fires with this closed:</span>
          <input
            type="number"
            value={stop}
            placeholder="stop price"
            aria-label="Stop price"
            onChange={(e) => setStop(e.target.value)}
          />
          <input
            type="number"
            value={target}
            placeholder="target price"
            aria-label="Take-profit price"
            onChange={(e) => setTarget(e.target.value)}
          />
          <button
            className="xbtn"
            disabled={busy || (!stop.trim() && !target.trim())}
            onClick={() => {
              const p = positions.find((x) => x.position_id === editing);
              if (p) void submit(p);
            }}
          >
            {busy ? "Sending…" : "Attach"}
          </button>
          <button className="xbtn" disabled={busy} onClick={() => setEditing(null)}>
            Cancel
          </button>
        </div>
      )}
      {failed && <p className="err">Not attached: {failed}</p>}

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
