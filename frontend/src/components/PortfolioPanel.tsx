import { useEffect, useState } from "react";
import { getPortfolio, type PortfolioResponse } from "../api";
import { formatPnl } from "../format";

export function PortfolioPanel() {
  const [portfolio, setPortfolio] = useState<PortfolioResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function load() {
      getPortfolio()
        .then(setPortfolio)
        .catch((err: Error) => setError(err.message));
    }
    load();
    // Real positions/P&L change after an order is placed elsewhere on the
    // page (StrategyPanel); refetch rather than going stale until reload.
    window.addEventListener("portfolio:refresh", load);
    return () => window.removeEventListener("portfolio:refresh", load);
  }, []);

  if (error) {
    return (
      <section className="panel">
        <p className="error">Failed to load portfolio: {error}</p>
      </section>
    );
  }
  if (!portfolio) {
    return (
      <section className="panel">
        <p className="empty-note">Loading portfolio…</p>
      </section>
    );
  }

  const totalClass = portfolio.total_pnl >= 0 ? "pnl-pos" : "pnl-neg";

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Portfolio</h2>
        <span className={`hero-pnl ${totalClass}`}>{formatPnl(portfolio.total_pnl)}</span>
      </div>

      <div className="pnl-strip">
        <div>
          <span className="stat-label">Realized</span>
          <span className={portfolio.realized_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
            {formatPnl(portfolio.realized_pnl)}
          </span>
        </div>
        <div>
          <span className="stat-label">Unrealized</span>
          <span className={portfolio.unrealized_pnl >= 0 ? "pnl-pos" : "pnl-neg"}>
            {formatPnl(portfolio.unrealized_pnl)}
          </span>
        </div>
        <div>
          <span className="stat-label">Open positions</span>
          <span>{portfolio.positions.length}</span>
        </div>
      </div>

      {portfolio.positions.length === 0 ? (
        <p className="empty-note">No open positions.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Net qty</th>
              <th>Avg price</th>
              <th>LTP</th>
              <th>Unrealized P&amp;L</th>
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
