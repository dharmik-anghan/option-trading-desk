import { useEffect, useState } from "react";
import type { Basket, BasketLeg, BasketLevels, HistoryMoment } from "../api";
import { addBasketLeg, getBasketHistory } from "../api";
import { PayoffChart, PayoffLegend } from "./PayoffChart";
import { dir, int, num, pct, rupees, signed } from "../format";
import { ThresholdInput } from "./ThresholdInput";

interface Props {
  basket: Basket;
  /** Set this structure's own alert levels. */
  onLevels: (levels: BasketLevels) => void;
  /** Live price of this basket's own underlying, or null if not known. */
  spot: number | null;
  onCloseLeg: (leg: BasketLeg) => void;
  onRemoveLeg: (leg: BasketLeg) => void;
  /** The structure changed here (a leg added by hand); reload. */
  onChanged: () => void;
}

const sign = (leg: BasketLeg) => (leg.side === "BUY" ? 1 : -1);

/**
 * Everything worth knowing about one open structure.
 *
 * The greeks are aggregated across the open legs: each is the broker's
 * per-unit figure multiplied by the leg's quantity and its direction, so a
 * short leg subtracts. Selling premium therefore shows positive theta and
 * negative vega and gamma, which is the shape of the trade.
 */
export function StructureDetail({
  basket,
  spot,
  onLevels,
  onCloseLeg,
  onRemoveLeg,
  onChanged,
}: Props) {
  const open = basket.legs.filter((l) => l.is_open);
  const shut = basket.legs.filter((l) => !l.is_open);
  const known = (field: keyof BasketLeg) => open.every((l) => l[field] !== null);

  const total = (field: "delta" | "gamma" | "theta" | "vega") =>
    open.reduce((a, l) => a + sign(l) * l.quantity * ((l[field] as number | null) ?? 0), 0);

  // Two delta figures, because they answer different questions and differ only
  // by the lot size - which is exactly why they get confused. A balanced
  // structure is zero in both.
  //
  //   drift    the directional sum of the quoted deltas. What the legs table
  //            shows, what a trader says out loud, and what a delta limit is set
  //            against.
  //   netDelta the same weighted by contracts. The position's real exposure and
  //            the only one that becomes money.
  const netDelta = basket.net_delta ?? total("delta");
  const drift = basket.net_delta_per_contract;
  const theta = total("theta");
  const vega = total("vega");
  const gamma = total("gamma");

  // Delta is per point of the underlying; a 1% move is the reading a trader
  // actually wants, and it is the only form comparable across indices.
  const onePct = spot !== null ? spot * 0.01 : null;
  const deltaRupees = onePct !== null ? netDelta * onePct : null;
  const gammaRupees = onePct !== null ? 0.5 * gamma * onePct * onePct : null;

  const credit = -open.reduce((a, l) => a + sign(l) * l.quantity * l.entry_price, 0);
  // What it would cost to buy the structure back right now.
  const closeNow = known("ltp")
    ? open.reduce((a, l) => a + sign(l) * l.quantity * (l.ltp ?? 0), 0)
    : null;
  const mtm = basket.mtm ?? (closeNow === null ? null : closeNow + credit);

  const captured =
    mtm !== null && basket.max_profit !== null && basket.max_profit > 0
      ? (mtm / basket.max_profit) * 100
      : null;
  const onRisk =
    basket.max_profit !== null && basket.max_loss !== null && basket.max_loss !== 0
      ? basket.max_profit / Math.abs(basket.max_loss)
      : null;

  // The short sitting closest to being tested — the leg that decides this trade.
  const shorts = open.filter((l) => l.side === "SELL" && l.delta !== null);
  const nearest = shorts.length
    ? shorts.reduce((b, l) => (Math.abs(l.delta!) > Math.abs(b.delta!) ? l : b))
    : null;

  // The unit belongs in the label, not on a third line under every value -
  // four tiles each three lines deep is what made this strip cramped. The
  // rupee translations moved into the table below, with the other rupee rows.
  const greeks: [string, string][] = [
    // Labelled per contract because that is what it is, and because the number
    // beside it is the same figure weighted by lots. One called "Net delta" and
    // one called "Exposure" read as the same thing measured twice.
    ["Delta per contract", drift === null ? "\u2014" : num(drift, 2)],
    ["Theta / day", signed(theta)],
    ["Vega / vol pt", signed(vega)],
    ["Gamma", num(gamma, 4)],
  ];

  const levels: BasketLevels = {
    stop_loss: basket.stop_loss,
    profit_target: basket.profit_target,
    delta_limit: basket.delta_limit,
    worst_case_limit: basket.worst_case_limit,
    short_delta_limit: basket.short_delta_limit,
    expiry_warn_days: basket.expiry_warn_days,
  };

  const rows: [string, string, string?][] = [
    ["Position delta", num(netDelta, 2)],
    ["Exposure per 1% move", deltaRupees === null ? "—" : signed(deltaRupees)],
    ["Curvature on a 1% move", gammaRupees === null ? "—" : signed(gammaRupees)],
    ["Took in", rupees(credit)],
    ["Banked from closed legs", shut.length ? signed(basket.realized) : "—"],
    [
      "Whole result so far",
      mtm === null ? "—" : `${signed(mtm + basket.realized)}  (open ${signed(mtm)})`,
    ],
    ["Cost to close now", closeNow === null ? "—" : rupees(-closeNow)],
    ["Best case", rupees(basket.max_profit)],
    ["Worst case", rupees(basket.max_loss)],
    ["Of best case won", captured === null ? "—" : `${Math.round(captured)}%`],
    ["Reward for the risk", onRisk === null ? "—" : `${onRisk.toFixed(2)} : 1`],
    [
      "Breaks even at",
      basket.breakevens.length
        ? basket.breakevens
            .map((b) => (spot === null ? int(b) : `${int(b)} (${pct((b - spot) / spot, 1)})`))
            .join("   ")
        : "—",
    ],
    [
      "Expiry",
      basket.expiry_date
        ? `${basket.expiry_date}${
            basket.days_to_expiry !== null ? ` · ${basket.days_to_expiry.toFixed(1)} days left` : ""
          }`
        : "—",
    ],
    [
      "Closest short",
      nearest
        ? `${int(nearest.strike)} ${nearest.option_type} at delta ${Math.abs(nearest.delta!).toFixed(2)}`
        : "—",
    ],
  ];

  return (
    <div className="detail">
      <div className="sec">
        Profit and loss<span className="dim">at expiry, and where it stands now</span>
      </div>
      {!basket.single_expiry && (
        <p className="empty">
          These legs expire on different dates, so a single payoff curve would be wrong: it values
          the far leg at expiry too, which reports the whole debit as a certain loss. The greeks
          and per-leg figures below are still live.
        </p>
      )}
      {basket.single_expiry && (
        <PayoffLegend
          color="var(--fg)"
          hasSpot={spot !== null}
          hasToday={basket.payoff_curve_today.length > 1}
        />
      )}
      {basket.single_expiry && (
        <PayoffChart
          curve={basket.payoff_curve}
          todayCurve={basket.payoff_curve_today}
          spot={spot}
          breakevens={basket.breakevens}
          daysToExpiry={basket.days_to_expiry}
          height={250}
          zoomable
        />
      )}

      <div className="sec">
        Greeks<span className="dim">across the {open.length} open legs</span>
      </div>
      <div className="greeks">
        {greeks.map(([label, value]) => (
          <div key={label}>
            <small>{label}</small>
            <b className={/^[+−]/.test(value) ? dir(value.startsWith("+") ? 1 : -1) : undefined}>
              {value}
            </b>
          </div>
        ))}
      </div>

      {/* Levels for this structure, where the structure is - rather than as
          account-wide settings applied to every one alike. "Has this trade made
          its money" and "has it drifted" are questions about one position, and a
          single threshold shared across a condor and a calendar cannot answer
          either. Blank means no level. */}
      <div className="sec">
        Tell me when<span className="dim">levels for this structure alone</span>
      </div>
      <table className="lim tight">
        <tbody>
          <tr>
            <td className="l">Profit reaches</td>
            <td>
              <ThresholdInput
                label="Profit target for this structure"
                value={basket.profit_target}
                step={500}
                nullable
                placeholder="none"
                onCommit={(v) => onLevels({ ...levels, profit_target: v })}
              />
            </td>
          </tr>
          <tr>
            <td className="l">Loss reaches</td>
            <td>
              <ThresholdInput
                label="Stop for this structure"
                value={basket.stop_loss}
                step={500}
                nullable
                signed
                placeholder="none"
                onCommit={(v) => onLevels({ ...levels, stop_loss: v })}
              />
            </td>
          </tr>
          <tr>
            {/* A magnitude, hence the plus-or-minus: a structure meant to be
                neutral has drifted whether it drifted long or short, and the
                alert names which way. Stepped in whole units because net delta
                is multiplied by the contracts held - on a 65-lot condor each leg
                contributes about 20, so a limit of 0.2 would fire instantly and
                a limit of 0.05 is not a number anyone means. */}
            {/* The current value beside the field, because the scale was the
                trap: the limit is read against the per-contract sum - the figure
                on the legs table - and not against the same sum weighted by
                sixty-five lots. */}
            <td className="l">
              Net delta past &plusmn;
              <span className="dim">
                {" "}
                now {drift === null ? "\u2014" : num(drift, 2)}
              </span>
            </td>
            <td>
              <ThresholdInput
                label="Delta limit for this structure, as a magnitude"
                value={basket.delta_limit}
                step={1}
                nullable
                placeholder="none"
                onCommit={(v) => onLevels({ ...levels, delta_limit: v })}
              />
            </td>
          </tr>
          {/* Overrides, shown with the default they replace. Blank means the
              default is in force - which is what a structure recorded before
              these existed has, and it behaves exactly as it did. */}
          <tr>
            <td className="l">Short tested at delta</td>
            <td>
              <ThresholdInput
                label="Delta a short counts as tested at, for this structure"
                value={basket.short_delta_limit}
                step={0.05}
                nullable
                placeholder="0.30"
                onCommit={(v) => onLevels({ ...levels, short_delta_limit: v })}
              />
            </td>
          </tr>
          <tr>
            <td className="l">Warn days before expiry</td>
            <td>
              <ThresholdInput
                label="Days before expiry to warn, for this structure"
                value={basket.expiry_warn_days}
                step={1}
                nullable
                placeholder="3"
                onCommit={(v) => onLevels({ ...levels, expiry_warn_days: v })}
              />
            </td>
          </tr>
          <tr>
            <td className="l">Worst case past</td>
            <td>
              <ThresholdInput
                label="Worst case limit for this structure"
                value={basket.worst_case_limit}
                step={1000}
                nullable
                placeholder="40,000"
                onCommit={(v) => onLevels({ ...levels, worst_case_limit: v })}
              />
            </td>
          </tr>
        </tbody>
      </table>

      <div className="sec">Where the trade stands</div>
      <table>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label}>
              <td className="l dim">{label}</td>
              <td className="l">{value}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="sec">
        Legs<span className="dim">greeks per contract, as the broker reports them</span>
      </div>
      <table>
        <thead>
          <tr>
            <th className="l">Leg</th>
            <th>Qty</th>
            <th>Entry</th>
            <th>Last</th>
            <th>P&amp;L</th>
            <th>Delta</th>
            <th>Theta</th>
            <th>Vega</th>
            <th>IV</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {open.map((l) => {
            const pl = l.ltp === null ? null : sign(l) * (l.ltp - l.entry_price) * l.quantity;
            return (
              <tr key={l.id}>
                <td className="l">
                  {l.side === "SELL" ? "Short" : "Long"} {int(l.strike)} {l.option_type}
                </td>
                <td>{int(l.quantity)}</td>
                <td className="dim">{num(l.entry_price)}</td>
                <td>{l.ltp === null ? "—" : num(l.ltp)}</td>
                <td className={pl === null ? undefined : dir(pl)}>
                  {pl === null ? "—" : signed(pl)}
                </td>
                <td>{l.delta === null ? "—" : num(l.delta, 2)}</td>
                <td>{l.theta === null ? "—" : num(l.theta, 2)}</td>
                <td>{l.vega === null ? "—" : num(l.vega, 2)}</td>
                <td>{l.iv === null ? "—" : num(l.iv, 1)}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button
                    className="xbtn"
                    title="Record an exit price — the leg stays on this structure"
                    onClick={() => onCloseLeg(l)}
                  >
                    Close
                  </button>{" "}
                  <button
                    className="xbtn danger"
                    title="Take this leg out of the structure — it was grouped here by mistake"
                    onClick={() => onRemoveLeg(l)}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            );
          })}
          <tr className="total">
            <td className="l">Structure</td>
            <td />
            <td />
            <td />
            <td className={mtm === null ? undefined : dir(mtm)}>
              {mtm === null ? "—" : signed(mtm)}
            </td>
            <td>{num(netDelta, 2)}</td>
            <td>{signed(theta)}</td>
            <td>{signed(vega)}</td>
            <td />
            <td />
          </tr>
        </tbody>
      </table>

      {!!shut.length && (
        <>
          <div className="sec">
            Closed legs<span className="dim">kept on the structure, with what each banked</span>
          </div>
          <table className="shut">
            <thead>
              <tr>
                <th className="l">Leg</th>
                <th>Qty</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>Closed</th>
                <th>Banked</th>
              </tr>
            </thead>
            <tbody>
              {shut.map((l) => {
                const banked = sign(l) * ((l.exit_price ?? 0) - l.entry_price) * l.quantity;
                return (
                  <tr key={l.id}>
                    <td className="l">
                      {l.side === "SELL" ? "Short" : "Long"} {int(l.strike)} {l.option_type}
                    </td>
                    <td>{int(l.quantity)}</td>
                    <td className="dim">{num(l.entry_price)}</td>
                    <td>{l.exit_price === null ? "—" : num(l.exit_price)}</td>
                    <td className="dim">{when(l.exit_at)}</td>
                    <td className={dir(banked)}>{signed(banked)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}

      <History basketId={basket.id} stamp={basket.legs.length + shut.length} />

      <AddLeg basketId={basket.id} onAdded={onChanged} />

      {!known("delta") && (
        <p className="dim" style={{ margin: 0, padding: "6px 9px", lineHeight: 1.4 }}>
          Some greeks are missing — the broker quotes none for at least one of these strikes, so the
          totals above cover only the legs it priced.
        </p>
      )}
    </div>
  );
}

/** "2026-09-29T05:06:49+00:00" -> "29 Sep 10:36", in the desk's own zone. */
function when(at: string | null): string {
  if (!at) return "—";
  const d = new Date(at);
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
}

const KIND: Record<HistoryMoment["kind"], string> = {
  opened: "Opened",
  adjusted: "Adjusted",
  added: "Legs added",
  reduced: "Legs closed",
  closed: "Closed",
};

/**
 * The structure's story: opened, each adjustment, closed - with what each step
 * banked and took in. Read from the legs, so it covers structures recorded long
 * before fills were synced.
 */
function History({ basketId, stamp }: { basketId: number; stamp: number }) {
  const [moments, setMoments] = useState<HistoryMoment[] | null>(null);
  useEffect(() => {
    let live = true;
    void getBasketHistory(basketId)
      .then((m) => live && setMoments(m))
      .catch(() => live && setMoments([]));
    return () => {
      live = false;
    };
    // `stamp` changes when a leg is added or closed, which is when the story does.
  }, [basketId, stamp]);

  if (!moments || !moments.length) return null;
  return (
    <>
      <div className="sec">
        History<span className="dim">every change to this structure, oldest first</span>
      </div>
      <ol className="story">
        {moments.map((m) => (
          <li key={m.at} className={m.kind}>
            <div className="story-h">
              <b>{KIND[m.kind]}</b>
              <span className="dim">{when(m.at)}</span>
              {m.realized !== 0 && (
                <span className={dir(m.realized)}>{signed(m.realized)} banked</span>
              )}
              {m.premium !== 0 && (
                <span className="dim">
                  {m.premium > 0 ? "took in" : "paid"} {rupees(Math.abs(m.premium))}
                </span>
              )}
            </div>
            <ul>
              {m.events.map((e, i) => (
                <li key={i}>
                  {e.action === "open"
                    ? e.side === "SELL"
                      ? "Sold"
                      : "Bought"
                    : e.side === "SELL"
                      ? "Bought back"
                      : "Sold out"}{" "}
                  {int(e.quantity)} × {e.symbol.replace("NSE:", "")} at {num(e.price)}
                  {e.realized !== null && (
                    <span className={dir(e.realized)}> {signed(e.realized)}</span>
                  )}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </>
  );
}

/**
 * A leg entered by hand - a position taken where the sync cannot see it, or a
 * correction. Writes to the desk's records only; nothing is sent to the broker.
 */
function AddLeg({ basketId, onAdded }: { basketId: number; onAdded: () => void }) {
  const [openForm, setOpenForm] = useState(false);
  const [symbol, setSymbol] = useState("NSE:NIFTY");
  const [side, setSide] = useState<"BUY" | "SELL">("SELL");
  const [quantity, setQuantity] = useState(65);
  const [price, setPrice] = useState(0);
  const [at, setAt] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!openForm) {
    return (
      <div className="addleg">
        <button className="xbtn" onClick={() => setOpenForm(true)}>
          + Add a leg by hand
        </button>
      </div>
    );
  }
  return (
    <div className="addleg open">
      <input
        className="sym"
        value={symbol}
        onChange={(e) => setSymbol(e.target.value.toUpperCase())}
        placeholder="NSE:NIFTY26OCT23100CE"
      />
      <select value={side} onChange={(e) => setSide(e.target.value as "BUY" | "SELL")}>
        <option value="SELL">Sold</option>
        <option value="BUY">Bought</option>
      </select>
      <input
        type="number"
        min={1}
        value={quantity}
        onChange={(e) => setQuantity(Math.max(1, Number(e.target.value)))}
        title="Quantity, in units"
      />
      <input
        type="number"
        min={0}
        step={0.05}
        value={price}
        onChange={(e) => setPrice(Math.max(0, Number(e.target.value)))}
        title="Entry price"
      />
      <input
        type="datetime-local"
        value={at}
        onChange={(e) => setAt(e.target.value)}
        title="When it was opened (IST). Blank is now."
      />
      <button
        className="tbtn go"
        onClick={() => {
          setError(null);
          void addBasketLeg(basketId, {
            symbol,
            side,
            quantity,
            entry_price: price,
            ...(at ? { entry_at: at } : {}),
          })
            .then(() => {
              setOpenForm(false);
              onAdded();
            })
            .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
        }}
      >
        Add leg
      </button>
      <button className="xbtn" onClick={() => setOpenForm(false)}>
        Cancel
      </button>
      {error && <p className="err">{error}</p>}
      <p className="dim note">
        Records the leg on this structure only. It sends nothing to Fyers — use it for a
        position the sync cannot see, or to correct one.
      </p>
    </div>
  );
}
