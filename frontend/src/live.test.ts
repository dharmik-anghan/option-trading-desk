import { describe, expect, it } from "vitest";
import type { Basket, PortfolioResponse } from "./api";
import { liveBaskets, livePortfolio, watchedSymbols } from "./live";

const CE = "NSE:NIFTY26OCT23500CE";
const PE = "NSE:NIFTY26OCT22900PE";

const portfolio: PortfolioResponse = {
  positions: [
    { symbol: CE, net_quantity: 65, average_price: 74.7, ltp: 45, unrealized_pnl: -1931, product_type: "MARGIN" },
    { symbol: PE, net_quantity: -65, average_price: 225.35, ltp: 526.2, unrealized_pnl: -19555, product_type: "MARGIN" },
    { symbol: "NSE:NIFTY26OCT23100CE", net_quantity: 0, average_price: 0, ltp: 110, unrealized_pnl: 0, product_type: "MARGIN" },
  ],
  realized_pnl: 4882,
  unrealized_pnl: -21486,
  total_pnl: -16604,
};

describe("livePortfolio", () => {
  it("moves each position by the price change times its quantity", () => {
    const out = livePortfolio(portfolio, { [CE]: { price: 50 }, [PE]: { price: 520 } });
    // long 65 up 5 = +325; short 65 down 6.2 = +403
    expect(out?.positions[0].unrealized_pnl).toBeCloseTo(-1931 + 325);
    expect(out?.positions[1].unrealized_pnl).toBeCloseTo(-19555 + 403);
    expect(out?.unrealized_pnl).toBeCloseTo(-21486 + 728);
    expect(out?.total_pnl).toBeCloseTo(-16604 + 728);
    expect(out?.realized_pnl).toBe(4882);
  });

  it("returns the same object when nothing moved", () => {
    expect(livePortfolio(portfolio, { [CE]: { price: 45 } })).toBe(portfolio);
  });
});

describe("liveBaskets", () => {
  const leg = (symbol: string, side: "BUY" | "SELL", entry: number, ltp: number) => ({
    id: 1,
    symbol,
    option_type: "CE" as const,
    strike: 0,
    side,
    quantity: 65,
    entry_price: entry,
    entry_at: "",
    exit_price: null,
    exit_at: null,
    is_open: true,
    ltp,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    iv: null,
    ltp_change: null,
    oi_change: null,
  });
  const basket = {
    realized: 100,
    mtm: 0,
    total_pnl: 100,
    legs: [leg(CE, "BUY", 74.7, 45), leg(PE, "SELL", 225.35, 526.2)],
  } as unknown as Basket;

  it("works the MTM out again as the server does", () => {
    const [out] = liveBaskets([basket], { [PE]: { price: 500 } }) ?? [];
    const expected = (45 - 74.7) * 65 - (500 - 225.35) * 65;
    expect(out.mtm).toBeCloseTo(expected);
    expect(out.total_pnl).toBeCloseTo(100 + expected);
  });
});

describe("watchedSymbols", () => {
  it("is the open contracts, sorted, without closed positions", () => {
    expect(watchedSymbols(portfolio, null, null)).toEqual([PE, CE].sort());
  });
});
