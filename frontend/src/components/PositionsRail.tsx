import type { PortfolioHistoryPoint, PortfolioResponse } from "../api";
import { clockIST, compact, dayIST, dir, int, num, signed } from "../format";

interface Props {
  portfolio: PortfolioResponse | null;
  portfolioError: Error | null;
  history: PortfolioHistoryPoint[] | null;
}

export function PositionsRail({ portfolio, portfolioError, history }: Props) {
  return (
    <>
      <section className="panel grow">
        <div className="ph">
          <h2>Positions</h2>
          <span className="sp" />
          <span className="sub">{portfolio ? `${portfolio.positions.length}` : " "}</span>
        </div>
        {portfolioError && <p className="err">{portfolioError.message}</p>}
        <div className="pb">
          {portfolio && !portfolio.positions.length && (
            <p className="empty">No open positions at your broker.</p>
          )}
          {portfolio && !!portfolio.positions.length && (
            <table>
              <thead>
                <tr>
                  <th className="l">Contract</th>
                  <th>Qty</th>
                  <th>Last</th>
                  <th>P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.positions.map((p) => (
                  <tr key={p.symbol}>
                    <td className="l" title={p.symbol}>
                      {p.symbol.replace(/^[A-Z]+:/, "")}
                    </td>
                    <td className={p.net_quantity < 0 ? "dn" : "up"}>{signed(p.net_quantity)}</td>
                    <td>{num(p.ltp)}</td>
                    <td className={dir(p.unrealized_pnl)}>{signed(p.unrealized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="ph">
          <h2>Profit and loss history</h2>
          <span className="sp" />
          <span className="sub">{history ? `${history.length} snapshots` : " "}</span>
        </div>
        <div className="pb">
          {history && history.length < 2 && (
            <p className="empty">
              Not enough yet to draw a line. A point is recorded every couple of minutes while the
              desk is open.
            </p>
          )}
          {history && history.length >= 2 && <Sparkline points={history} />}
        </div>
      </section>
    </>
  );
}

function Sparkline({ points }: { points: PortfolioHistoryPoint[] }) {
  const w = 240;
  const h = 62;
  const pad = 4;
  const ys = points.map((p) => p.total_pnl);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const span = hi - lo || 1;
  const X = (i: number) => pad + (i / Math.max(1, points.length - 1)) * (w - 2 * pad);
  const Y = (v: number) => pad + ((hi - v) / span) * (h - 2 * pad);
  const d = points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.total_pnl).toFixed(1)}`).join("");
  const last = points[points.length - 1];

  return (
    <div style={{ padding: "8px 9px" }}>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        style={{ width: "100%", height: h, display: "block" }}
        role="img"
        aria-label="Total profit and loss over recent snapshots"
      >
        <line x1={pad} x2={w - pad} y1={Y(0)} y2={Y(0)} stroke="var(--line)" />
        <path d={d} fill="none" stroke="var(--you)" strokeWidth="1.8" />
        <circle cx={X(points.length - 1)} cy={Y(last.total_pnl)} r="2.5" fill="var(--you)" />
      </svg>
      <div className="irow dim" style={{ marginTop: 4 }}>
        <span>
          {dayIST(points[0].fetched_at)} → {dayIST(last.fetched_at)}
        </span>
        <span className={dir(last.total_pnl)}>{signed(last.total_pnl)}</span>
      </div>
      <div className="irow dim">
        <span>last snapshot</span>
        <span>{clockIST(last.fetched_at)}</span>
      </div>
      <div className="irow dim">
        <span>booked / open</span>
        <span>
          {compact(last.realized_pnl)} / {compact(last.unrealized_pnl)}
        </span>
      </div>
      <div className="irow dim">
        <span>range</span>
        <span>
          {int(lo)} to {int(hi)}
        </span>
      </div>
    </div>
  );
}
