import { useState } from "react";
import type { PerpInstrument, PerpOrderResult } from "../api";
import { placePerpOrder } from "../api";
import { num } from "../format";

interface Props {
  instruments: readonly PerpInstrument[];
  /** Live price per symbol, for sizing and for showing the notional. */
  prices: Readonly<Record<string, number | null>>;
  symbol: string;
  quoteCurrency: string;
  onPlaced: () => void;
}

/**
 * An order, and what it would cost before you send it.
 *
 * Two deliberate frictions. Leverage has no default, because a default is a
 * decision about risk taken quietly and on a venue offering 150x the difference
 * between 8 and 80 is not a detail. And the notional is shown as you type rather
 * than after you commit, because "0.002" means nothing and "168 USDT" means
 * something.
 *
 * The result is shown in full, every check listed, whether it passed or not. A
 * green screen saying "done" teaches nothing about what was checked; the point of
 * a review is that you can see what the machine looked at.
 */
export function PerpsTicket({
  instruments,
  prices,
  symbol,
  quoteCurrency,
  onPlaced,
}: Props) {
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"MARKET" | "LIMIT">("MARKET");
  const [quantity, setQuantity] = useState("");
  const [leverage, setLeverage] = useState("");
  const [limitPrice, setLimitPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PerpOrderResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const instrument = instruments.find((i) => i.symbol === symbol);
  const live = prices[symbol] ?? null;
  const priceUsed = type === "LIMIT" ? Number(limitPrice) || null : live;
  const qty = Number(quantity);
  const lev = Number(leverage);
  const notional = priceUsed !== null && qty > 0 ? qty * priceUsed : null;

  const ready =
    qty > 0 &&
    lev > 0 &&
    priceUsed !== null &&
    (type === "MARKET" || Number(limitPrice) > 0);

  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    setFailed(null);
    setResult(null);
    try {
      const outcome = await placePerpOrder({
        symbol,
        side,
        order_type: type,
        quantity: qty,
        leverage: lev,
        limit_price: type === "LIMIT" ? Number(limitPrice) : null,
      });
      setResult(outcome);
      onPlaced();
    } catch (e) {
      setFailed(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel a-ticket">
      <div className="ph">
        <h2>Order</h2>
        <span className="sub">{instrument?.name ?? symbol}</span>
      </div>

      <div className="ticket">
        <div className="tside">
          {/* Buy and sell are the one pair the desk already colours, and a side
              chosen by mistake is the most expensive mistake available here. */}
          <button
            className={side === "BUY" ? "on buy" : undefined}
            aria-pressed={side === "BUY"}
            onClick={() => setSide("BUY")}
          >
            Buy / long
          </button>
          <button
            className={side === "SELL" ? "on sell" : undefined}
            aria-pressed={side === "SELL"}
            onClick={() => setSide("SELL")}
          >
            Sell / short
          </button>
        </div>

        <label>
          <small>Quantity</small>
          <input
            type="number"
            step={instrument ? 10 ** -instrument.quantity_dp : 0.001}
            value={quantity}
            placeholder="0.000"
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>

        <label>
          <small>Leverage</small>
          {/* No default: a default here is a decision about risk taken quietly. */}
          <input
            type="number"
            step={1}
            value={leverage}
            placeholder="required"
            onChange={(e) => setLeverage(e.target.value)}
          />
        </label>

        <label>
          <small>Type</small>
          <select value={type} onChange={(e) => setType(e.target.value as "MARKET" | "LIMIT")}>
            <option value="MARKET">Market</option>
            <option value="LIMIT">Limit</option>
          </select>
        </label>

        {type === "LIMIT" && (
          <label>
            <small>Limit price</small>
            <input
              type="number"
              value={limitPrice}
              placeholder={live === null ? "price" : num(live, instrument?.price_dp ?? 2)}
              onChange={(e) => setLimitPrice(e.target.value)}
            />
          </label>
        )}

        <div className="tnotional">
          <small>Worth</small>
          <b>
            {notional === null ? "—" : `${num(notional, 2)} ${quoteCurrency}`}
            {priceUsed !== null && (
              <span className="dim"> at {num(priceUsed, instrument?.price_dp ?? 2)}</span>
            )}
          </b>
        </div>

        <button className="xbtn place" disabled={!ready || busy} onClick={() => void submit()}>
          {busy ? "Checking…" : "Review and place"}
        </button>
      </div>

      {failed && <p className="err">{failed}</p>}

      {result && (
        <div className="outcome">
          {/* Held back and refused are different things, and a screen that blurs
              them teaches you to ignore it. */}
          <p className={result.sent ? "sent" : result.reasons.length ? "err" : "empty warnish"}>
            {result.sent
              ? `Sent. Venue reference ${result.venue_order_id ?? "—"}.`
              : result.reasons.length
                ? "Refused. Nothing was sent."
                : "Checks passed, and nothing was sent: the desk is in dry-run mode."}
          </p>
          <table className="lim tight">
            <tbody>
              {result.checks.map((c) => (
                <tr key={c.reason}>
                  <td className="l">
                    <span className={c.passed ? "ok" : "bad"}>{c.passed ? "✓" : "✕"}</span>
                  </td>
                  <td className="l">{c.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="dim" style={{ margin: 0, padding: "2px 9px 8px" }}>
            Recorded as #{result.record_id}, whether or not it was sent.
          </p>
        </div>
      )}
    </section>
  );
}
