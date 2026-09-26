import { useMemo, useState } from "react";
import type { Basket, Position, Quote } from "../api";
import { closeBasketLeg, deleteBasket, removeBasketLeg, setBasketLevels } from "../api";
import { dir, int, num, rupeesC, signed } from "../format";
import { PayoffChart } from "./PayoffChart";
import { ConfirmDialog } from "./ConfirmDialog";
import { AdoptDialog } from "./AdoptDialog";
import { StructureDetail } from "./StructureDetail";

interface Props {
  baskets: Basket[] | null;
  error: Error | null;
  loading: boolean;
  positions: Position[];
  /** The underlying currently in focus, used as the default when adopting. */
  symbol: string;
  /** Quotes for every watched underlying, so each card knows its own spot
      rather than only the one in focus. */
  quotes: Record<string, Quote> | null;
  onChanged: () => void;
}

/** A colour per open basket, so a basket reads the same everywhere it appears. */
const CARD_COLOURS = ["#45BDD4", "#9A8CF5", "#E585C0", "#D6A25A", "#6FA8FF", "#C77DFF"];

/**
 * Open structures, each as one card with its own payoff.
 *
 * Grouping legs into the basket they were placed as is the whole point: a
 * flat list of eight option legs tells you nothing about the two positions
 * you actually hold.
 */
