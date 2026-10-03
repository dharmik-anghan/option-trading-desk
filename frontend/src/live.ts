import type { Basket, OptionChain, PortfolioResponse } from "./api";

/** The latest streamed price per symbol. */
export type Prices = Readonly<Record<string, { price: number }>>;

/**
 * Every contract the desk is showing a price for, for the stream to carry:
 * open positions, open basket legs, and the strikes on the chain when it is
 * open. Sorted, so the same set always makes the same request and a refetch
 * that changed nothing does not reconnect the stream.
 */
export function watchedSymbols(
  portfolio: PortfolioResponse | null,
  baskets: readonly Basket[] | null,
  chain: OptionChain | null,
): string[] {
  const out = new Set<string>();
  for (const p of portfolio?.positions ?? []) if (p.net_quantity !== 0) out.add(p.symbol);
  for (const b of baskets ?? []) for (const l of b.legs) if (l.is_open) out.add(l.symbol);
  for (const r of chain?.rows ?? []) out.add(r.symbol);
  return [...out].sort();
}

/**
 * Positions moved to the streamed price.
 *
 * Unrealised P&L is linear in the price - quantity times the move - so each
 * position moves by (new - old) x net quantity from the broker's own figure,
 * which keeps whatever the broker folded into it. Realised is untouched.
 */
export function livePortfolio(
  portfolio: PortfolioResponse | null,
  prices: Prices,
): PortfolioResponse | null {
  if (!portfolio) return portfolio;
  let moved = 0;
  const positions = portfolio.positions.map((p) => {
    const tick = prices[p.symbol];
    if (!tick || p.net_quantity === 0 || tick.price === p.ltp) return p;
    const delta = (tick.price - p.ltp) * p.net_quantity;
    moved += delta;
    return { ...p, ltp: tick.price, unrealized_pnl: p.unrealized_pnl + delta };
  });
  if (moved === 0) return portfolio;
  return {
    ...portfolio,
    positions,
    unrealized_pnl: portfolio.unrealized_pnl + moved,
    total_pnl: portfolio.total_pnl + moved,
  };
}

/**
 * Baskets with each open leg at its streamed price, and the structure's MTM
 * worked out again the way the server does: the sum over open legs of
 * direction x (mark - entry) x quantity. Greeks and the payoff curve are left
 * as polled - they need a model, not a price.
 */
export function liveBaskets(baskets: Basket[] | null, prices: Prices): Basket[] | null {
  if (!baskets) return baskets;
  return baskets.map((b) => {
    let changed = false;
    const legs = b.legs.map((l) => {
      const tick = l.is_open ? prices[l.symbol] : undefined;
      if (!tick || tick.price === l.ltp) return l;
      changed = true;
      return { ...l, ltp: tick.price };
    });
    if (!changed) return b;
    const open = legs.filter((l) => l.is_open);
    const priced = open.every((l) => l.ltp !== null);
    const mtm = priced
      ? open.reduce(
          (sum, l) => sum + (l.side === "BUY" ? 1 : -1) * ((l.ltp ?? 0) - l.entry_price) * l.quantity,
          0,
        )
      : null;
    return {
      ...b,
      legs,
      mtm,
      total_pnl: mtm === null ? b.total_pnl : b.realized + mtm,
    };
  });
}

/** The chain with each row at its streamed price. OI and greeks stay as fetched. */
export function liveChain(chain: OptionChain | null, prices: Prices): OptionChain | null {
  if (!chain) return chain;
  let changed = false;
  const rows = chain.rows.map((r) => {
    const tick = prices[r.symbol];
    if (!tick || tick.price === r.ltp) return r;
    changed = true;
    return { ...r, ltp: tick.price };
  });
  return changed ? { ...chain, rows } : chain;
}
