import { useCallback, useEffect, useState } from "react";
import type { Basket, PendingFill, SyncReport } from "../api";
import { assignFills, ignoreFills, syncBaskets } from "../api";
import { int, num, signed } from "../format";

interface Props {
  /** Structures with an open leg - where a new position can be put. */
  open: Basket[];
  /** Something in the structures changed; reload them. */
  onChanged: () => void;
}

/**
 * What the broker did that the structures should know about.
 *
 * Syncs on mount and on demand. A leg bought back at the broker closes here on
 * its own - that fill can mean nothing else. A new position is never put into a
 * structure on a guess: it is listed with a suggestion, and one click puts it
 * there. Reads the broker and writes only the desk's records; no order is sent.
 */
export function FillsTray({ open, onChanged }: Props) {
  const [report, setReport] = useState<SyncReport | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [target, setTarget] = useState<Record<string, number | "new">>({});

  const sync = useCallback(async () => {
    setSyncing(true);
    setError(null);
    try {
      const r = await syncBaskets();
      setReport(r);
      if (r.closed.length) onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  }, [onChanged]);

  useEffect(() => {
    void sync();
    // Once on mount: the desk has just opened, and whatever happened at the
    // broker since it was last open should land before anything is read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fills grouped by where they are suggested to go, so an adjustment's two
  // legs are placed with one click rather than two.
  const pending = report?.pending ?? [];
  const groups = new Map<string, PendingFill[]>();
  for (const p of pending) {
    const key = p.suggestion ? `s${p.suggestion.basket_id}` : "none";
    groups.set(key, [...(groups.get(key) ?? []), p]);
  }

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChanged();
      await sync();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const place = (fills: PendingFill[], key: string) => {
    const where = target[key] ?? fills[0].suggestion?.basket_id ?? "new";
    const ids = fills.map((f) => f.fill_id);
    return act(() =>
      where === "new"
        ? assignFills(ids, { name: newName.trim() || "New structure" })
        : assignFills(ids, { basketId: where }),
    );
  };

  const quiet =
    report && !report.closed.length && !pending.length && !report.unexplained.length;

  return (
    <div className="fills">
      <div className="fills-h">
        <span className="dim">
          {syncing
            ? "Reading fills from Fyers…"
            : report
              ? `Checked ${report.fills_read} fills since ${report.since}`
              : " "}
        </span>
        <span className="sp" />
        <button className="tbtn" onClick={() => void sync()} disabled={syncing}>
          Sync with Fyers
        </button>
      </div>

      {error && <p className="err">{error}</p>}
      {quiet && <p className="dim fills-ok">Structures match what Fyers executed.</p>}

      {!!report?.closed.length && (
        <div className="fills-box">
          <b>Closed from Fyers fills</b>
          {report.closed.map((c) => (
            <div key={`${c.symbol}-${c.at}`} className="fills-row">
              <span>{c.basket_name}</span>
              <span>
                {int(c.quantity)} × {c.symbol.replace("NSE:", "")} at {num(c.price)}
              </span>
              <span className="dim">{c.at.slice(11, 16)}</span>
              <span className={c.realized >= 0 ? "up" : "dn"}>{signed(c.realized)}</span>
            </div>
          ))}
        </div>
      )}

      {[...groups.entries()].map(([key, fills]) => {
        const suggestion = fills[0].suggestion;
        const where = target[key] ?? suggestion?.basket_id ?? "new";
        return (
          <div className="fills-box pend" key={key}>
            <b>New at Fyers, not in a structure yet</b>
            {fills.map((f) => (
              <div key={f.fill_id} className="fills-row">
                <span className={f.side === "SELL" ? "sell" : "buy"}>{f.side}</span>
                <span>
                  {int(f.quantity)} × {f.symbol.replace("NSE:", "")} at {num(f.price)}
                </span>
                <span className="dim">
                  {f.at.slice(0, 10)} {f.at.slice(11, 16)}
                </span>
              </div>
            ))}
            {suggestion && <p className="dim why">Suggested: {suggestion.basket_name} — {suggestion.why}.</p>}
            <div className="fills-act">
              <select
                value={String(where)}
                onChange={(e) =>
                  setTarget({
                    ...target,
                    [key]: e.target.value === "new" ? "new" : Number(e.target.value),
                  })
                }
              >
                {open.map((b) => (
                  <option key={b.id} value={b.id}>
                    Add to {b.name}
                  </option>
                ))}
                <option value="new">Start a new structure…</option>
              </select>
              {where === "new" && (
                <input
                  placeholder="Name it"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                />
              )}
              <button className="tbtn go" onClick={() => void place(fills, key)}>
                {fills.length > 1 ? `Add these ${fills.length} legs` : "Add this leg"}
              </button>
              <button
                className="xbtn"
                title="Not part of any structure — stop asking"
                onClick={() => void act(() => ignoreFills(fills.map((f) => f.fill_id)))}
              >
                Ignore
              </button>
            </div>
          </div>
        );
      })}

      {!!report?.unexplained.length && (
        <div className="fills-box warn">
          <b>Open here, but Fyers no longer holds them</b>
          {report.unexplained.map((u) => (
            <div key={u.leg_id} className="fills-row">
              <span>{u.basket_name}</span>
              <span>
                {u.side === "SELL" ? "Short" : "Long"} {int(u.quantity)} ×{" "}
                {u.symbol.replace("NSE:", "")}
              </span>
              <span className="dim">Fyers holds {int(u.broker_quantity)}</span>
            </div>
          ))}
          <p className="dim why">
            No fill was found for these in the last 7 days, so the exit price is unknown. Close
            the leg on its structure with the price you got — the desk will not guess it.
          </p>
        </div>
      )}
    </div>
  );
}
