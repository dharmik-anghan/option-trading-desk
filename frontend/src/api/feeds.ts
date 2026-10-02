import { getJson } from "./http";

export interface CalendarEvent {
  day: string;
  name: string;
  label: string;
  importance: "H" | "M" | "L";
  coverage: "india" | "global";
  country: string | null;
}

export interface EventsResponse {
  events: CalendarEvent[];
  /** Seconds since the calendar was refreshed; null means never. */
  age_seconds: number | null;
  /** Set when the refresh failed. The events may still be usable, just stale. */
  error: string | null;
}

export interface Headline {
  title: string;
  link: string;
  source: string;
  published: string | null;
  /** Its publisher's beat — taken from the source, not read out of the title. */
  topics: string[];
}

export interface NewsResponse {
  headlines: Headline[];
  age_seconds: number | null;
  error: string | null;
  /** Every topic the desk has a source for, so the filter offers what exists. */
  available_topics: string[];
  /** Topics per publisher, for naming what a filter would include. */
  sources: Record<string, string[]>;
}

export function getEvents(days = 45, importance = "HM"): Promise<EventsResponse> {
  return getJson<EventsResponse>(`/api/events?days=${days}&importance=${importance}`);
}

export function getNews(limit = 40, topics: readonly string[] = []): Promise<NewsResponse> {
  const filter = topics.length ? `&topics=${encodeURIComponent(topics.join(","))}` : "";
  return getJson<NewsResponse>(`/api/news?limit=${limit}${filter}`);
}

/**
 * Whether a calendar entry bears on an Indian index position.
 *
 * Kept on this side purely for the top bar's "next event", which picks one from
 * the calendar it already has rather than asking for it. The backend applies the
 * same rule when it decides what to alert on - `alerting/rules.py` - and that
 * copy is the one that matters.
 */
export function affectsIndia(event: CalendarEvent): boolean {
  if (event.importance !== "H") return false;
  return event.coverage === "india" || event.country === "United States";
}
