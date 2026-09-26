import { useState } from "react";
import type { CalendarEvent, EventsResponse, NewsResponse } from "../api";
import { affectsIndia } from "../api";
import { clockIST, dayIST } from "../format";

interface Props {
  news: NewsResponse | null;
  events: EventsResponse | null;
  /** Expiry dates you hold, so events landing before one can be marked. */
  expiryDays: string[];
  /** Which news topics are showing. Owned above, because the desk you are on
      decides the default and the panel does not know which desk that is. */
  topics: readonly string[];
  onTopics: (topics: string[]) => void;
}

type Tab = "news" | "events";

/** How each topic reads on a chip. The API names them; this names them for a
    person, and anything new falls back to its own id rather than vanishing. */
const TOPIC_LABEL: Record<string, string> = {
  india: "India",
  crypto: "Crypto",
  commodities: "Metals & oil",
};
type Scope = "relevant" | "india" | "all";

const SCOPES: [Scope, string][] = [
  ["relevant", "Moves India"],
  ["india", "India"],
  ["all", "Everything"],
];

function inScope(event: CalendarEvent, scope: Scope): boolean {
  if (scope === "all") return true;
  if (scope === "india") return event.coverage === "india";
  return affectsIndia(event);
}

/**
 * Headlines and the economic calendar.
 *
 * Both are scraped or syndicated from elsewhere and refreshed on a long timer,
 * so each tab says how old its data is. An event that falls before one of your
 * expiries is marked, because that is the only thing on this panel that
 * actually bears on a position you hold.
 */
export function NewsPanel({ news, events, expiryDays, topics, onTopics }: Props) {
  const [tab, setTab] = useState<Tab>("news");
  // The calendar carries several hundred entries and most of them do not move
  // an Indian index, so the useful view is the default rather than an option.
  const [scope, setScope] = useState<Scope>("relevant");

  const cached = tab === "news" ? news : events;
  const shown =
    tab === "events" && events ? events.events.filter((e) => inScope(e, scope)) : [];

  const lastExpiry = expiryDays.length ? expiryDays.slice().sort().at(-1) : undefined;

  return (
    <section className="panel grow">
      <div className="ph">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "news"} onClick={() => setTab("news")}>
            News
          </button>
          <button role="tab" aria-selected={tab === "events"} onClick={() => setTab("events")}>
            Calendar
          </button>
        </div>
        <span className="sp" />
        <span className="sub">{freshness(cached)}</span>
      </div>

      {cached?.error && <p className="err">{cached.error}</p>}

      {tab === "events" && (
        <div className="fbar">
          {SCOPES.map(([value, label]) => (
            <button key={value} aria-pressed={scope === value} onClick={() => setScope(value)}>
              {label}
            </button>
          ))}
        </div>
      )}

      {/* The topics come from the backend rather than being listed here, so a
          source added there shows up without a frontend change. Chips toggle,
          because reading two markets at once is a normal thing to want and an
          exclusive control would not allow it. Turning the last one off shows
          everything, which is the only sensible reading of "no filter". */}
      {tab === "news" && (news?.available_topics.length ?? 0) > 1 && (
        <div className="fbar">
          {(news?.available_topics ?? []).map((topic) => {
            const on = topics.includes(topic);
            return (
              <button
                key={topic}
                aria-pressed={on}
                title={sourcesFor(news, topic)}
                onClick={() =>
                  onTopics(
                    on ? topics.filter((t) => t !== topic) : [...topics, topic],
                  )
                }
              >
                {TOPIC_LABEL[topic] ?? topic}
              </button>
            );
          })}
          {topics.length > 0 && (
            <button className="fclear" onClick={() => onTopics([])} title="Show every source">
              All
            </button>
          )}
        </div>
      )}

      <div className="pb">
        {tab === "news" && !news && <p className="empty">Loading headlines…</p>}
        {tab === "news" && news && !news.headlines.length && (
          <p className="empty">
            No headlines for {topics.map((t) => TOPIC_LABEL[t] ?? t).join(" or ")}.
          </p>
        )}
        {tab === "news" &&
          news?.headlines.map((h) => (
            <a
              className="nitem"
              key={h.link + h.title}
              href={h.link}
              target="_blank"
              rel="noreferrer noopener"
            >
              <div className="nmeta">
                <span>{h.published ? clockIST(h.published) : "—"}</span>
                <span>{h.source}</span>
                {/* Only when the filter is off: with one topic selected every row
                    would carry the same word, which tells the reader nothing. */}
                {topics.length !== 1 &&
                  h.topics.map((t) => (
                    <span className="ntopic" key={t}>
                      {TOPIC_LABEL[t] ?? t}
                    </span>
                  ))}
              </div>
              <div className="nhead">{h.title}</div>
            </a>
          ))}

        {tab === "events" && !events && <p className="empty">Loading the calendar…</p>}
        {tab === "events" && events && !shown.length && (
          <p className="empty">Nothing scheduled in this window.</p>
        )}
        {tab === "events" &&
          shown.map((e) => {
            // only marked for events that would actually warrant a warning,
            // or every row gets a flag and the flag stops meaning anything
            const before =
              lastExpiry !== undefined && e.day <= lastExpiry && affectsIndia(e);
            return (
              <div className={`nitem${before ? " evh" : ""}`} key={e.day + e.label}>
                <div className="nmeta">
                  <span>{dayIST(e.day)}</span>
                  <span className={`imp ${e.importance}`}>{IMPORTANCE[e.importance]}</span>
                  <span>{e.coverage === "india" ? "India" : e.country ?? "Global"}</span>
                </div>
                <div className="nhead">{e.name}</div>
                {before && (
                  <div className="nmeta">
                    <b className="mkt">Falls before one of your expiries</b>
                  </div>
                )}
              </div>
            );
          })}
      </div>
    </section>
  );
}

const IMPORTANCE: Record<CalendarEvent["importance"], string> = {
  H: "HIGH",
  M: "MED",
  L: "LOW",
};

/** "updated 4 min ago", or why there is nothing to show. */
function freshness(cached: { age_seconds: number | null } | null): string {
  if (!cached || cached.age_seconds === null) return " ";
  const s = Math.round(cached.age_seconds);
  if (s < 90) return `updated ${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 90) return `updated ${m} min ago`;
  return `updated ${Math.round(m / 60)}h ago`;
}


/** Which publishers a topic covers, for the chip's tooltip. */
function sourcesFor(news: NewsResponse | null, topic: string): string {
  const names = Object.entries(news?.sources ?? {})
    .filter(([, topics]) => topics.includes(topic))
    .map(([name]) => name);
  return names.length ? names.join(", ") : topic;
}
