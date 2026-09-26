import { SEVERITY_LABEL } from "../alerts";
import type { Alert, Limits } from "../alerts";
import { clockIST } from "../format";

interface Props {
  alerts: readonly Alert[];
  activeCount: number;
  limits: Limits;
  onLimits: (next: Limits) => void;
  onClear: () => void;
}

const FIELDS: { key: keyof Limits; label: string; step: number; suffix?: string }[] = [
  { key: "target", label: "Profit target", step: 1000, suffix: "₹" },
  { key: "dailyLoss", label: "Daily loss", step: 1000, suffix: "₹" },
  { key: "maxLoss", label: "Worst case per structure", step: 1000, suffix: "₹" },
  { key: "shortDelta", label: "Short tested at delta", step: 0.05 },
  { key: "expiryDays", label: "Warn this many days out", step: 1 },
];

/**
 * The alert log, and the thresholds it watches.
 *
 * The limits sit in the same panel as the alerts they produce, so changing a
 * number and seeing what it fires is one glance rather than two places.
 */
export function AlertsPanel({ alerts, activeCount, limits, onLimits, onClear }: Props) {
  return (
    <section className="panel a-alerts">
      <div className="ph">
        <h2>Alerts</h2>
        <span className="sub">
          {activeCount ? `${activeCount} live` : "nothing live"} · {alerts.length} today
        </span>
        <span className="sp" />
        <button className="xbtn" onClick={onClear} disabled={!alerts.length}>
          Clear log
        </button>
      </div>

      <div className="pb">
        {!alerts.length && (
          <p className="empty">
            Nothing yet. An alert is written the moment a condition below becomes true, and not
            again while it stays true.
          </p>
        )}
        {/* A list, not a table: in a rail this narrow a message column is
            about 200px wide and every line wraps three deep. Given the full
            width the message fits on one line, or two at worst. */}
        {alerts.map((a) => (
          <div className={`alert ${a.severity}`} key={`${a.key}-${a.at}`}>
            <div className="amsg">{a.message}</div>
            {/* `a.subject` holds which structure it concerns, but the message
                already names the strike, so printing it too was the same long
                line repeated on every row. */}
            <div className="ameta">
              <span className={`sev ${a.severity}`}>{SEVERITY_LABEL[a.severity]}</span>
              <span className="atime">{clockIST(a.at)}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="sec">Watch for</div>
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
      <p className="dim" style={{ margin: 0, padding: "6px 9px 9px", lineHeight: 1.4 }}>
        Alerts are evaluated in this tab, from the data on screen. Close it and nothing is
        watching.
      </p>
    </section>
  );
}
