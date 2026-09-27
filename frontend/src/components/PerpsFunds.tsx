import type { PerpPosition } from "../api";
import { dir, num, signed } from "../format";

interface Props {
  positions: readonly PerpPosition[];
  /** Streamed prices by symbol, so the figures move between polls. */
  prices: Record<string, number>;
  quoteCurrency: string;
  moneyCurrency: string;
}

/** Live profit on one position, from the streamed price rather than the poll.
 *
 *  The venue reports no mark price and no unrealized profit on an open
 *  position - only what you paid and how much of it you hold - so there is
 *  nothing to display unless it is worked out here. */
export function livePnl(p: PerpPosition, price: number | undefined): number | null {
  const mark = price ?? p.price;
  if (mark === null || mark === undefined) return null;
  return (mark - p.entry_price) * p.quantity * (p.side.toUpperCase() === "LONG" ? 1 : -1);
}

/**
 * What is at stake, and what it is doing.
 *
 * Only on screen when something is open, because an empty strip of zeros is a
 * line of furniture. Profit is recomputed from every tick rather than from the
 * fifteen-second poll: an open position that only moves four times a minute
 * looks frozen, which is exactly the complaint.
 *
 * Both currencies are named on every figure. Prices here are USDT and the
 * account is kept in INR, and a number without its unit on this desk could be
 * either - a position showing 0.862 margin was labelled INR when the INR figure
 * was 87.92, a hundredfold out, for exactly that reason.
 */
export function PerpsFunds({ positions, prices, quoteCurrency, moneyCurrency }: Props) {
  if (!positions.length) return null;

  let pnl = 0;
  let pnlInMoney = 0;
  let margin = 0;
  let marginInMoney = 0;
  let exposure = 0;
  let known = false;

  for (const p of positions) {
    const live = livePnl(p, prices[p.symbol]);
    if (live !== null) {
      pnl += live;
      pnlInMoney += live * (p.conversion_rate ?? 0);
      known = true;
    }
    margin += p.margin;
    marginInMoney += p.margin_in_margin_asset ?? 0;
    const mark = prices[p.symbol] ?? p.price ?? p.entry_price;
    exposure += mark * p.quantity;
  }

  // Against the margin actually committed, which is the return on what is at
  // risk. Against the whole account it would be a different and much smaller
  // number, and we cannot compute that one: the venue publishes no balance.
  const onMargin = margin > 0 ? pnl / margin : null;

  return (
    <div className="funds">
      {/* In the account's money first. The instrument trades in USDT, but the
          account is funded in INR and that is what the venue's own screen
          shows - and at two decimals of USDT a 0.002 lot of gold reads 0.00
          however far it has moved, because the figure lives in the fourth
          decimal. Reporting 0.00 against the venue's 0.06 is the same number
          twice, shown once in a way nobody can use. */}
      <div className="fund">
        <small>Open profit</small>
        <b className={known ? dir(pnl) : undefined}>
          {known ? signed(pnlInMoney, 2) : "—"}
          <span className="unit">{moneyCurrency}</span>
        </b>
        {known && (
          <em className={dir(pnl)}>
            {signed(pnl, 4)} {quoteCurrency}
            {onMargin !== null && ` · ${signed(onMargin * 100, 2)}% of margin`}
          </em>
        )}
      </div>

      <div className="fund">
        <small>Margin committed</small>
        <b>
          {num(marginInMoney, 0)}
          <span className="unit">{moneyCurrency}</span>
        </b>
        <em>
          {num(margin, 2)} {quoteCurrency}
        </em>
      </div>

      <div className="fund">
        <small>Exposure</small>
        <b>
          {num(exposure, 0)}
          <span className="unit">{quoteCurrency}</span>
        </b>
        <em>
          {positions.length} position{positions.length === 1 ? "" : "s"}
        </em>
      </div>
    </div>
  );
}
