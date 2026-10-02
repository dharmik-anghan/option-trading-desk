import { Fragment, useEffect, useMemo, useState } from "react";
import {
  getPreOpenDays,
  getPreOpenSession,
  type PreOpenDays,
  type PreOpenLevel,
  type PreOpenQuote,
  type PreOpenSession,
} from "../api";
import { BackButton } from "./BackButton";

interface Props {
  onHome: () => void;
}

type SortKey = "symbol" | "pct_change" | "final_quantity" | "turnover_cr" | "pressure";

const WEEKDAY = new Intl.DateTimeFormat("en-IN", { weekday: "short" });
const SHORT = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short" });

function asDate(day: string): Date {
  return new Date(`${day}T00:00:00`);
}

function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function qty(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString("en-IN");
}

function pct(v: number): string {
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function tone(v: number): string {
  return v > 0 ? "up" : v < 0 ? "dn" : "";
}

/** Buy quantity over sell quantity. Above one, more wanted in than out. */
function pressure(q: PreOpenQuote): number | null {
  if (!q.total_buy_qty || !q.total_sell_qty) return null;
  return q.total_buy_qty / q.total_sell_qty;
}

/**
 * NSE's pre-open auction, day by day.
 *
 * Where each stock was set to open before anything traded, and how much stood
 * behind that price. The desk writes a session down every morning because NSE
 * only ever shows the latest one; this is the record of it.
 */
export function PreOpen({ onHome }: Props) {
  const [days, setDays] = useState<PreOpenDays | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [session, setSession] = useState<PreOpenSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [only, setOnly] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: "pct_change",
    desc: true,
  });
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    void getPreOpenDays()
      .then((d) => {
        setDays(d);
        setPicked((p) => p ?? d.days[0]?.day ?? null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!picked) return;
    let live = true;
    setOpen(null);
    void getPreOpenSession(picked)
      .then((s) => live && setSession(s))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [picked]);

  const shown = useMemo(() => {
    if (!session) return [];
    const needle = search.trim().toUpperCase();
    const kept = session.quotes.filter(
      (q) =>
        (only === "all" || q.indices.includes(only)) &&
        (!needle ||
          q.symbol.includes(needle) ||
          (q.name ?? "").toUpperCase().includes(needle)),
    );
    const value = (q: PreOpenQuote): number | string => {
      if (sort.key === "symbol") return q.symbol;
      if (sort.key === "pressure") return pressure(q) ?? -1;
      return q[sort.key] ?? -1;
    };
    return kept.slice().sort((a, b) => {
      const x = value(a);
      const y = value(b);
      const c = typeof x === "string" ? x.localeCompare(y as string) : x - (y as number);
      return sort.desc ? -c : c;
    });
  }, [session, only, search, sort]);

  // One scale for the whole list, so a bar's length compares across rows. The
  // widest gap sets it, floored so a quiet morning is not drawn as a loud one.
  const scale = useMemo(
    () => Math.max(1, ...shown.map((q) => Math.abs(q.pct_change))),
    [shown],
  );

  const breadth = useMemo(() => {
    const up = shown.filter((q) => q.change > 0).length;
    const down = shown.filter((q) => q.change < 0).length;
    const value = shown.reduce((s, q) => s + (q.turnover_cr ?? 0), 0);
    return { up, down, flat: shown.length - up - down, value };
  }, [shown]);

  const by = (key: SortKey) =>
    setSort((s) => ({ key, desc: s.key === key ? !s.desc : key !== "symbol" }));
  const arrow = (key: SortKey) => (sort.key === key ? (sort.desc ? " ↓" : " ↑") : "");

  const day = session?.day;
  const fromApi = day?.source === "nse";

  return (
    <main className="rrgpage preopen">
      <header>
        <BackButton onClick={onHome} />
        <h1 title="Where each stock was set to open in NSE's 09:00–09:08 auction. NSE only shows the latest session; the desk records each one.">
          Pre-open
        </h1>
      </header>

      {error && <p className="bad">{error}</p>}
      {days && !days.recorder.running && (
        <p className="caveat">
          Recorder not running — today won't be saved. Run <code>scripts/preopen.py fetch</code>.
        </p>
      )}
      {days?.recorder.last_error && (
        <p className="caveat">Last recording failed: {days.recorder.last_error}</p>
      )}

      {days && days.days.length === 0 && (
        <p className="empty">
          No sessions yet. Today's is saved after 09:08; import older days with{" "}
          <code>scripts/preopen.py import</code>.
        </p>
      )}

      {days && days.days.length > 0 && (
        <div className="pobody">
          <nav className="podays" aria-label="Recorded sessions">
            {days.days.map((d) => (
              <button
                key={d.day}
                className={d.day === picked ? "on" : ""}
                aria-current={d.day === picked ? "date" : undefined}
                title={
                  d.source === "csv"
                    ? "From a downloaded file: no book, no buy/sell totals, no index"
                    : undefined
                }
                onClick={() => setPicked(d.day)}
              >
                <b>{SHORT.format(asDate(d.day))}</b>
                <small>
                  {WEEKDAY.format(asDate(d.day))}
                  {d.source === "csv" && " · file"}
                </small>
                <span className={`gap ${d.index ? tone(d.index.pct_change) : ""}`}>
                  {d.index ? pct(d.index.pct_change) : "—"}
                </span>
                <Breadth up={d.advances} down={d.declines} flat={d.unchanged} />
              </button>
            ))}
          </nav>

          <section className="poday">
            {day && (
              <div className="posum">
                <div className="idx">
                  <small>NIFTY 50</small>
                  {day.index ? (
                    <>
                      <b>{num(day.index.price)}</b>
                      <span className={tone(day.index.change)}>
                        {day.index.change > 0 ? "+" : ""}
                        {num(day.index.change)} {pct(day.index.pct_change)}
                      </span>
                    </>
                  ) : (
                    <b className="none" title="A downloaded file does not carry the index">
                      —
                    </b>
                  )}
                </div>
                <div className="adv">
                  <small>Advances / declines</small>
                  <span className="counts">
                    <b className="up">{breadth.up}</b>
                    <Breadth up={breadth.up} down={breadth.down} flat={breadth.flat} />
                    <b className="dn">{breadth.down}</b>
                  </span>
                  <span className="dim">{breadth.flat} unchanged</span>
                </div>
                <div>
                  <small>Auction value</small>
                  <b>₹{num(breadth.value)} cr</b>
                </div>
                <div>
                  <small>Settled</small>
                  <b>{day.as_of ? day.as_of.slice(11, 19) : "—"}</b>
                  <span className="dim">{fromApi ? "NSE" : "file"}</span>
                </div>
              </div>
            )}

            <div className="controls">
              <label title="Index lists are today's constituents, not the lists on that day">
                <select aria-label="Filter by index" value={only} onChange={(e) => setOnly(e.target.value)}>
                  <option value="all">Every stock ({session?.quotes.length ?? 0})</option>
                  {session?.filters.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name} ({session.quotes.filter((q) => q.indices.includes(f.id)).length})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <input
                  type="search"
                  value={search}
                  aria-label="Find a stock"
                  placeholder="Find symbol or name"
                  onChange={(e) => setSearch(e.target.value)}
                />
              </label>
              <small className="hint">
                {shown.length} shown{!fromApi && " · no book in a downloaded file"}
              </small>
            </div>

            <div className="poscroll">
              <table className="potable">
                <thead>
                  <tr>
                    <th onClick={() => by("symbol")}>Stock{arrow("symbol")}</th>
                    <th className="n">Prev close</th>
                    <th className="n">Opens at</th>
                    <th className="n" onClick={() => by("pct_change")}>
                      Change{arrow("pct_change")}
                    </th>
                    <th className="gapcol" aria-hidden="true" />
                    <th className="n" onClick={() => by("final_quantity")}>
                      Quantity{arrow("final_quantity")}
                    </th>
                    <th className="n" onClick={() => by("turnover_cr")}>
                      ₹ cr{arrow("turnover_cr")}
                    </th>
                    <th
                      className="n"
                      onClick={() => by("pressure")}
                      title="Total buy quantity over total sell quantity left in the book"
                    >
                      Buy / sell{arrow("pressure")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((q) => {
                    const p = pressure(q);
                    const expanded = open === q.symbol && q.book.length > 0;
                    return (
                      <Fragment key={q.symbol}>
                        <tr
                          className={[q.book.length ? "click" : "", expanded ? "on" : ""].join(" ")}
                          title={q.book.length ? "Show the auction book" : undefined}
                          onClick={() =>
                            q.book.length && setOpen((o) => (o === q.symbol ? null : q.symbol))
                          }
                        >
                          <td>
                            <b>{q.symbol}</b>
                            {q.name && <small title={q.name}>{q.name}</small>}
                          </td>
                          <td className="n">{num(q.prev_close)}</td>
                          <td className="n">{num(q.final_price)}</td>
                          <td className={`n ${tone(q.change)}`}>
                            {num(q.change)} <small>{pct(q.pct_change)}</small>
                          </td>
                          <td className="gapcol" aria-hidden="true">
                            <Gap pct={q.pct_change} scale={scale} />
                          </td>
                          <td className="n">{qty(q.final_quantity)}</td>
                          <td className="n">{num(q.turnover_cr)}</td>
                          <td className="n">
                            {p === null ? "—" : `${p.toFixed(2)}×`}
                          </td>
                        </tr>
                        {expanded && (
                          <tr className="bookrow">
                            <td colSpan={8}>
                              <Book levels={q.book} quote={q} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                  {session && shown.length === 0 && (
                    <tr>
                      <td colSpan={8} className="none">
                        nothing matches
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

function Breadth({ up, down, flat }: { up: number; down: number; flat: number }) {
  const total = up + down + flat || 1;
  return (
    <span className="pobreadth" aria-hidden="true">
      <i className="up" style={{ width: `${(up / total) * 100}%` }} />
      <i className="flat" style={{ width: `${(flat / total) * 100}%` }} />
      <i className="dn" style={{ width: `${(down / total) * 100}%` }} />
    </span>
  );
}

/** The opening gap as a bar either side of zero. */
function Gap({ pct: p, scale }: { pct: number; scale: number }) {
  const w = Math.min(50, (Math.abs(p) / scale) * 50);
  return (
    <span className="pogap">
      <i className={tone(p)} style={p >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
    </span>
  );
}

/** The ten levels the auction was struck from, buyers left and sellers right. */
function Book({ levels, quote }: { levels: PreOpenLevel[]; quote: PreOpenQuote }) {
  const most = Math.max(1, ...levels.map((l) => Math.max(l.buy_qty, l.sell_qty)));
  const rows = levels.slice().sort((a, b) => b.price - a.price);
  return (
    <div className="pobook">
      <table>
        <thead>
          <tr>
            <th className="n">Buy qty</th>
            <th className="n">Price</th>
            <th>Sell qty</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((l) => (
            <tr key={l.price} className={l.is_iep ? "iep" : ""}>
              <td className="n buy">
                {l.buy_qty > 0 && (
                  <>
                    <i style={{ width: `${(l.buy_qty / most) * 100}%` }} />
                    <span>{qty(l.buy_qty)}</span>
                  </>
                )}
              </td>
              <td className="n px">
                {num(l.price)}
                {l.is_iep && <small> settled</small>}
              </td>
              <td className="sell">
                {l.sell_qty > 0 && (
                  <>
                    <i style={{ width: `${(l.sell_qty / most) * 100}%` }} />
                    <span>{qty(l.sell_qty)}</span>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <dl>
        <dt>Total buy</dt>
        <dd>{qty(quote.total_buy_qty)}</dd>
        <dt>Total sell</dt>
        <dd>{qty(quote.total_sell_qty)}</dd>
        <dt>52-week range</dt>
        <dd>
          {num(quote.year_low)} – {num(quote.year_high)}
        </dd>
        {quote.industry && (
          <>
            <dt>Industry</dt>
            <dd>{quote.industry}</dd>
          </>
        )}
      </dl>
    </div>
  );
}
