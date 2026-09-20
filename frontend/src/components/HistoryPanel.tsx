import { useEffect, useState } from "react";
import { getPortfolioHistory, type PortfolioHistoryPoint } from "../api";
import { formatNumber } from "../format";

export function HistoryPanel() {
  const [history, setHistory] = useState<PortfolioHistoryPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPortfolioHistory(7)
      .then(setHistory)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) {
    return <p className="error">Failed to load history: {error}</p>;
  }
  if (!history) {
    return <p>Loading history...</p>;
  }
  if (history.length === 0) {
    return (
      <section>
        <h2>P&amp;L history (last 7 days)</h2>
        <p>No snapshots yet — run scripts/portfolio_status.py to record one.</p>
      </section>
    );
  }

  return (
    <section>
      <h2>P&amp;L history (last 7 days)</h2>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Realized</th>
            <th>Unrealized</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          {history.map((point) => (
            <tr key={point.fetched_at}>
              <td>{new Date(point.fetched_at).toLocaleString()}</td>
              <td>{formatNumber(point.realized_pnl)}</td>
              <td>{formatNumber(point.unrealized_pnl)}</td>
              <td className={point.total_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                {formatNumber(point.total_pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
