import type { VolRank, Volatility } from "../api";
import { num, signed } from "../format";

interface Props {
  vol: Volatility | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Is premium rich or cheap.
 *
 * The question this desk could not answer. It showed greeks and a payoff —
 * what a position *is* — and nothing about whether the options being sold were
 * expensive, which is the first thing a seller wants to know.
 *
 * The rank is the headline rather than the level, because the level alone says
 * nothing: implied nearly always exceeds realised, so 12% is only meaningful
 * against the fact that the last two years ran between 9 and 28.
 */
export function VolPanel({ vol, error, loading }: Props) {
  const parkinson20 = vol?.realised.find((r) => r.window === 20)?.parkinson;

  return (
    <section className="panel a-vol">
      <div className="ph">
        <h2>Volatility</h2>
        {vol && (
          <span className="sub">
            {vol.expiry} · {vol.days_to_expiry.toFixed(0)}d
          </span>
        )}
      </div>

      <div className="pb">
        {error && <p className="err">{error.message}</p>}
        {!vol && loading && <p className="empty">Reading the chain…</p>}
        {!vol && !loading && !error && <p className="empty">Nothing to show yet.</p>}

        {vol && (
          <>
            <div className="volhead">
              <Figure
                label="Implied"
                value={vol.atm_iv === null ? "—" : `${num(vol.atm_iv, 2)}%`}
                note={`at ${num(vol.atm_strike, 0)}`}
              />
              <Figure
                label="Realised 20d"
                value={fmt(vol.realised.find((r) => r.window === 20)?.close_to_close)}
                note="what it actually did"
              />
              {/* Both, because they say different things. The difference is
                  what a seller collects; the ratio is what compares across
                  instruments, where two points on a 9% index is a quarter again
                  and on a 25% one is almost nothing. */}
              <Figure
                label="IV / HV"
                value={vol.iv_hv === null ? "—" : `${num(vol.iv_hv, 2)}×`}
                tone={vol.iv_hv === null ? undefined : vol.iv_hv > 1 ? "up" : "dn"}
                note={
                  vol.spread === null
                    ? "implied over realised"
                    : `${signed(vol.spread, 2)} vol points`
                }
              />
            </div>

            {/* What the market is priced to cover between now and expiry. The
                number a strangle seller is short, in the units the strikes are
                quoted in. */}
            {vol.expected_move_pct !== null && (
              <p className="move">
                Priced for <b>±{num(vol.expected_move_pct, 2)}%</b>
                {vol.expected_move_points !== null && (
                  <>
                    {" "}
                    — <b>±{num(vol.expected_move_points, 0)}</b> points, or{" "}
                    {num(vol.spot - vol.expected_move_points, 0)}–
                    {num(vol.spot + vol.expected_move_points, 0)}
                  </>
                )}{" "}
                by {vol.expiry}
              </p>
            )}

            {vol.iv_rank ? (
              <Ranked label="This underlying's implied" rank={vol.iv_rank} />
            ) : (
              vol.vix_rank && <Ranked label="India VIX" rank={vol.vix_rank} />
            )}

            {/* Two columns, not three. A note beside each figure was cut off
                at this width, and the notes are about the set rather than
                about a row - so they sit under it. */}
            <table className="volrealised">
              <tbody>
                {vol.realised.map((r) => (
                  <tr key={r.window}>
                    <th>Realised {r.window}d</th>
                    <td className="n">{fmt(r.close_to_close)}</td>
                  </tr>
                ))}
                {parkinson20 !== null && parkinson20 !== undefined && (
                  <tr className="aside">
                    <th>Parkinson 20d</th>
                    <td className="n">{fmt(parkinson20)}</td>
                  </tr>
                )}
                {vol.india_vix !== null && (
                  <tr className="aside">
                    <th>India VIX</th>
                    <td className="n">{num(vol.india_vix, 2)}%</td>
                  </tr>
                )}
              </tbody>
            </table>

            <p className="volnote">
              Parkinson reads the whole day&rsquo;s range rather than its two
              endpoints, so it runs low when the movement arrives overnight. India
              VIX is a 30-day figure across strikes, not this expiry.
            </p>

            {vol.caveats.map((c) => (
              <p className="volcaveat" key={c}>
                {c}
              </p>
            ))}
          </>
        )}
      </div>
    </section>
  );
}

const fmt = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${num(v, 2)}%`);

function Figure({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "up" | "dn";
}) {
  return (
    <div className="volfig">
      <small>{label}</small>
      <b className={tone}>{value}</b>
      {note && <em>{note}</em>}
    </div>
  );
}

/**
 * Where a reading sits in its own past, as a bar.
 *
 * Both figures, because they disagree usefully: the rank is position within the
 * high–low range, which one spike two years ago flattens, and the percentile
 * counts days and ignores how far away the extreme is.
 */
function Ranked({ label, rank }: { label: string; rank: VolRank }) {
  const tone = rank.percentile >= 70 ? "rich" : rank.percentile <= 30 ? "cheap" : "mid";
  return (
    <div className={`rankbar ${tone}`}>
      <div className="rankhead">
        <span>{label}</span>
        <b>{rank.says}</b>
      </div>
      <div className="track" title={`${rank.low.toFixed(2)} to ${rank.high.toFixed(2)}`}>
        <span className="fill" style={{ width: `${Math.min(100, Math.max(0, rank.rank))}%` }} />
        <span className="here" style={{ left: `${Math.min(100, Math.max(0, rank.rank))}%` }} />
      </div>
      <div className="rankfoot">
        <span>{num(rank.low, 2)}</span>
        <span>
          rank {rank.rank.toFixed(0)} · {rank.percentile.toFixed(0)}th percentile ·{" "}
          {rank.days} days
        </span>
        <span>{num(rank.high, 2)}</span>
      </div>
    </div>
  );
}
