import { useState } from "react";
import type { Alert, Limits, Watch, WatchDirection, WatchKind } from "../api";
import { SEVERITY_LABEL } from "../api";
import { clockIST } from "../format";

interface Props {
  alerts: readonly Alert[];
  activeCount: number;
  limits: Limits;
  watches: readonly Watch[];
  /** Symbols worth offering for a price level — the underlyings on screen. */
  symbols: readonly { id: string; name: string }[];
  telegram: boolean;
  watching: boolean;
  /** Whether the risk thresholds below apply to what is on screen. They are read
      off the options book - its P&L, its structures, its short legs - so on any
      other desk they would be somebody else's numbers to edit. */
  showThresholds: boolean;
  trouble: string | null;
  onLimits: (next: Limits) => void;
  onClear: () => void;
  onAddWatch: (watch: {
    kind: WatchKind;
    direction: WatchDirection;
    level: number;
    symbol?: string | null;
    note?: string;
  }) => void;
  onToggleWatch: (id: number, enabled: boolean) => void;
  onDeleteWatch: (id: number) => void;
}

const FIELDS: { key: keyof Limits; label: string; step: number }[] = [
  { key: "target", label: "Profit target", step: 1000 },
  { key: "daily_loss", label: "Daily loss", step: 1000 },
  { key: "max_loss", label: "Worst case per structure", step: 1000 },
  { key: "short_delta", label: "Short tested at delta", step: 0.05 },
  { key: "expiry_days", label: "Warn this many days out", step: 1 },
];

/**
 * The alert log, the thresholds behind it, and the levels you asked about.
 *
 * A view now. The engine that raised these ran in this tab until recently,
 * which meant nothing watched once it was closed and a phone could never be
 * told anything. It runs in the backend, so this panel reads rather than
 * decides — which is also why the log is the same in every browser you open.
 */
