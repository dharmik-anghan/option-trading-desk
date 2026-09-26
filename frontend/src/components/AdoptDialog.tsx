import { useMemo, useState } from "react";
import { STRATEGIES, UNDERLYINGS, createBasket } from "../api";
import type { NewBasketLegInput, Position } from "../api";
import { int, num, parseContract } from "../format";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * Offered, not enforced. The four the backend can *build* are here, plus the
 * shapes people commonly hold but cannot be generated. Anything you already
 * own can be recorded whatever you call it: the backend stores this as a
 * label and never dispatches on it, and the payoff is worked out from the
 * legs regardless.
 */
const SUGGESTED_STRUCTURES: string[] = [
  ...STRATEGIES.map((s) => s.label),
  "Calendar spread",
  "Diagonal spread",
  "Ratio spread",
  "Covered call",
  "Protective put",
  "Butterfly",
  "Jade lizard",
  "Custom",
];

interface Props {
  positions: Position[];
  /** Contract symbols already grouped into an open structure. */
  alreadyGrouped: Set<string>;
  defaultUnderlying: string;
  onClose: () => void;
  onCreated: () => void;
}

/**
 * Group positions that already exist at the broker into one named structure.
 *
 * Placing through this desk records the basket for you. Anything opened
 * elsewhere — or before this desk existed — has to be adopted, otherwise it
 * shows up as four loose legs and never gets a payoff curve.
 */
export function AdoptDialog({
  positions,
  alreadyGrouped,
  defaultUnderlying,
  onClose,
  onCreated,
}: Props) {
  const candidates = useMemo(
    () =>
      positions
        .filter((p) => p.net_quantity !== 0 && !alreadyGrouped.has(p.symbol))
        .map((p) => ({ p, parsed: parseContract(p.symbol) }))
        .filter((x): x is { p: Position; parsed: { strike: number; optionType: "CE" | "PE" } } =>
          x.parsed !== null,
        ),
    [positions, alreadyGrouped],
  );

  const [picked, setPicked] = useState<Set<string>>(() => new Set(candidates.map((c) => c.p.symbol)));
  const [name, setName] = useState("");
  const [strategy, setStrategy] = useState("");
  const [underlying, setUnderlying] = useState(defaultUnderlying);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (sym: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(sym)) next.delete(sym);
      else next.add(sym);
      return next;
    });

  async function create() {
    const legs: NewBasketLegInput[] = candidates
      .filter((c) => picked.has(c.p.symbol))
      .map((c) => ({
        symbol: c.p.symbol,
        option_type: c.parsed.optionType,
        strike: c.parsed.strike,
        side: c.p.net_quantity > 0 ? "BUY" : "SELL",
        quantity: Math.abs(c.p.net_quantity),
        entry_price: c.p.average_price,
      }));
    if (!legs.length) return;
    setBusy(true);
    setError(null);
    try {
      await createBasket(
        name.trim() || "Untitled structure",
        strategy.trim() || "Custom",
        underlying,
        legs,
      );
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const nothingLeft = candidates.length === 0;

  return (
    <ConfirmDialog
      title="Record an open position as a structure"
      go={busy ? "Recording…" : `Record ${picked.size} leg${picked.size === 1 ? "" : "s"}`}
      disabled={busy || picked.size === 0 || nothingLeft}
      onCancel={onClose}
      onConfirm={create}
    >
      {nothingLeft ? (
        <p style={{ margin: 0 }}>
          Every open option position is already part of a structure. Nothing left to group.
        </p>
      ) : (
        <>
          <p style={{ margin: 0 }}>
            This only writes your own records — it sends nothing to the broker. Pick the legs that
            belong together and give them a name.
          </p>

          <div className="field" style={{ marginTop: 10 }}>
            <label htmlFor="bname">Name</label>
            <input
              id="bname"
              type="text"
              value={name}
              placeholder="Iron condor · Oct"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="field" style={{ marginTop: 7 }}>
            <label htmlFor="bstrat">Structure</label>
            {/* Free text with the common ones offered, not a fixed list. What
                you already hold can be anything - a calendar, a ratio, a
                jade lizard, legs you assembled by hand - and the backend
                stores this as a label rather than dispatching on it. */}
            <input
              id="bstrat"
              type="text"
              list="structure-suggestions"
              value={strategy}
              placeholder="Iron condor, calendar, ratio spread…"
              onChange={(e) => setStrategy(e.target.value)}
            />
            <datalist id="structure-suggestions">
              {SUGGESTED_STRUCTURES.map((label) => (
                <option key={label} value={label} />
              ))}
            </datalist>
          </div>
          <div className="field" style={{ marginTop: 7 }}>
            <label htmlFor="bunder">Underlying</label>
            <select id="bunder" value={underlying} onChange={(e) => setUnderlying(e.target.value)}>
              {UNDERLYINGS.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name}
                </option>
              ))}
            </select>
          </div>

          <div className="adopt">
            {candidates.map(({ p, parsed }) => (
              <label key={p.symbol}>
                <input
                  type="checkbox"
                  checked={picked.has(p.symbol)}
                  onChange={() => toggle(p.symbol)}
                />
                <span className="who">
                  {p.net_quantity > 0 ? "Long" : "Short"} {int(parsed.strike)} {parsed.optionType}
                </span>
                <span className="qty dim">
                  {int(Math.abs(p.net_quantity))} @ {num(p.average_price)}
                </span>
              </label>
            ))}
          </div>

          {error && (
            <p className="err" style={{ padding: "8px 0 0", border: 0 }}>
              {error}
            </p>
          )}
        </>
      )}
    </ConfirmDialog>
  );
}
