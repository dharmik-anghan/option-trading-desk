import { useEffect, useMemo, useRef } from "react";
import type { OptionChain, OptionChainRow, Position } from "../api";
import { BUILDUP_LABEL, buildup, compact, int, isOpening, num, signed } from "../format";

type View = "trading" | "oi" | "greeks";

interface Props {
  chain: OptionChain | null;
  error: Error | null;
  loading: boolean;
  view: View;
  onView: (v: View) => void;
  /** What you actually hold at the broker, marked on its own strikes. */
  positions: Position[];
  open: boolean;
  onOpen: (open: boolean) => void;
  expiry: string;
  onExpiry: (token: string) => void;
  /** Strikes either side of the money to request. */
  depth: number;
  onDepth: (n: number) => void;
}

interface StrikeRow {
  strike: number;
  ce: OptionChainRow | null;
  pe: OptionChainRow | null;
}

const COLS: Record<View, string[]> = {
  trading: ["oi", "iv", "delta", "bid", "ask", "ltp"],
  oi: ["oi", "doi", "oichp", "volume", "buildup", "ltp", "chgp"],
  greeks: ["iv", "delta", "gamma", "theta", "vega", "ltp"],
};

const HEAD: Record<string, string> = {
  oi: "Open int.",
  doi: "Chg OI",
  oichp: "Chg OI %",
  buildup: "Buildup",
  chgp: "Chg %",
  volume: "Volume",
  iv: "IV",
  delta: "Delta",
  gamma: "Gamma",
  theta: "Theta",
  vega: "Vega",
  bid: "Bid",
  ask: "Ask",
  ltp: "Last",
};