export function AlertsPanel({
  alerts,
  activeCount,
  limits,
  watches,
  symbols,
  telegram,
  watching,
  showThresholds,
  trouble,
  onLimits,
  onClear,
  onAddWatch,
  onToggleWatch,
  onDeleteWatch,
}: Props) {
  const [kind, setKind] = useState<WatchKind>("price");
  const [direction, setDirection] = useState<WatchDirection>("above");
  const [symbol, setSymbol] = useState(symbols[0]?.id ?? "");
  const [level, setLevel] = useState("");
  const [note, setNote] = useState("");

  const levelValue = Number(level);
  const canAdd = level.trim() !== "" && Number.isFinite(levelValue) && (kind === "pnl" || !!symbol);

  const submit = () => {
    if (!canAdd) return;
    onAddWatch({
      kind,
      direction,
      level: levelValue,
      symbol: kind === "price" ? symbol : null,
      note: note.trim(),
    });
    setLevel("");
    setNote("");
  };

  return (
    <section className="panel a-alerts">
      <div className="ph">
        <h2>Alerts</h2>
        <span className="sub">
          {activeCount ? `${activeCount} live` : "nothing live"} · {alerts.length} logged
        </span>
        <span className="sp" />
        <button className="xbtn" onClick={onClear} disabled={!alerts.length}>
          Clear log
        </button>
      </div>

      <div className="pb">
        {/* An empty log means "nothing is wrong" only if something is looking,
            so say which it is rather than implying calm. */}
        {!watching && (
          <p className="empty warnish">
            Nothing is watching. The backend raises these, so they stop when it does.
          </p>
        )}
        {watching && trouble && <p className="empty warnish">Last alert pass failed: {trouble}</p>}

        {/* The empty state has to agree with the count beside the heading. It
            once read "Nothing yet" while the header said several were live,
            which is the panel calling itself a liar. */}
        {!alerts.length && watching && !trouble && (
          <p className="empty">
            {activeCount
              ? `${activeCount} ${activeCount === 1 ? "condition is" : "conditions are"} live, but the log was cleared. Each is written again on the next pass.`
              : "Nothing yet. An alert is written the moment a condition below becomes true, and not again while it stays true."}
          </p>
        )}

        {/* A list, not a table: in a rail this narrow a message column is
            about 200px wide and every line wraps three deep. */}
        {alerts.map((a) => (
          <div className={`alert ${a.severity}`} key={`${a.key}-${a.at}`}>
            <div className="amsg">{a.message}</div>
            <div className="ameta">
              <span className={`sev ${a.severity}`}>{SEVERITY_LABEL[a.severity]}</span>
              <span className="atime">{clockIST(a.at)}</span>
              {/* Only shown when there is somewhere to send: a missing tick
                  with Telegram off would look like a failure rather than a
                  setting. */}
              {telegram && (
                <span className="atime" title={a.notified_at ? "Sent to Telegram" : "Not sent yet"}>
                  {a.notified_at ? "sent" : "queued"}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="sec">
        Tell me when<span className="dim">a price or the book goes through a level</span>
      </div>
      <div className="watchadd">
        <select value={kind} onChange={(e) => setKind(e.target.value as WatchKind)}
                aria-label="What to watch">
          <option value="price">Price</option>
          <option value="pnl">Net P&amp;L</option>
        </select>
        {kind === "price" && (
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Symbol">
            {symbols.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        )}
        <select
          value={direction}
          onChange={(e) => setDirection(e.target.value as WatchDirection)}
          aria-label="Direction"
        >
          <option value="above">goes above</option>
          <option value="below">goes below</option>
        </select>
        <input
          type="number"
          value={level}
          placeholder="level"
          aria-label="Level"
          onChange={(e) => setLevel(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <input
          type="text"
          value={note}
          placeholder="note (optional)"
          aria-label="Note"
          maxLength={80}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <button className="xbtn" onClick={submit} disabled={!canAdd}>
          Add
        </button>
      </div>

      {watches.length > 0 && (
        <table className="lim tight">
          <tbody>
            {watches.map((w) => (
              <tr key={w.id} className={w.enabled ? undefined : "off"}>
                <td className="l">
                  {w.note || (w.kind === "pnl" ? "Net P&L" : shortSymbol(w.symbol))}{" "}
                  <span className="dim">
                    {w.direction} {w.kind === "pnl" ? `₹${w.level.toLocaleString("en-IN")}` : w.level.toLocaleString("en-IN")}
                  </span>
                </td>
                <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                  <button
                    className="xbtn"
                    title={w.enabled ? "Stop watching, keep the level" : "Watch this again"}
                    onClick={() => onToggleWatch(w.id, !w.enabled)}
                  >
                    {w.enabled ? "Pause" : "Resume"}
                  </button>{" "}
                  <button className="xbtn danger" onClick={() => onDeleteWatch(w.id)}>
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Named for what they are. "Watch for" read as a heading for the whole
          panel, which made five live thresholds look like decoration. */}
      {showThresholds && (
        <>
          <div className="sec">
            Risk thresholds<span className="dim">on your option structures</span>
          </div>
          <table className="lim tight">
            <tbody>
              {FIELDS.map((f) => (
                <tr key={f.key}>
                  <td className="l">{f.label}</td>
                  <td>
                    <input
                      type="number"
                      step={f.step}
                      min={0}
                      value={limits[f.key]}
                      aria-label={f.label}
                      onChange={(e) => {
                        const v = Number(e.target.value);
                        onLimits({ ...limits, [f.key]: Number.isFinite(v) ? Math.abs(v) : 0 });
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="dim" style={{ margin: 0, padding: "6px 9px 9px", lineHeight: 1.4 }}>
        {telegram
          ? "Raised by the backend and sent to Telegram, so they fire with this tab closed."
          : "Raised by the backend, so they fire with this tab closed. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to have them sent to your phone."}
      </p>
    </section>
  );
}

/** "NSE:NIFTY50-INDEX" is what the broker calls it, not what anyone says. */
function shortSymbol(symbol: string | null): string {
  if (!symbol) return "price";
  return symbol.split(":").pop()!.replace(/-INDEX$/, "");
}
