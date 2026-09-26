import type { Basket, BasketLeg, BasketLevels } from "../api";
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
export function StructureDetail({ basket, spot, onLevels, onCloseLeg, onRemoveLeg }: Props) {
  const open = basket.legs.filter((l) => l.is_open);
  const known = (field: keyof BasketLeg) => open.every((l) => l[field] !== null);

  const total = (field: "delta" | "gamma" | "theta" | "vega") =>
    open.reduce((a, l) => a + sign(l) * l.quantity * ((l[field] as number | null) ?? 0), 0);

  // From the server, which now computes both - the rules that alert on them read
  // the same figures, and three places deriving one number is three chances to
  // disagree. The per-leg totals below are still summed here, since they are a
  // table footer rather than a number anything alerts on.
  const netDelta = basket.net_delta ?? total("delta");
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
    ["Net delta", num(netDelta, 2)],
    ["Theta / day", signed(theta)],
    ["Vega / vol pt", signed(vega)],
    ["Gamma", num(gamma, 4)],
  ];

  const levels: BasketLevels = {
    stop_loss: basket.stop_loss,
    profit_target: basket.profit_target,
    delta_limit: basket.delta_limit,
  };

  const rows: [string, string, string?][] = [
    ["Exposure per 1% move", deltaRupees === null ? "—" : signed(deltaRupees)],
    ["Curvature on a 1% move", gammaRupees === null ? "—" : signed(gammaRupees)],
    ["Took in", rupees(credit)],
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
            <td className="l">
              Net delta past &plusmn;<span className="dim"> per index point</span>
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

      {!known("delta") && (
        <p className="dim" style={{ margin: 0, padding: "6px 9px", lineHeight: 1.4 }}>
          Some greeks are missing — the broker quotes none for at least one of these strikes, so the
          totals above cover only the legs it priced.
        </p>
      )}
    </div>
  );
}
