import { useEffect, useState } from "react";
import { getPortfolio, type PortfolioResponse } from "../api";
import { formatPnl } from "../format";

export function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPortfolio()
      .then(setPortfolio)
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) {
    return <p className="error">Failed to load portfolio: {error}</p>;
  }
  if (!portfolio) {
    return <p>Loading portfolio...</p>;
  }

  return (
    <section>
      <h2>Portfolio</h2>
      <div className="pnl-summary">
        <div>
          <span className="label">Realized</span>
          <span className={portfolio.realized_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
            {formatPnl(portfolio.realized_pnl)}
          </span>
        </div>
        <div>
          <span className="label">Unrealized</span>
          <span className={portfolio.unrealized_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
            {formatPnl(portfolio.unrealized_pnl)}
          </span>
        </div>
        <div>
          <span className="label">Total</span>
          <span className={portfolio.total_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
            {formatPnl(portfolio.total_pnl)}
          </span>
        </div>
      </div>

      {portfolio.positions.length === 0 ? (
        <p>No open positions.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Net qty</th>
              <th>Avg price</th>
              <th>LTP</th>
              <th>Unrealized P&L</th>
            </tr>
          </thead>
          <tbody>
            {portfolio.positions.map((position) => (
              <tr key={position.symbol}>
                <td>{position.symbol}</td>
                <td>{position.net_quantity}</td>
                <td>{position.average_price}</td>
                <td>{position.ltp}</td>
                <td className={position.unrealized_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
                  {formatPnl(position.unrealized_pnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
