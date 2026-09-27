import { useEffect, useState } from "react";
import type { Alert } from "../api";

interface Props {
  alerts: readonly Alert[];
  /** Conditions true right now, which is not the same as alerts logged. */
  activeCount: number;
  /** Whether the backend is watching at all. An empty log means "nothing is
      wrong" only if something is looking. */
  watching: boolean;
  open: boolean;
  onToggle: () => void;
}

/** The newest alert already seen, so a badge counts what arrived since. */
const KEY = "optiondesk-alerts-seen";

/* Epoch milliseconds, and 0 for "nothing seen yet" - the same units an alert
   carries. It was read back and compared as a string, which made the badge
   count every alert whenever the stored value and the alert's own timestamp
   disagreed about type. */
function lastSeen(): number {
  try {
    return Number(localStorage.getItem(KEY)) || 0;
  } catch {
    return 0;
  }
}

export function markSeen(alerts: readonly Alert[]): void {
  const newest = alerts[0]?.at;
  if (!newest) return;
  try {
    localStorage.setItem(KEY, String(newest));
  } catch {
    // forgetting what was read costs a badge, not an alert
  }
}

/**
 * Alerts, as a count rather than a column.
 *
 * These go to Telegram the moment they fire, so the log here was never how
 * anyone found out — it was a second copy of something already read on a phone,
 * and it was taking a third of the screen to be one. A count in the header says
 * the same thing in the space it deserves, and opens the log when it matters.
 *
 * Badged on what arrived since it was last opened, not on the total. A number
 * that only ever grows stops being read within a day.
 */
export function AlertsBell({ alerts, activeCount, watching, open, onToggle }: Props) {
  const [seen, setSeen] = useState(lastSeen);

  useEffect(() => {
    if (open) {
      markSeen(alerts);
      setSeen(alerts[0]?.at ?? seen);
    }
    // Only when the panel opens, or reading it would race the poll behind it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const unread = seen ? alerts.filter((a) => a.at > seen).length : alerts.length;
  const tone = !watching ? "off" : unread ? "new" : activeCount ? "live" : "";

  return (
    <button
      className={`bell ${tone}${open ? " on" : ""}`}
      onClick={onToggle}
      aria-expanded={open}
      title={
        !watching
          ? "Nothing is watching: the backend raises these, so they stop when it does"
          : unread
            ? `${unread} since you last looked`
            : activeCount
              ? `${activeCount} condition${activeCount === 1 ? "" : "s"} true now`
              : "Alerts"
      }
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path
          d="M8 1.5a3.6 3.6 0 0 0-3.6 3.6v2.3L3 10.2h10l-1.4-2.8V5.1A3.6 3.6 0 0 0 8 1.5Z"
          className="body"
        />
        <path d="M6.4 11.6a1.6 1.6 0 0 0 3.2 0" className="clapper" />
      </svg>
      {unread > 0 && <span className="count">{unread > 99 ? "99+" : unread}</span>}
      {unread === 0 && activeCount > 0 && <span className="live" aria-hidden="true" />}
    </button>
  );
}
