import { useState } from "react";
import { getStrategySignal, type StrategySignalResponse } from "../api";

const STRATEGIES = ["short_strangle", "iron_condor", "credit_spread_bullish", "credit_spread_bearish"];

export function StrategyPanel() {
  const [strategy, setStrategy] = useState(STRATEGIES[1]);
  const [symbol, setSymbol] = useState("NSE:NIFTY50-INDEX");
  const [quantity, setQuantity] = useState(1);
  const [signal, setSignal] = useState<StrategySignalResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function handlePreview() {
    setLoading(true);
    setError(null);
    getStrategySignal(strategy, symbol, quantity)
      .then(setSignal)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }

  return (
    <section>
      <h2>Strategy preview</h2>
      <p className="hint">
        This is a read-only preview — placing orders is done deliberately outside the
        dashboard, via <code>scripts/place_strategy_order.py</code>.
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
          {loading ? "Loading..." : "Preview"}
        </button>
      </div>

      {error && <p className="error">Failed to load signal: {error}</p>}

      {signal && (
        <div>
          <p>
            {signal.strategy} on {signal.symbol} (spot {signal.underlying_ltp})
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
          <div className="pnl-summary">
            <div>
              <span className="label">Max profit</span>
              <span>{signal.max_profit === null ? "Unbounded" : signal.max_profit}</span>
            </div>
            <div>
              <span className="label">Max loss</span>
              <span>{signal.max_loss === null ? "Unbounded" : signal.max_loss}</span>
            </div>
            <div>
              <span className="label">Breakevens</span>
              <span>{signal.breakevens.join(", ")}</span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
