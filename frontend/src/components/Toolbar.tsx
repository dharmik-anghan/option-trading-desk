import { UNDERLYINGS } from "../api";
import type {
  CalendarEvent,
  Health,
  MarketContext,
  PerpsDesk,
  PortfolioResponse,
  Venue,
} from "../api";
import { ContextStrip } from "./ContextStrip";
import { VenueSwitch } from "./VenueSwitch";
import { dayIST, dir, num, pct, signed } from "../format";

interface Props {
  symbol: string;
  spot: number | null;
  /** Day change on the spot, so the headline price says which way it is going. */
  change: number | null;
  changePct: number | null;
  /** The next release that bears on an Indian index, if one is known. */
  nextEvent: CalendarEvent | null;
  portfolio: PortfolioResponse | null;
  health: Health | null;
  /** The most telling failure across the panels, if any. */
  trouble: { text: string; transient: boolean } | null;
  stale: boolean;
  agoSeconds: number | null;
  paused: boolean;
  onPause: () => void;
  theme: "dark" | "light";
  onTheme: () => void;
  context: MarketContext | null;
  venues: readonly Venue[];
  venueId: string;
  onVenue: (id: string) => void;
  /** Set only on the perpetuals desk, whose headline is a different shape: no
      booked P&L to read, and prices in a different currency from the account. */
  perps: PerpsDesk | null;
}

export function Toolbar({
  symbol,
  spot,
  change,
  changePct,
  nextEvent,
  portfolio,
  health,
  trouble,
  stale,
  agoSeconds,
  paused,
  onPause,
  theme,
  onTheme,
  context,
  venues,
  venueId,
  onVenue,
  perps,
}: Props) {
  const name = UNDERLYINGS.find((u) => u.id === symbol)?.name ?? symbol;
  const net = portfolio?.total_pnl ?? null;

  // Say what is wrong, in order of how much it matters. Before this a broker
  // failure just froze the numbers with no explanation at all.
  const rateLimited = trouble?.transient || health?.rate_limited === true;
  const blocking = trouble && !trouble.transient;

  const dotClass = blocking ? "dot bad" : paused || rateLimited || stale ? "dot off" : "dot";
  const feedText = paused
    ? "Paused"
    : blocking
      ? trouble.text
      : rateLimited
        ? "Rate limited — figures a few seconds behind"
        : agoSeconds === null
          ? "Connecting…"
          : `Updated ${agoSeconds}s ago`;

  // One card, not two. As separate panels the toolbar and the market strip had
  // a border and a gap between them, which on a wide screen read as a band of
  // empty white across the top of the desk.
  return (
    <header className="topbar">
      <div className="tb">
      {/* The title is the switch. First thing in the bar, because it reframes
          everything to the right of it: the instruments, the panels, and the
          currency the numbers are in. */}
      <VenueSwitch venues={venues} selected={venueId} onSelect={onVenue} />

      {!perps && (
      <div className="tbk spot">
        <small>{name} spot</small>
        <b>
          {spot === null ? "—" : num(spot)}
          {change !== null && changePct !== null && (
            <span className={`chg ${dir(change)}`}>
              {signed(change, 2)} {pct(changePct, 2)}
            </span>
          )}
        </b>
      </div>
      )}

      {/* On the perpetuals desk the two currencies have to be said out loud.
          Prices are in USDT and the account margins in INR, so a figure here
          without its unit is a figure that could be read as either. */}
      {perps && (
        <div className="tbk">
          <small>Quoted in</small>
          <b>
            {perps.quote_currency}
            <span className="chg dim"> account {perps.money_currency}</span>
          </b>
        </div>
      )}
      {perps && (
        <div className="tbk">
          <small>Stream</small>
          <b className={perps.stream.connected ? undefined : "dn"}>
            {perps.stream.connected ? "live" : "down"}
            <span className="chg dim"> {perps.stream.ticks.toLocaleString("en-IN")} ticks</span>
          </b>
        </div>
      )}

      {/* The next scheduled release goes in the middle, which was otherwise a
          few hundred pixels of nothing on a wide screen. It is also the one
          thing here you cannot work out from the numbers either side of it. */}
      {nextEvent ? (
        <div className="tbevent" title={`${nextEvent.label} — ${nextEvent.day}`}>
          <small>Next event · {dayIST(nextEvent.day)}</small>
          <b>{nextEvent.label}</b>
        </div>
      ) : (
        <div className="tbsp" />
      )}

      <div className="tbsp" />

      {/* Booked + MTM = Net, so they read as one cluster: no dividers between
          the two parts, one divider before the sum. */}
      {!perps && (
      <div className="tbgroup">
        <div className="tbk">
          <small>Booked</small>
          <b className={portfolio ? dir(portfolio.realized_pnl) : undefined}>
            {portfolio ? signed(portfolio.realized_pnl) : "—"}
          </b>
        </div>
        <div className="tbk">
          <small>MTM</small>
          <b className={portfolio ? dir(portfolio.unrealized_pnl) : undefined}>
            {portfolio ? signed(portfolio.unrealized_pnl) : "—"}
          </b>
        </div>
        <div className="tbk hero">
          <small>Net today</small>
          <b className={net !== null ? dir(net) : undefined}>{net !== null ? signed(net) : "—"}</b>
        </div>
      </div>
      )}

      <div className="tbtools">
        <div className={`feed${blocking ? " bad" : ""}`} title={feedText}>
          <span className={dotClass} />
          <span className="feedtext">{feedText}</span>
        </div>
        <button className="tbtn" onClick={onPause} aria-label={paused ? "Resume updates" : "Pause updates"}>
          {paused ? "▶" : "❚❚"}
        </button>
        <button
          className="tbtn"
          onClick={onTheme}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
        >
          ◐
        </button>
      </div>
      </div>
      {!perps && <ContextStrip context={context} />}
    </header>
  );
}
