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

/** Leverage worth offering, coarsest first. Filtered to what the contract allows,
    because the venue's ceiling differs sharply per instrument: 150x on BTCUSDT,
    75x on gold, 50x on oil. Offered as a list rather than a free number so that
    choosing is picking from what exists rather than typing and being refused. */
const LEVERAGE_STEPS = [2, 3, 5, 10, 15, 20, 25, 50, 75, 100, 125, 150];

/**
 * An order, what it will cost to hold, and what the venue will accept.
 *
 * Three things the venue knows and you should not have to: the leverage ceiling
 * for this contract, the smallest order it will take, and what the position
 * demands as margin. All three are shown rather than discovered by being refused.
 *
 * Margin is notional over leverage, in the quote asset. It is the size of the
 * commitment rather than a quotation - the venue margins in rupees at a rate it
 * decides and adds a buffer, so the figure it charges will differ.
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
  const [leverage, setLeverage] = useState(10);
  const [limitPrice, setLimitPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PerpOrderResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  const instrument = instruments.find((i) => i.symbol === symbol);
  const live = prices[symbol] ?? null;
  const priceUsed = type === "LIMIT" ? Number(limitPrice) || null : live;
  const qty = Number(quantity);
  const lev = leverage;
  const notional = priceUsed !== null && qty > 0 ? qty * priceUsed : null;
  const margin = notional !== null && lev > 0 ? notional / lev : null;
  const ceiling = instrument?.max_leverage ?? 0;
  const steps = LEVERAGE_STEPS.filter((x) => ceiling <= 0 || x <= ceiling);
  const smallest = instrument?.min_quantity ?? 0;
  const tooSmall = qty > 0 && smallest > 0 && qty < smallest;

  const ready =
    qty > 0 &&
    !tooSmall &&
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
            placeholder={smallest > 0 ? String(smallest) : "0.000"}
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>
        {/* The floor, stated. It is usually a notional minimum rather than a
            quantity one, so it differs per instrument and moves with the price:
            BTCUSDT allows 0.001 but demands 115 USDT, which at 84,000 is 0.002,
            while oil needs 0.07. Better said here than discovered by a rejection. */}
        {smallest > 0 && (
          <p className={tooSmall ? "tnote bad" : "tnote"}>
            Smallest the venue takes: {smallest}
            {instrument?.min_notional ? ` (${instrument.min_notional} ${quoteCurrency} minimum)` : ""}
          </p>
        )}

        <label>
          <small>Leverage</small>
          <select value={leverage} onChange={(e) => setLeverage(Number(e.target.value))}>
            {steps.map((x) => (
              <option key={x} value={x}>
                {x}×
              </option>
            ))}
          </select>
        </label>
        {ceiling > 0 && (
          <p className="tnote">This contract allows up to {ceiling}×.</p>
        )}

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
        <div className="tnotional">
          <small>Margin</small>
          <b>
            {margin === null ? "—" : `${num(margin, 2)} ${quoteCurrency}`}
            <span className="dim"> at {lev}×</span>
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
          {/* The venue's own words when the venue decided. "Not sent" on its own
              is a dead end: the checks below may all have passed and the refusal
              have come from the exchange, which is a different problem with a
              different fix. */}
          <p className={result.sent ? "sent" : "err"}>
            {result.sent
              ? `Sent at ${lev}×. Reference ${result.venue_order_id ?? "—"}.`
              : result.reasons.length
                ? "Refused here. Nothing reached the venue."
                : `The venue refused it. ${result.outcome}`}
          </p>
          {/* Only what failed. A list of ticks saying a cap was not breached is
              read once and skipped forever, and a screen people skip is one that
              hides the line that matters. What passed is in the order log if it is
              ever needed. */}
          {result.reasons.length > 0 && (
            <table className="lim tight">
              <tbody>
                {result.checks
                  .filter((c) => !c.passed)
                  .map((c) => (
                    <tr key={c.reason}>
                      <td className="l">
                        <span className="bad">✕</span>
                      </td>
                      <td className="l">{c.reason}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
          <p className="dim" style={{ margin: 0, padding: "2px 9px 8px" }}>
            Recorded as #{result.record_id}.
          </p>
        </div>
      )}
    </section>
  );
}
