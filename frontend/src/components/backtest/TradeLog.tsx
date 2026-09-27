import { useMemo, useState } from "react";
import type { BacktestTrade } from "../../api";

interface Props {
  trades: BacktestTrade[];
  total: number;
  quote: string;
  /** Which trade is being looked at on the chart, if any. */
  picked: BacktestTrade | null;
  onPick: (trade: BacktestTrade | null) => void;
}

type Filter = "all" | "long" | "short" | "won" | "lost";

const num = (v: number, dp = 2) =>
  v.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });

const when = (iso: string) => iso.slice(0, 16).replace("T", " ");

/**
 * Every trade, in full.
 *
 * A trade log is searched and totalled rather than read top to bottom, so the
 * filters are the feature: what the shorts did on their own, what the
 * liquidations cost, whether the winners were bigger than the losers. The footer
 * totals whatever is on screen, so a filter is also a calculator.
 *
 * Newest first, because the last thing a run did is what you look at first.
 */
export function TradeLog({ trades, total, quote, picked, onPick }: Props) {
  const [filter, setFilter] = useState<Filter>("all");
  const [limit, setLimit] = useState(200);

  const shown = useMemo(() => {
    const matching = trades.filter((t) => {
      if (filter === "long" || filter === "short") return t.side === filter;
      if (filter === "won") return t.net > 0;
      if (filter === "lost") return t.net <= 0;
      return true;
    });
    return [...matching].reverse();
  }, [trades, filter]);

  const sum = useMemo(
    () =>
      shown.reduce(
        (acc, t) => ({
          gross: acc.gross + t.gross,
          fees: acc.fees + t.fees,
          funding: acc.funding + t.funding,
          slippage: acc.slippage + t.slippage,
          net: acc.net + t.net,
        }),
        { gross: 0, fees: 0, funding: 0, slippage: 0, net: 0 },
      ),
    [shown],
  );

  const page = shown.slice(0, limit);

  return (
    <section className="tradelog">
      <header>
        <h3>Trades</h3>
        <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
          <option value="all">every trade</option>
          <option value="long">longs only</option>
          <option value="short">shorts only</option>
          <option value="won">winners</option>
          <option value="lost">losers</option>
        </select>
        <span className="sp" />
        <small className="hint">click a row to see it on the chart</small>
        <small>
          {shown.length.toLocaleString()} shown
          {trades.length < total && ` · last ${trades.length.toLocaleString()} of ${total.toLocaleString()} kept`}
        </small>
        <button onClick={() => download(shown, quote)}>Download CSV</button>
      </header>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Opened</th>
              <th>Closed</th>
              <th>Side</th>
              <th className="n">Qty</th>
              <th className="n">Size</th>
              <th className="n">Entry</th>
              <th className="n">Exit</th>
              <th className="n">Held</th>
              <th>Ended</th>
              <th className="n">Gross</th>
              <th className="n">Fees</th>
              <th className="n">Funding</th>
              <th className="n">Slip</th>
              <th className="n">Net</th>
              <th className="n">Net %</th>
              <th>Opened because</th>
              <th>Closed because</th>
            </tr>
          </thead>
          <tbody>
            {page.map((t, i) => (
              <tr
                key={`${t.opened_at}-${i}`}
                className={[
                  t.net > 0 ? "won" : "lost",
                  picked === t ? "picked" : "",
                ].join(" ")}
                onClick={() => onPick(picked === t ? null : t)}
                title="Show this trade on the chart"
              >
                <td>{when(t.opened_at)}</td>
                <td>{when(t.closed_at)}</td>
                <td className={t.side === "long" ? "lng" : "sht"}>{t.side}</td>
                <td className="n">{num(t.quantity, 4)}</td>
                <td className="n">{num(t.notional, 0)}</td>
                <td className="n">{num(t.entry)}</td>
                <td className="n">{num(t.exit_price)}</td>
                <td className="n">{num(t.bars_held, 0)}</td>
                <td className={t.why === "liquidation" ? "bad" : ""}>{t.why}</td>
                <td className={`n ${t.gross >= 0 ? "up" : "dn"}`}>{num(t.gross)}</td>
                <td className="n dim">−{num(t.fees)}</td>
                <td className="n dim">
                  {t.funding >= 0 ? "−" : "+"}
                  {num(Math.abs(t.funding))}
                </td>
                <td className="n dim">−{num(t.slippage)}</td>
                <td className={`n ${t.net >= 0 ? "up" : "dn"}`}>{num(t.net)}</td>
                <td className={`n ${t.net >= 0 ? "up" : "dn"}`}>
                  {(t.net_pct * 100).toFixed(1)}%
                </td>
                <td className="why">{t.entry_reason}</td>
                <td className="why">{t.exit_reason}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={9}>
                Total of the {shown.length.toLocaleString()} shown
              </td>
              <td className={`n ${sum.gross >= 0 ? "up" : "dn"}`}>{num(sum.gross, 0)}</td>
              <td className="n dim">−{num(sum.fees, 0)}</td>
              <td className="n dim">
                {sum.funding >= 0 ? "−" : "+"}
                {num(Math.abs(sum.funding), 0)}
              </td>
              <td className="n dim">−{num(sum.slippage, 0)}</td>
              <td className={`n ${sum.net >= 0 ? "up" : "dn"}`}>{num(sum.net, 0)}</td>
              <td colSpan={3} />
            </tr>
          </tfoot>
        </table>
      </div>

      {page.length < shown.length && (
        <button className="more" onClick={() => setLimit((n) => n + 500)}>
          Show 500 more ({(shown.length - page.length).toLocaleString()} left)
        </button>
      )}
    </section>
  );
}

/**
 * The trades as a file.
 *
 * Built in the browser rather than asked for from the server: the rows are
 * already here, and a second request could return a different run if anything
 * about the strategy changed in between.
 */
function download(trades: BacktestTrade[], quote: string) {
  const head = [
    "opened",
    "closed",
    "side",
    "quantity",
    `size_${quote}`,
    "entry",
    "exit",
    "bars_held",
    "ended",
    "gross",
    "fees",
    "funding",
    "slippage",
    "net",
    "net_pct",
    "opened_because",
    "closed_because",
  ];
  const rows = trades.map((t) => [
    t.opened_at,
    t.closed_at,
    t.side,
    t.quantity,
    t.notional.toFixed(2),
    t.entry,
    t.exit_price,
    t.bars_held.toFixed(0),
    t.why,
    t.gross.toFixed(4),
    t.fees.toFixed(4),
    t.funding.toFixed(4),
    t.slippage.toFixed(4),
    t.net.toFixed(4),
    (t.net_pct * 100).toFixed(3),
    // The reasons contain commas and are the fields that need quoting.
    `"${t.entry_reason.replace(/"/g, '""')}"`,
    `"${t.exit_reason.replace(/"/g, '""')}"`,
  ]);
  const csv = [head.join(","), ...rows.map((r) => r.join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "backtest-trades.csv";
  link.click();
  URL.revokeObjectURL(url);
}
