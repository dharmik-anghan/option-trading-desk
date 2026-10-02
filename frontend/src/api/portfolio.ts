import { getJson } from "./http";

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

export interface PortfolioHistoryPoint {
  fetched_at: string;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
}

export function getPortfolio(): Promise<PortfolioResponse> {
  return getJson<PortfolioResponse>("/api/portfolio");
}

export function getPortfolioHistory(days = 7): Promise<PortfolioHistoryPoint[]> {
  return getJson<PortfolioHistoryPoint[]>(`/api/portfolio/history?days=${days}`);
}
