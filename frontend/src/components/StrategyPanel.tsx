import { useState } from "react";
import { getStrategySignal, placeOrder, type StrategySignalResponse } from "../api";

const STRATEGIES = [
  "short_strangle",
  "iron_condor",
  "credit_spread_bullish",
  "credit_spread_bearish",
];

export function StrategyPanel() {
  const [strategy, setStrategy] = useState(STRATEGIES[1]);
  const [symbol, setSymbol] = useState("NSE:NIFTY50-INDEX");
  const [quantity, setQuantity] = useState(1);
  const [signal, setSignal] = useState<StrategySignalResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [placed, setPlaced] = useState<string[] | null>(null);

  function handlePreview() {
    setLoading(true);
    setError(null);
    setPlaced(null);
    setPlaceError(null);
    getStrategySignal(strategy, symbol, quantity)
      .then(setSignal)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }

  function handlePlaceOrder() {
    setPlacing(true);
    setPlaceError(null);
    placeOrder(strategy, symbol, quantity)
      .then((response) => {
        setPlaced(response.orders.map((o) => o.order_id));
        window.dispatchEvent(new Event("portfolio:refresh"));
      })
      .catch((err: Error) => setPlaceError(err.message))
      .finally(() => setPlacing(false));
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Strategy preview</h2>
      </div>
      <p className="panel-note">
        Review the legs and risk checks below before placing a real order.
      </p>
      <div className="controls">
        <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
          {STRATEGIES.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        <input
          type="number"
          min={1}
          value={quantity}
          onChange={(e) => setQuantity(Number(e.target.value))}
        />
        <button onClick={handlePreview} disabled={loading}>
          {loading ? "Loading…" : "Preview"}
        </button>
      </div>

      {error && <p className="error">Failed to load signal: {error}</p>}

      {signal && (
        <div>
          <p className="signal-heading">
            {signal.strategy} on <span className="sym">{signal.symbol}</span> — spot{" "}
            <span className="sym">{signal.underlying_ltp}</span>
          </p>
          <table>
            <thead>
              <tr>
                <th>Side</th>
                <th>Qty</th>
                <th>Type</th>
                <th>Strike</th>
                <th>Premium</th>
              </tr>
            </thead>
            <tbody>
              {signal.legs.map((leg, i) => (
                <tr key={i}>
                  <td>{leg.side}</td>
                  <td>{leg.quantity}</td>
                  <td>{leg.option_type}</td>
                  <td>{leg.strike}</td>
                  <td>{leg.premium}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pnl-strip">
            <div>
              <span className="stat-label">Max profit</span>
              <span className={signal.max_profit !== null ? "pnl-pos" : ""}>
                {signal.max_profit === null ? "Unbounded" : signal.max_profit}
              </span>
            </div>
            <div>
              <span className="stat-label">Max loss</span>
              <span className={signal.max_loss !== null ? "pnl-neg" : ""}>
                {signal.max_loss === null ? "Unbounded" : signal.max_loss}
              </span>
            </div>
            <div>
              <span className="stat-label">Breakevens</span>
              <span>{signal.breakevens.join(", ")}</span>
            </div>
          </div>

          <ul className="risk-checks">
            {signal.pre_trade_checks.map((check, i) => (
              <li key={i} className={check.passed ? "check-pass" : "check-fail"}>
                {check.passed ? "PASS" : "FAIL"} — {check.reason}
              </li>
            ))}
          </ul>

          <div className="place-order-row">
            <button
              className="place-order-btn"
              onClick={handlePlaceOrder}
              disabled={!signal.can_place || placing}
            >
              {placing ? "Placing…" : "Place order"}
            </button>
            {!signal.can_place && (
              <span className="panel-note" style={{ margin: 0 }}>
                Placement blocked — a risk check above failed.
              </span>
            )}
          </div>

          {placeError && <p className="error">Order placement failed: {placeError}</p>}
          {placed && (
            <p className="place-success">
              Placed {placed.length} order(s): {placed.join(", ")}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
