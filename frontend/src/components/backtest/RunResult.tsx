import type { BacktestResult } from "../../api";

interface Props {
  result: BacktestResult;
}

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;
const money = (v: number) =>
  v.toLocaleString(undefined, { maximumFractionDigits: 0, minimumFractionDigits: 0 });

/**
 * What a run came to.
 *
 * Ordered by what decides whether a rule is worth trading, which is not the
 * return. Holding comes first beside it, because a 40% return over a window where
 * the instrument tripled is a loss in the only sense that matters. Then the
 * drawdown, which decides whether it is holdable at all. Then where the money
 * went - on a short-horizon rule that is usually the whole story.
 */
export function RunResult({ result }: Props) {
  const m = result.metrics;
  const costs = m.fees + m.funding + m.slippage;

  return (
    <div className="result">
      <header>
        <h2>{result.name}</h2>
        <p className="reads">{result.reads}</p>
        <p className="over">
          {result.bars.toLocaleString()} {result.interval} bars of {result.symbol} from{" "}
          {result.source}
          {result.started && result.ended
            ? `, ${result.started.slice(0, 10)} to ${result.ended.slice(0, 10)}`
            : ""}
        </p>
      </header>

      <div className="heads">
        <Figure
          label="Return"
          value={pct(m.total_return)}
          tone={m.total_return > 0 ? "up" : "dn"}
        />
        <Figure
          label="Holding it instead"
          value={pct(m.buy_and_hold)}
          tone={m.beat_holding ? "dn" : "up"}
          note={m.beat_holding ? "the rule did better" : "the rule did worse"}
        />
        <Figure label="Worst drawdown" value={pct(m.max_drawdown)} tone="dn" />
        <Figure
          label="Trades"
          value={m.trades.toLocaleString()}
          note={m.trades ? `${pct(m.win_rate)} won` : ""}
        />
      </div>

      {m.trades > 0 && (
        <table className="where">
          <caption>Where the money went</caption>
          <tbody>
            <tr>
              <th>Gross</th>
              <td className={m.gross >= 0 ? "up" : "dn"}>{money(m.gross)}</td>
              <td className="say">what the moves were worth, before costs</td>
            </tr>
            <tr>
              <th>Fees</th>
              <td className="dn">−{money(m.fees)}</td>
              <td className="say">both fills, including the 18% GST</td>
            </tr>
            <tr>
              <th>Funding</th>
              <td className={m.funding >= 0 ? "dn" : "up"}>
                {m.funding >= 0 ? "−" : "+"}
                {money(Math.abs(m.funding))}
              </td>
              <td className="say">
                {m.funding >= 0 ? "paid to hold" : "received for holding"}
              </td>
            </tr>
            <tr>
              <th>Slippage</th>
              <td className="dn">−{money(m.slippage)}</td>
              <td className="say">assumed, always against the trade</td>
            </tr>
            <tr className="total">
              <th>Net</th>
              <td className={result.final >= result.capital ? "up" : "dn"}>
                {money(result.final - result.capital)}
              </td>
              <td className="say">
                {m.cost_share !== null
                  ? `costs were ${m.cost_share.toFixed(1)}× the gross profit`
                  : "there was no gross profit for costs to eat"}
              </td>
            </tr>
          </tbody>
        </table>
      )}

      <Curve curve={result.curve} capital={result.capital} />

      {Object.keys(m.endings).length > 0 && (
        <p className="endings">
          How they ended:{" "}
          {Object.entries(m.endings)
            .map(([why, n]) => `${n} by ${why}`)
            .join(", ")}
          . Held {pct(m.exposure)} of the time.
        </p>
      )}

      {result.caveats.map((c) => (
        <p className="caveat" key={c}>
          {c}
        </p>
      ))}

      {result.trades.length > 0 && (
        <details className="log">
          <summary>
            Trades ({result.trades.length === result.trades_total
              ? result.trades_total.toLocaleString()
              : `last ${result.trades.length} of ${result.trades_total.toLocaleString()}`}
            )
          </summary>
          <table>
            <thead>
              <tr>
                <th>Opened</th>
                <th>Side</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Why</th>
                <th>Net</th>
              </tr>
            </thead>
            <tbody>
              {[...result.trades].reverse().map((t, i) => (
                <tr key={i}>
                  <td>{t.opened_at.slice(0, 16).replace("T", " ")}</td>
                  <td>{t.side}</td>
                  <td className="n">{t.entry.toFixed(1)}</td>
                  <td className="n">{t.exit_price.toFixed(1)}</td>
                  <td>{t.why}</td>
                  <td className={`n ${t.net >= 0 ? "up" : "dn"}`}>{t.net.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function Figure({
  label,
  value,
  tone,
  note,
}: {
  label: string;
  value: string;
  tone?: "up" | "dn";
  note?: string;
}) {
  return (
    <div className="fig">
      <small>{label}</small>
      <b className={tone}>{value}</b>
      {note && <em>{note}</em>}
    </div>
  );
}

/**
 * The equity curve.
 *
 * Drawn against the starting capital rather than against its own minimum, so the
 * line crossing the baseline means losing money rather than being near the bottom
 * of the window. A curve autoscaled to itself makes every result look eventful.
 */
function Curve({ curve, capital }: { curve: [number, number][]; capital: number }) {
  if (curve.length < 2) return null;

  const width = 900;
  const height = 220;
  const pad = 4;
  const values = curve.map(([, v]) => v);
  const top = Math.max(...values, capital);
  const bottom = Math.min(...values, capital);
  const span = top - bottom || 1;
  const first = curve[0][0];
  const last = curve[curve.length - 1][0];
  const across = last - first || 1;

  const x = (at: number) => ((at - first) / across) * (width - pad * 2) + pad;
  const y = (v: number) => height - pad - ((v - bottom) / span) * (height - pad * 2);

  const line = curve.map(([at, v], i) => `${i ? "L" : "M"}${x(at).toFixed(1)} ${y(v).toFixed(1)}`);

  return (
    <svg className="curve" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
      <line x1={0} x2={width} y1={y(capital)} y2={y(capital)} className="base" />
      <path d={line.join(" ")} className="eq" />
      <text x={6} y={y(capital) - 5} className="lab">
        started with {capital.toLocaleString()}
      </text>
    </svg>
  );
}