export function OptionChainPanel({
  chain,
  error,
  loading,
  view,
  onView,
  positions,
  open,
  onOpen,
  expiry,
  onExpiry,
  depth,
  onDepth,
}: Props) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const centred = useRef<string | null>(null);

  const { rows, atm, maxCeOi, maxPeOi, hasGreeks } = useMemo(() => {
    if (!chain) return { rows: [] as StrikeRow[], atm: 0, maxCeOi: 1, maxPeOi: 1, hasGreeks: false };
    const by = new Map<number, StrikeRow>();
    for (const r of chain.rows) {
      const slot = by.get(r.strike) ?? { strike: r.strike, ce: null, pe: null };
      if (r.option_type === "CE") slot.ce = r;
      else slot.pe = r;
      by.set(r.strike, slot);
    }
    const list = [...by.values()].sort((a, b) => a.strike - b.strike);
    const spot = chain.underlying_ltp;
    const nearest = list.reduce(
      (best, r) => (Math.abs(r.strike - spot) < Math.abs(best - spot) ? r.strike : best),
      list[0]?.strike ?? 0,
    );
    return {
      rows: list,
      atm: nearest,
      maxCeOi: Math.max(1, ...list.map((r) => r.ce?.oi ?? 0)),
      maxPeOi: Math.max(1, ...list.map((r) => r.pe?.oi ?? 0)),
      hasGreeks: chain.rows.some((r) => r.greeks !== null),
    };
  }, [chain]);

  // centre on the money once per underlying, not on every poll
  useEffect(() => {
    if (!chain || !rows.length) return;
    if (centred.current === chain.underlying_symbol) return;
    const el = bodyRef.current?.querySelector("tr.atm") as HTMLElement | null;
    const box = bodyRef.current;
    if (el && box && box.clientHeight) {
      box.scrollTop = el.offsetTop - box.clientHeight / 2;
      centred.current = chain.underlying_symbol;
    }
  }, [chain, rows]);

  /* Marks are keyed by contract symbol, not strike + type. The symbol carries
     the expiry ("NIFTY26OCT23100CE"), so an October position no longer shows
     up against every other expiry's chain as well. */
  const heldAt = useMemo(() => {
    const m = new Map<string, Position>();
    for (const p of positions) if (p.net_quantity !== 0) m.set(p.symbol, p);
    return m;
  }, [positions]);

  const cols = COLS[view];
  const spot = chain?.underlying_ltp ?? 0;

  return (
    <section className={`panel a-chain${open ? "" : " shut"}`}>
      <div className="ph">
        <button
          className="disclose"
          aria-expanded={open}
          onClick={() => onOpen(!open)}
          title={open ? "Hide the chain" : "Show the chain"}
        >
          <span aria-hidden="true">{open ? "\u25be" : "\u25b8"}</span> Option chain
        </button>
        {open && chain && chain.expiries.length > 0 && (
          <div className="expiry">
            <label htmlFor="expiry" className="dim">
              Expiry
            </label>
            <select
              id="expiry"
              value={expiry || chain.expiry_token || ""}
              onChange={(e) => onExpiry(e.target.value)}
            >
              {chain.expiries.map((e) => (
                <option key={e.token} value={e.token}>
                  {e.date} {e.weekly ? "· weekly" : "· monthly"}
                </option>
              ))}
            </select>
            {(() => {
              const cur = chain.expiries.find(
                (e) => e.token === (expiry || chain.expiry_token),
              );
              return cur && !cur.weekly ? <span className="tag m">MONTHLY</span> : null;
            })()}
          </div>
        )}
        <span className="sp" />
        {open && (
          <div className="seg sm" role="group" aria-label="Strikes shown">
            {[15, 25, 40].map((n) => (
              <button key={n} aria-pressed={depth === n} onClick={() => onDepth(n)}>
                {n * 2 + 1}
              </button>
            ))}
          </div>
        )}
        {open && (
          <div className="seg sm" role="group" aria-label="Columns">
            {(["trading", "oi", "greeks"] as View[]).map((v) => (
              <button key={v} aria-pressed={view === v} onClick={() => onView(v)}>
                {v === "trading" ? "Trading" : v === "oi" ? "Open interest" : "Greeks"}
              </button>
            ))}
          </div>
        )}
      </div>

      {!open && null}

      {open && error && <p className="err">{error.message}</p>}

      <div className="pb" ref={bodyRef} hidden={!open}>
        {!chain && loading && <p className="empty">Loading the chain…</p>}
        {!chain && !loading && !error && <p className="empty">No chain yet.</p>}
        {chain && view === "greeks" && !hasGreeks && (
          <p className="err">
            Your broker did not return greeks for this chain, so these columns are blank.
          </p>
        )}
        {chain && (
          <table className="chain">
            <thead>
              <tr>
                <th className="side c" colSpan={cols.length}>
                  Calls
                </th>
                <th className="k" />
                <th className="side p" colSpan={cols.length}>
                  Puts
                </th>
              </tr>
              <tr>
                {cols.map((c) => (
                  <th key={`c-${c}`}>{HEAD[c]}</th>
                ))}
                <th className="k">Strike</th>
                {[...cols].reverse().map((c) => (
                  <th key={`p-${c}`}>{HEAD[c]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const marks = [r.ce, r.pe].flatMap((row) => {
                  if (!row) return [];
                  const held = heldAt.get(row.symbol);
                  const out: {
                    key: string;
                    short: boolean;
                    type: "CE" | "PE";
                    title: string;
                  }[] = [];
                  if (held) {
                    out.push({
                      key: `h-${row.symbol}`,
                      short: held.net_quantity < 0,
                      type: row.option_type,
                      title: `You hold ${held.net_quantity < 0 ? "short" : "long"} ${Math.abs(
                        held.net_quantity,
                      )} × ${int(row.strike)} ${row.option_type} at ${num(held.average_price)}`,
                    });
                  }
                  return out;
                });
                return (
                  <tr key={r.strike} className={r.strike === atm ? "atm" : undefined}>
                    {cols.map((c) => (
                      <Cell
                        key={`c-${c}`}
                        row={r.ce}
                        col={c}
                        itm={r.strike < spot}
                        side="CE"
                        maxOi={maxCeOi}
                      />
                    ))}
                    <td className="k">
                      {int(r.strike)}
                      {marks.map((m) => (
                        <span
                          key={m.key}
                          className={`leg${m.short ? " s" : ""}`}
                          title={m.title}
                        >
                          {m.short ? "−" : "+"}
                          {m.type[0]}
                        </span>
                      ))}
                    </td>
                    {[...cols].reverse().map((c) => (
                      <Cell
                        key={`p-${c}`}
                        row={r.pe}
                        col={c}
                        itm={r.strike > spot}
                        side="PE"
                        maxOi={maxPeOi}
                      />
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

function Cell({
  row,
  col,
  itm,
  side,
  maxOi,
}: {
  row: OptionChainRow | null;
  col: string;
  itm: boolean;
  side: "CE" | "PE";
  maxOi: number;
}) {
  const cls = itm ? "itm" : undefined;
  if (!row) return <td className={cls}>—</td>;
  const g = row.greeks;

  switch (col) {
    case "oi": {
      const w = (row.oi / maxOi) * 100;
      return (
        <td
          className={`${cls ?? ""} oi`.trim()}
          style={{
            backgroundImage: `linear-gradient(to ${side === "CE" ? "left" : "right"}, var(--oibar) ${w}%, transparent ${w}%)`,
          }}
        >
          {compact(row.oi)}
        </td>
      );
    }
    case "doi": {
      // the broker's own figure, not oi - prev_oi, which only agrees when
      // every strike happened to be loaded
      const d = row.oi_change;
      return (
        <td className={cls}>
          <span className={d > 0 ? "up" : d < 0 ? "dn" : ""}>{signed(d)}</span>
        </td>
      );
    }
    case "oichp":
      return (
        <td className={cls}>
          <span className={row.oi_change_pct > 0 ? "up" : row.oi_change_pct < 0 ? "dn" : ""}>
            {num(row.oi_change_pct, 1)}%
          </span>
        </td>
      );
    case "chgp":
      return (
        <td className={cls}>
          <span className={row.ltp_change > 0 ? "up" : row.ltp_change < 0 ? "dn" : ""}>
            {num(row.ltp_change_pct, 1)}%
          </span>
        </td>
      );
    case "buildup": {
      const b = buildup(row.ltp_change, row.oi_change);
      if (!b) return <td className={cls}>—</td>;
      return (
        <td className={`${cls ?? ""} bu`.trim()}>
          <span
            className={isOpening(b) ? "opening" : "closing"}
            title={
              isOpening(b)
                ? "Open interest rising — positions being opened"
                : "Open interest falling — positions being closed"
            }
          >
            {BUILDUP_LABEL[b]}
          </span>
        </td>
      );
    }
    case "volume":
      return <td className={cls}>{compact(row.volume)}</td>;
    case "iv":
      return <td className={cls}>{g ? num(g.iv, 1) : "—"}</td>;
    case "delta":
      return <td className={cls}>{g ? num(g.delta, 2) : "—"}</td>;
    case "gamma":
      return <td className={cls}>{g ? num(g.gamma, 4) : "—"}</td>;
    case "theta":
      return <td className={cls}>{g ? num(g.theta, 2) : "—"}</td>;
    case "vega":
      return <td className={cls}>{g ? num(g.vega, 2) : "—"}</td>;
    case "bid":
      return <td className={cls}>{num(row.bid)}</td>;
    case "ask":
      return <td className={cls}>{num(row.ask)}</td>;
    case "ltp":
      return <td className={cls}><b>{num(row.ltp)}</b></td>;
    default:
      return <td className={cls} />;
  }
}