export function BasketsPanel({
  baskets,
  error,
  loading,
  positions,
  symbol,
  quotes,
  onChanged,
}: Props) {
  const [closing, setClosing] = useState<{ basket: Basket; legId: number; price: number } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);
  const [adopting, setAdopting] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  // "remove" is a bookkeeping fix, kept deliberately separate from "close",
  // which records a real exit and keeps the leg on the basket
  const [dropping, setDropping] = useState<Basket | null>(null);
  const [unlinking, setUnlinking] = useState<{ basket: Basket; legId: number; what: string } | null>(
    null,
  );

  // live P&L per leg, matched to broker positions by contract symbol
  const ltpBySymbol = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of positions) m.set(p.symbol, p.ltp);
    return m;
  }, [positions]);

  const open = (baskets ?? []).filter((b) => b.legs.some((l) => l.is_open));

  const alreadyGrouped = useMemo(() => {
    const set = new Set<string>();
    for (const b of baskets ?? []) {
      for (const l of b.legs) if (l.is_open) set.add(l.symbol);
    }
    return set;
  }, [baskets]);

  const adoptable = positions.filter(
    (p) => p.net_quantity !== 0 && !alreadyGrouped.has(p.symbol),
  ).length;

  async function run(fn: () => Promise<unknown>, done: () => void) {
    setBusy(true);
    setCloseError(null);
    try {
      await fn();
      done();
      onChanged();
    } catch (e) {
      setCloseError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doClose() {
    if (!closing) return;
    setBusy(true);
    setCloseError(null);
    try {
      await closeBasketLeg(closing.basket.id, closing.legId, closing.price);
      setClosing(null);
      onChanged();
    } catch (e) {
      setCloseError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel a-baskets">
      <div className="ph">
        <h2>Open structures</h2>
        <span className="sub">{baskets ? `${open.length} live` : " "}</span>
        <span className="sp" />
        <button className="tbtn" onClick={() => setAdopting(true)} disabled={!positions.length}>
          Record an open position{adoptable ? ` (${adoptable})` : ""}
        </button>
      </div>

      {error && <p className="err">{error.message}</p>}

      <div className="pb">
        {!baskets && loading && <p className="empty">Loading…</p>}
        {baskets && !open.length && (
          <p className="empty">
            Nothing grouped yet. Place a structure from the right and it lands here automatically —
            or, if you already hold legs at your broker, use “Record an open position” above to
            group them into a card.
          </p>
        )}
        {!!open.length && (
          <div className="cards">
            {open.map((b, i) => {
              const colour = CARD_COLOURS[i % CARD_COLOURS.length];
              const live = b.legs.filter((l) => l.is_open);
              const pnl = live.reduce((a, l) => {
                const ltp = ltpBySymbol.get(l.symbol);
                if (ltp === undefined) return a;
                const sign = l.side === "SELL" ? -1 : 1;
                return a + sign * (ltp - l.entry_price) * l.quantity;
              }, 0);
              const known = live.some((l) => ltpBySymbol.has(l.symbol));

              return (
                <div className="card" key={b.id}>
                  <button
                    className="card-h"
                    style={{ ["--sc" as string]: colour }}
                    aria-expanded={openId === b.id}
                    onClick={() => setOpenId(openId === b.id ? null : b.id)}
                    title={openId === b.id ? "Hide the analysis" : "Show the full analysis"}
                  >
                    <span className="caret" aria-hidden="true">
                      {openId === b.id ? "\u25be" : "\u25b8"}
                    </span>
                    <b>{b.name}</b>
                    <span className="dim">
                      {b.strategy}
                      {b.expiry_date ? ` · expires ${b.expiry_date}` : ""}
                      {b.days_to_expiry != null ? ` · ${b.days_to_expiry.toFixed(1)}d left` : ""}
                    </span>
                    {known && <span className={`mtm ${dir(pnl)}`}>{signed(pnl)}</span>}
                  </button>

                  <div className="met">
                    <div>
                      <small>Best case</small>
                      <span>{rupeesC(b.max_profit)}</span>
                    </div>
                    <div>
                      <small>Worst case</small>
                      <span className="dn">{rupeesC(b.max_loss)}</span>
                    </div>
                    <div>
                      <small>Breaks even at</small>
                      <span>{b.breakevens.length ? b.breakevens.map(int).join(", ") : "—"}</span>
                    </div>
                  </div>

                  {!b.single_expiry && (
                    <p className="empty" style={{ padding: "7px 9px" }}>
                      Legs in different expiries, so there is no single payoff curve — the near leg
                      can expire while the far one still has time value.
                    </p>
                  )}
                  {openId !== b.id && b.payoff_curve.length > 1 && (
                    <PayoffChart
                      curve={b.payoff_curve}
                      todayCurve={b.payoff_curve_today}
                      spot={quotes?.[b.underlying_symbol]?.ltp ?? null}
                      breakevens={b.breakevens}
                      height={120}
                      color={colour}
                      compactMode
                    />
                  )}

                  {openId === b.id ? (
                    <StructureDetail
                      onLevels={(levels) =>
                        // Through the same helper as closing a leg, so a refused
                        // level surfaces where a refused close does rather than
                        // vanishing into the console.
                        void run(() => setBasketLevels(b.id, levels), () => {})
                      }
                      basket={b}
                      spot={quotes?.[b.underlying_symbol]?.ltp ?? null}
                      onCloseLeg={(l) =>
                        setClosing({ basket: b, legId: l.id, price: l.ltp ?? l.entry_price })
                      }
                      onRemoveLeg={(l) =>
                        setUnlinking({
                          basket: b,
                          legId: l.id,
                          what: `${l.side === "SELL" ? "Short" : "Long"} ${int(l.strike)} ${l.option_type}`,
                        })
                      }
                    />
                  ) : (
                    <table>
                      <tbody>
                        {live.map((l) => (
                          <tr key={l.id}>
                            <td className="l">
                              {l.side === "SELL" ? "Short" : "Long"} {int(l.strike)} {l.option_type}
                            </td>
                            <td>{int(l.quantity)}</td>
                            <td className="dim">{num(l.entry_price)}</td>
                            <td>{l.ltp === null ? "\u2014" : num(l.ltp)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  <div className="card-a">
                    <span className="dim">{live.length} open legs</span>
                    <button
                      className="xbtn danger"
                      style={{ marginLeft: "auto" }}
                      onClick={() => setDropping(b)}
                    >
                      Delete structure
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {adopting && (
        <AdoptDialog
          positions={positions}
          alreadyGrouped={alreadyGrouped}
          defaultUnderlying={symbol}
          onClose={() => setAdopting(false)}
          onCreated={onChanged}
        />
      )}

      {dropping && (
        <ConfirmDialog
          title="Delete this structure?"
          go={busy ? "Deleting…" : "Delete the record"}
          disabled={busy}
          onCancel={() => setDropping(null)}
          onConfirm={() =>
            run(() => deleteBasket(dropping.id), () => setDropping(null))
          }
        >
          <p style={{ margin: 0 }}>
            Forgets <b>{dropping.name}</b> and its {dropping.legs.length} legs. Your position at
            the broker is <b>not</b> touched — nothing is squared off, and the legs will go back to
            showing as ungrouped positions you can record again.
          </p>
          <p className="dim" style={{ margin: "8px 0 0", lineHeight: 1.45 }}>
            If the trade is actually over, close each leg instead — that keeps its exit price, so
            the P&amp;L stays in your history.
          </p>
          {closeError && (
            <p className="err" style={{ padding: "8px 0 0", border: 0 }}>
              {closeError}
            </p>
          )}
        </ConfirmDialog>
      )}

      {unlinking && (
        <ConfirmDialog
          title="Remove this leg from the structure?"
          go={busy ? "Removing…" : "Remove the leg"}
          disabled={busy}
          onCancel={() => setUnlinking(null)}
          onConfirm={() =>
            run(
              () => removeBasketLeg(unlinking.basket.id, unlinking.legId),
              () => setUnlinking(null),
            )
          }
        >
          <p style={{ margin: 0 }}>
            Takes <b>{unlinking.what}</b> out of <b>{unlinking.basket.name}</b>. Use this when the
            leg was grouped here by mistake — it sends nothing to the broker, and the position
            stays open.
          </p>
          <p className="dim" style={{ margin: "8px 0 0", lineHeight: 1.45 }}>
            {unlinking.basket.legs.length === 1
              ? "This is the last leg, so the structure itself will go too."
              : "The structure's payoff and breakevens will be recalculated without it."}
          </p>
          {closeError && (
            <p className="err" style={{ padding: "8px 0 0", border: 0 }}>
              {closeError}
            </p>
          )}
        </ConfirmDialog>
      )}

      {closing && (
        <ConfirmDialog
          title="Record this leg as closed?"
          go={busy ? "Recording…" : "Record the exit"}
          onCancel={() => setClosing(null)}
          onConfirm={doClose}
        >
          <p style={{ margin: 0 }}>
            This writes an exit price into your own records for{" "}
            <b>{closing.basket.name}</b>. It does <b>not</b> send an order to your broker — square
            the leg off there first, then record it here.
          </p>
          <div className="field" style={{ marginTop: 10 }}>
            <label htmlFor="exitpx">Exit price</label>
            <input
              id="exitpx"
              type="number"
              step="0.05"
              value={closing.price}
              onChange={(e) => setClosing({ ...closing, price: Number(e.target.value) || 0 })}
            />
          </div>
          {closeError && <p className="err" style={{ padding: "8px 0 0", border: 0 }}>{closeError}</p>}
        </ConfirmDialog>
      )}
    </section>
  );
}
