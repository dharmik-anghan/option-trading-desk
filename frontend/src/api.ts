const API_BASE = "http://127.0.0.1:8000";

export interface Position {
  symbol: string;
  net_quantity: number;
  average_price: number;
  ltp: number;
  unrealized_pnl: number;
  product_type: string;
}

export interface PortfolioResponse {
  positions: Position[];
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
}

export interface Leg {
  option_type: "CE" | "PE";
  strike: number;
  premium: number;
  quantity: number;
  side: "BUY" | "SELL";
  symbol: string | null;
}

export interface RiskCheck {
  passed: boolean;
  reason: string;
}

export interface StrategySignalResponse {
  strategy: string;
  symbol: string;
  underlying_ltp: number;
  legs: Leg[];
  // null means unbounded (the backend can't send Infinity - it isn't valid JSON).
  max_profit: number | null;
  max_loss: number | null;
  breakevens: number[];
  pre_trade_checks: RiskCheck[];
  can_place: boolean;
}

export interface OrderResult {
  order_id: string;
  message: string;
}

export interface PlaceOrderResponse {
  orders: OrderResult[];
}

export interface PortfolioHistoryPoint {
  fetched_at: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status} ${await response.text()}`);
  }
  return (await response.json()) as T;
}

async function extractErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text);
    if (body?.detail?.reasons) {
      return (body.detail.reasons as string[]).join("; ");
    }
    if (typeof body?.detail === "string") {
      return body.detail;
    }
  } catch {
    // not JSON - fall through to raw text below
  }
  return `${response.status} ${text}`;
}

async function postJson<TRequest, TResponse>(path: string, body: TRequest): Promise<TResponse> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await extractErrorMessage(response));
  }
  return (await response.json()) as TResponse;
}

export function getPortfolio(): Promise<PortfolioResponse> {
  return getJson<PortfolioResponse>("/api/portfolio");
}

export function getStrategySignal(
  strategy: string,
  symbol: string,
  quantity: number,
): Promise<StrategySignalResponse> {
  const params = new URLSearchParams({ symbol, quantity: String(quantity) });
  return getJson<StrategySignalResponse>(`/api/strategies/${strategy}?${params}`);
}

export function getPortfolioHistory(days = 7): Promise<PortfolioHistoryPoint[]> {
  return getJson<PortfolioHistoryPoint[]>(`/api/portfolio/history?days=${days}`);
}

export function placeOrder(
  strategy: string,
  symbol: string,
  quantity: number,
): Promise<PlaceOrderResponse> {
  return postJson<{ strategy: string; symbol: string; quantity: number }, PlaceOrderResponse>(
    "/api/orders/place",
    { strategy, symbol, quantity },
  );
}
