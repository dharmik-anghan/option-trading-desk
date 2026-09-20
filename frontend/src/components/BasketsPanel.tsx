import { useEffect, useState } from "react";
import {
  closeBasketLeg,
  createBasket,
  getBaskets,
  getPortfolio,
  type Basket,
  type NewBasketLegInput,
  type Position,
} from "../api";
import { formatPnl } from "../format";
import { PayoffChart } from "./PayoffChart";

function parseOptionSymbol(
  symbol: string,
): { option_type: "CE" | "PE"; strike: number } | null {
  const match = symbol.match(/(\d+)(CE|PE)$/);
  if (!match) return null;
  return { strike: Number(match[1]), option_type: match[2] as "CE" | "PE" };
}

function CreateBasketForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [positions, setPositions] = useState<Position[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [name, setName] = useState("");
  const [strategy, setStrategy] = useState("iron_condor");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function toggleOpen() {
    if (!open && positions === null) {
      getPortfolio()
        .then((p) => setPositions(p.positions))
        .catch((err: Error) => setError(err.message));
    }
    setOpen(!open);
  }

  function toggleSelected(symbol: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(symbol)) next.delete(symbol);
      else next.add(symbol);
      return next;
    });
  }

  function handleSubmit() {
    if (!positions) return;
    const chosen = positions.filter((p) => selected.has(p.symbol));
    const legs: NewBasketLegInput[] = [];
    for (const position of chosen) {
      const parsed = parseOptionSymbol(position.symbol);
      if (!parsed) {
        setError(`Could not parse strike/type from symbol: ${position.symbol}`);
        return;
      }
      legs.push({
        symbol: position.symbol,
        option_type: parsed.option_type,
        strike: parsed.strike,
        side: position.net_quantity >= 0 ? "BUY" : "SELL",
        quantity: Math.abs(position.net_quantity),
        entry_price: position.average_price,
      });
    }
    if (legs.length === 0) {
      setError("Select at least one position.");
      return;
    }
    setSubmitting(true);
    setError(null);
    createBasket(
      name || `${strategy} basket`,
      strategy,
      "NSE:NIFTY50-INDEX",
      legs,
    )
      .then(() => {
        onCreated();
        setOpen(false);
        setSelected(new Set());
        setName("");
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setSubmitting(false));
  }

  return (
    <div className="create-basket">
      <button className="ghost-btn" onClick={toggleOpen}>
        {open ? "Cancel" : "+ Create basket from current positions"}
      </button>
      {open && (
        <div className="create-basket-body">
          {error && <p className="error">{error}</p>}
          {!positions ? (
            <p className="empty-note">Loading positions…</p>
          ) : positions.length === 0 ? (
            <p className="empty-note">No open positions to group.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th></th>
                  <th>Symbol</th>
                  <th>Net qty</th>
                  <th>Avg price</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.symbol}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selected.has(p.symbol)}
                        onChange={() => toggleSelected(p.symbol)}
                      />
                    </td>
                    <td>{p.symbol}</td>
                    <td>{p.net_quantity}</td>
                    <td>{p.average_price}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="controls">
            <input
              placeholder="Basket name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <input
              placeholder="Strategy label"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            />
            <button onClick={handleSubmit} disabled={submitting}>
              {submitting ? "Creating…" : "Create basket"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function BasketRow({
  basket,
  onChanged,
}: {
  basket: Basket;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [closingLegId, setClosingLegId] = useState<number | null>(null);
  const [exitPrice, setExitPrice] = useState("");
  const [error, setError] = useState<string | null>(null);

  const openLegs = basket.legs.filter((l) => l.is_open).length;
  const closedLegs = basket.legs.length - openLegs;

  function handleClose(legId: number) {
    const price = Number(exitPrice);
    if (!Number.isFinite(price)) {
      setError("Enter a valid exit price.");
      return;
    }
    closeBasketLeg(basket.id, legId, price)
      .then(() => {
        setClosingLegId(null);
        setExitPrice("");
        setError(null);
        onChanged();
      })
      .catch((err: Error) => setError(err.message));
  }

  return (
    <div className="basket-row">
      <div className="basket-summary" onClick={() => setExpanded(!expanded)}>
        <div>
          <span className="basket-name">{basket.name}</span>
          <span className="basket-meta">
            {basket.strategy} · {basket.underlying_symbol} · {openLegs} open
            {closedLegs > 0 ? `, ${closedLegs} closed` : ""}
          </span>
        </div>
        <div className="pnl-strip" style={{ margin: 0 }}>
          <div>
            <span className="stat-label">Max profit</span>
            <span
              className={
                basket.max_profit === null || basket.max_profit >= 0
                  ? "pnl-pos"
                  : "pnl-neg"
              }
            >
              {basket.max_profit === null
                ? "Unbounded"
                : formatPnl(basket.max_profit)}
            </span>
          </div>
          <div>
            <span className="stat-label">Max loss</span>
            <span
              className={
                basket.max_loss === null || basket.max_loss >= 0
                  ? "pnl-pos"
                  : "pnl-neg"
              }
            >
              {basket.max_loss === null
                ? "Unbounded"
                : formatPnl(basket.max_loss)}
            </span>
          </div>
        </div>
      </div>

      {expanded && (
        <>
          <PayoffChart
            points={basket.payoff_curve}
            breakevens={basket.breakevens}
          />
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {basket.legs.map((leg) => (
                <tr key={leg.id}>
                  <td>{leg.symbol}</td>
                  <td>{leg.side}</td>
                  <td>{leg.quantity}</td>
                  <td>{leg.entry_price}</td>
                  <td>{leg.exit_price ?? "—"}</td>
                  <td className={leg.is_open ? "" : "pnl-pos"}>
                    {leg.is_open ? "Open" : "Closed"}
                  </td>
                  <td>
                    {leg.is_open &&
                      (closingLegId === leg.id ? (
                        <span className="controls" style={{ margin: 0 }}>
                          <input
                            type="number"
                            placeholder="exit price"
                            value={exitPrice}
                            onChange={(e) => setExitPrice(e.target.value)}
                          />
                          <button onClick={() => handleClose(leg.id)}>
                            Confirm
                          </button>
                        </span>
                      ) : (
                        <button
                          className="ghost-btn"
                          onClick={() => setClosingLegId(leg.id)}
                        >
                          Close
                        </button>
                      ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export function BasketsPanel() {
  const [baskets, setBaskets] = useState<Basket[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    getBaskets()
      .then(setBaskets)
      .catch((err: Error) => setError(err.message));
  }

  useEffect(() => {
    load();
    window.addEventListener("portfolio:refresh", load);
    return () => window.removeEventListener("portfolio:refresh", load);
  }, []);

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>Strategies</h2>
      </div>
      <p className="panel-note">
        Grouped strategies tracked by us (not the broker) — keeps every leg ever
        part of a strategy, including closed ones, so the payoff reflects
        P&amp;L already banked from legs you've exited.
      </p>

      <CreateBasketForm onCreated={load} />

      {error && <p className="error">Failed to load baskets: {error}</p>}
      {!baskets ? (
        <p className="empty-note">Loading strategies…</p>
      ) : baskets.length === 0 ? (
        <p className="empty-note">No strategies tracked yet.</p>
      ) : (
        <div className="basket-list">
          {baskets.map((basket) => (
            <BasketRow key={basket.id} basket={basket} onChanged={load} />
          ))}
        </div>
      )}
    </section>
  );
}
