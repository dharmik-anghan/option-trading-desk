import { useMemo, useState } from "react";
import type { OptbtLeg, OptbtTrade } from "../../api";
import { day, dir, hhmm, premium, rupees, signedRupees } from "../../format";

interface Props {
  trades: OptbtTrade[];
  picked: OptbtTrade | null;
  onPick: (trade: OptbtTrade | null) => void;
}

const PAGE = 250;

/**
 * Every trade, a row each, every leg inside it.
 *
 * A multi-leg result is legs that usually went opposite ways, so each leg's
 * strike, entry, exit and how it ended stay visible rather than folding into
 * one net. Clicking a row replays it above the table.
 */
export function TradeTable({ trades, picked, onPick }: Props) {
  const [limit, setLimit] = useState(PAGE);
  // Newest first: the recent past is what gets checked against memory. The
  // filtering itself happens above, in the results' own filter bar.
  const shown = useMemo(() => trades.slice().reverse(), [trades]);

  const page = shown.slice(0, limit);
  const sum = shown.reduce(
    (a, t) => ({ gross: a.gross + t.gross, charges: a.charges + t.charges, net: a.net + t.net }),
    { gross: 0, charges: 0, net: 0 },
  );

  return (
    <section className="tradelog otrades">
      <header>
        <h3>Trades</h3>
        <span className="sp" />
        <button onClick={() => download(shown)}>Download CSV</button>
      </header>

      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Opened</th>
              <th>Closed</th>
              <th>Legs — strike, in → out, how it ended</th>
              <th className="n">Gross</th>
              <th className="n">Charges</th>
              <th className="n">Net</th>
            </tr>
          </thead>
          <tbody>
            {page.map((t) => (
              <tr
                key={t.id}
                className={[t.net > 0 ? "won" : "lost", picked?.id === t.id ? "picked" : ""].join(
                  " ",
                )}
                onClick={() => onPick(picked?.id === t.id ? null : t)}
                title="Replay this trade"
              >
                <td>
                  {day(t.opened)} {hhmm(t.opened)}
                </td>
                <td>{t.closed ? `${day(t.closed)} ${hhmm(t.closed)}` : "open"}</td>
                <td className="legcells">
                  {t.legs.map((l, i) => (
                    <LegLine key={i} leg={l} />
                  ))}
                </td>
                <td className={`n ${dir(t.gross)}`}>{signedRupees(t.gross)}</td>
                <td className="n dim">{rupees(-t.charges)}</td>
                <td className={`n ${dir(t.net)}`}>{signedRupees(t.net)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={3}>Total of {shown.length.toLocaleString()} trades</td>
              <td className={`n ${dir(sum.gross)}`}>{signedRupees(sum.gross)}</td>
              <td className="n dim">{rupees(-sum.charges)}</td>
              <td className={`n ${dir(sum.net)}`}>{signedRupees(sum.net)}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {page.length < shown.length && (
        <button className="more" onClick={() => setLimit((n) => n + PAGE)}>
          Show {PAGE} more ({(shown.length - page.length).toLocaleString()} left)
        </button>
      )}
    </section>
  );
}

function LegLine({ leg }: { leg: OptbtLeg }) {
  return (
    <span className={`legline ${leg.side}`}>
      <i>{leg.side === "sell" ? "S" : "B"}</i>
      <span className="k">
        {leg.strike.toLocaleString("en-IN")} {leg.kind}
      </span>
      <span className="p">
        {premium(leg.entry)} → {premium(leg.exit)}
      </span>
      <span className={leg.ended === "stop" ? "stopped" : "dim"}>
        {leg.ended ?? "open"}
        {leg.exit_at && leg.ended !== "expiry" ? ` ${hhmm(leg.exit_at)}` : ""}
      </span>
      <span className={`n ${dir(leg.pnl)}`}>{signedRupees(leg.pnl)}</span>
    </span>
  );
}

/** Built in the browser from the rows on screen, a line per leg, so the file is exactly this run. */
function download(trades: OptbtTrade[]) {
  const head = [
    "trade", "opened", "closed", "leg", "side", "expiry", "strike", "kind", "lots", "lot_size",
    "entry_at", "entry", "exit_at", "exit", "ended", "leg_pnl", "leg_charges",
    "trade_gross", "trade_charges", "trade_net",
  ];
  const rows = trades.flatMap((t) =>
    t.legs.map((l) => [
      t.id, t.opened, t.closed ?? "", l.tag, l.side, l.expiry, l.strike, l.kind, l.lots,
      l.lot_size, l.entry_at, l.entry, l.exit_at ?? "", l.exit ?? "", l.ended ?? "open",
      l.pnl.toFixed(2), l.charges.toFixed(2),
      t.gross.toFixed(2), t.charges.toFixed(2), t.net.toFixed(2),
    ]),
  );
  const csv = [head.join(","), ...rows.map((r) => r.join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "option-backtest-trades.csv";
  link.click();
  URL.revokeObjectURL(url);
}
