import type { PerpInstrument, PerpPrice } from "../api";
import { dir } from "../format";

interface Props {
  instruments: readonly PerpInstrument[];
  prices: readonly PerpPrice[];
  selected: string;
  onSelect: (symbol: string) => void;
  quoteAsset: string;
}

/** Past this, a streamed price is not a live price any more. */
const STALE_AFTER = 20;

/**
 * The instruments on this desk, with whatever the stream last said.
 *
 * Selection drives the chart, so this is the desk's navigation as well as its
 * watchlist — the same job MarketWatch does on the options side, and it reads the
 * same way on purpose.
 */
export function PerpsWatch({ instruments, prices, selected, onSelect, quoteAsset }: Props) {
  const priceOf = new Map(prices.map((p) => [p.symbol, p]));

  return (
    <section className="panel a-watch">
      <div className="ph">
        <h2>Instruments</h2>
        <span className="sub">in {quoteAsset}</span>
      </div>
      <div className="pb">
        <table className="watch">
          <tbody>
            {instruments.map((i) => {
              const live = priceOf.get(i.symbol);
              const price = live?.price ?? null;
              // The venue's own 24-hour figure. Deriving one would need a close,
              // and this market does not have one.
              const change = live?.change_pct ?? null;
              const stale = (live?.age_seconds ?? Infinity) > STALE_AFTER;
              return (
                <tr
                  key={i.symbol}
                  className={i.symbol === selected ? "sel" : undefined}
                  onClick={() => onSelect(i.symbol)}
                  aria-current={i.symbol === selected}
                >
                  <td className="l">
                    <span className="wname">{i.name}</span>
                    <span className="wsym dim">{i.symbol}</span>
                  </td>
                  <td className="wpx">
                    {price === null ? "—" : price.toFixed(i.price_dp)}
                    {/* A price with no recent tick behind it is not a live
                        price, and showing it as one is the failure that matters
                        on a desk that runs overnight. */}
                    {stale && price !== null && <span className="wstale" title="No recent tick">·</span>}
                  </td>
                  <td className={change === null ? "wchg dim" : `wchg ${dir(change)}`}>
                    {change === null ? "—" : `${change > 0 ? "+" : ""}${change.toFixed(2)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}