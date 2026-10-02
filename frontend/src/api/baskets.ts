import { del, getJson, postJson, putJson, request } from "./http";

export interface PayoffPoint {
  spot: number;
  payoff: number;
}

export interface BasketLeg {
  id: number;
  symbol: string;
  option_type: "CE" | "PE";
  strike: number;
  side: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
  entry_at: string;
  exit_price: number | null;
  exit_at: string | null;
  is_open: boolean;
  /** Live state, present only when fetched with live=true. Greeks are
      per contract: delta per point, theta per day, vega per volatility point. */
  ltp: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  iv: number | null;
  ltp_change: number | null;
  oi_change: number | null;
}

export interface Basket {
  id: number;
  name: string;
  strategy: string;
  underlying_symbol: string;
  created_at: string;
  /** This structure's own alert levels. Null means no level set. */
  stop_loss: number | null;
  profit_target: number | null;
  delta_limit: number | null;
  /** Overrides for the shared defaults. Null means the default is used. */
  worst_case_limit: number | null;
  short_delta_limit: number | null;
  expiry_warn_days: number | null;
  /** What the open legs are worth now, and how the structure leans — computed
      server-side, so the screen and the alert about it read one number. Null
      when the broker has not priced every open leg. */
  mtm: number | null;
  /** Exposure: deltas weighted by contracts. The only one that converts to money. */
  net_delta: number | null;
  /** The directional sum of the quoted deltas, unweighted — the figure on the
      legs table and the scale a delta limit is set in. */
  net_delta_per_contract: number | null;
  /** Banked by legs already closed — part of the structure's result. */
  realized: number;
  /** realized + mtm: the whole result so far. Null when mtm is. */
  total_pnl: number | null;
  /** When the last leg came off; null while any is open. */
  closed_at: string | null;
  legs: BasketLeg[];
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  payoff_curve: PayoffPoint[];
  /** Only present when fetched with live=true and the expiry could be resolved. */
  payoff_curve_today: PayoffPoint[];
  days_to_expiry: number | null;
  expiry_date: string | null;
  /** False for a calendar or diagonal: the payoff fields are then empty,
      because they are computed at a single expiry and would be wrong. */
  single_expiry: boolean;
}

/** Forget a grouping. Sends nothing to the broker — the position stays open. */
export function deleteBasket(id: number): Promise<void> {
  return del(`/api/baskets/${id}`);
}

/** Take a leg out of a basket it never belonged to. Not an exit — see closeBasketLeg. */
export function removeBasketLeg(basketId: number, legId: number): Promise<void> {
  return del(`/api/baskets/${basketId}/legs/${legId}`);
}

export function getBaskets(live = false): Promise<Basket[]> {
  return getJson<Basket[]>(`/api/baskets${live ? "?live=true" : ""}`);
}

export interface NewBasketLegInput {
  symbol: string;
  option_type: "CE" | "PE";
  strike: number;
  side: "BUY" | "SELL";
  quantity: number;
  entry_price: number;
}

export function createBasket(
  name: string,
  strategy: string,
  underlyingSymbol: string,
  legs: NewBasketLegInput[],
): Promise<Basket> {
  return postJson<
    { name: string; strategy: string; underlying_symbol: string; legs: NewBasketLegInput[] },
    Basket
  >("/api/baskets", { name, strategy, underlying_symbol: underlyingSymbol, legs });
}

/** Alert levels for one structure. Null clears a level. */
export interface BasketLevels {
  stop_loss: number | null;
  profit_target: number | null;
  delta_limit: number | null;
  worst_case_limit: number | null;
  short_delta_limit: number | null;
  expiry_warn_days: number | null;
}

export function setBasketLevels(id: number, levels: BasketLevels): Promise<Basket> {
  return putJson<BasketLevels, Basket>(`/api/baskets/${id}/levels`, levels);
}

// --- Following the broker ---------------------------------------------------
// Everything here reads the broker and writes only the desk's own records. None
// of it places, changes or cancels an order.

export interface FillSuggestion {
  basket_id: number;
  basket_name: string;
  why: string;
}

export interface PendingFill {
  fill_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  quantity: number;
  price: number;
  at: string;
  suggestion: FillSuggestion | null;
}

export interface SyncReport {
  since: string;
  fills_read: number;
  already_seen: number;
  closed: {
    basket_id: number;
    basket_name: string;
    symbol: string;
    quantity: number;
    price: number;
    at: string;
    realized: number;
  }[];
  covered: number;
  outside: number;
  pending: PendingFill[];
  /** Open legs the broker no longer holds, with no fill found to close them. */
  unexplained: {
    basket_id: number;
    basket_name: string;
    leg_id: number;
    symbol: string;
    side: "BUY" | "SELL";
    quantity: number;
    broker_quantity: number;
  }[];
}

export interface HistoryMoment {
  at: string;
  kind: "opened" | "adjusted" | "added" | "reduced" | "closed";
  events: {
    at: string;
    action: "open" | "close";
    leg_id: number;
    symbol: string;
    side: "BUY" | "SELL";
    quantity: number;
    price: number;
    realized: number | null;
  }[];
  realized: number;
  premium: number;
  realized_to_date: number;
}

/** Read fills from the broker and apply them to structures. Read-only toward the broker. */
export function syncBaskets(days = 7): Promise<SyncReport> {
  return postJson<Record<string, never>, SyncReport>(`/api/baskets/sync?days=${days}`, {});
}

export function assignFills(
  fillIds: string[],
  to: { basketId: number } | { name: string; strategy?: string },
): Promise<Basket> {
  return postJson<object, Basket>("/api/baskets/fills/assign", {
    fill_ids: fillIds,
    ...("basketId" in to ? { basket_id: to.basketId } : { name: to.name, strategy: to.strategy }),
  });
}

export function ignoreFills(fillIds: string[]): Promise<void> {
  return request<void>("POST", "/api/baskets/fills/ignore", { fill_ids: fillIds });
}

export function addBasketLeg(
  basketId: number,
  leg: { symbol: string; side: "BUY" | "SELL"; quantity: number; entry_price: number; entry_at?: string },
): Promise<Basket> {
  return postJson<typeof leg, Basket>(`/api/baskets/${basketId}/legs`, leg);
}

export function getBasketHistory(basketId: number): Promise<HistoryMoment[]> {
  return getJson<HistoryMoment[]>(`/api/baskets/${basketId}/history`);
}

export function closeBasketLeg(basketId: number, legId: number, exitPrice: number): Promise<Basket> {
  return postJson<{ exit_price: number }, Basket>(
    `/api/baskets/${basketId}/legs/${legId}/close`,
    { exit_price: exitPrice },
  );
}
