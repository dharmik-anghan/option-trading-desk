interface Props {
  sessions: string[];
  closeOutside: boolean;
  onSessions: (next: string[]) => void;
  onCloseOutside: (next: boolean) => void;
}

/**
 * When the strategy is allowed to trade.
 *
 * Named centres rather than hours you type, because the hours are the easy part
 * and the timezone is not: London opens at 08:00 local whether or not the clocks
 * have gone forward, so a window written in UTC slides by an hour twice a year,
 * silently, right through the period being measured.
 *
 * Choosing two means either, not both — which is what picking London and New York
 * obviously means, and would otherwise be true only during their four-hour
 * overlap.
 */
const SESSIONS = [
  { id: "london", name: "London", hours: "08:00–16:30 London" },
  { id: "newyork", name: "New York", hours: "08:00–17:00 New York" },
  { id: "tokyo", name: "Tokyo", hours: "09:00–18:00 Tokyo" },
  { id: "sydney", name: "Sydney", hours: "07:00–16:00 Sydney" },
  { id: "india", name: "India", hours: "09:15–15:30 NSE" },
] as const;

export function SessionPicker({ sessions, closeOutside, onSessions, onCloseOutside }: Props) {
  const toggle = (id: string) =>
    onSessions(sessions.includes(id) ? sessions.filter((s) => s !== id) : [...sessions, id]);

  return (
    <section className="sessions">
      <header>
        <h3>Trade during</h3>
        <span className="sp" />
        {sessions.length > 0 && (
          <button onClick={() => onSessions([])}>Any hour</button>
        )}
      </header>

      <div className="picks">
        {SESSIONS.map((s) => (
          <button
            key={s.id}
            className={sessions.includes(s.id) ? "pick on" : "pick"}
            onClick={() => toggle(s.id)}
            title={s.hours}
          >
            {s.name}
          </button>
        ))}
      </div>

      <p className="say">
        {sessions.length === 0
          ? "Every hour of every day, which is right for a perpetual and wrong for almost everything else."
          : sessions.length === 1
            ? `${SESSIONS.find((s) => s.id === sessions[0])?.hours}, weekdays. Daylight saving is followed.`
            : "Any one of the chosen sessions, weekdays. Daylight saving is followed."}
      </p>

      {sessions.length > 0 && (
        <label className="also">
          <input
            type="checkbox"
            checked={closeOutside}
            onChange={(e) => onCloseOutside(e.target.checked)}
          />
          Close whatever is open when the session ends
          <small>
            the difference between trading a session and merely opening in one
          </small>
        </label>
      )}
    </section>
  );
}
