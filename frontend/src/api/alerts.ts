import { del, getJson, postJson, request } from "./http";

export type Severity = "risk" | "warn" | "target" | "info";

export interface Alert {
  /** Stable per condition, so one condition is one alert however often it is polled. */
  key: string;
  severity: Severity;
  /** Which structure it concerns, or null for account-wide ones. */
  subject: string | null;
  message: string;
  /** Epoch milliseconds. */
  at: number;
  /** When it was delivered off-screen, or null if it has not been. */
  notified_at: number | null;
}

export interface Limits {
  target: number;
  daily_loss: number;
  max_loss: number;
  short_delta: number;
  expiry_days: number;
}

export type WatchKind = "price" | "pnl";

export type WatchDirection = "above" | "below";

/** A level you asked to be told about. */
export interface Watch {
  id: number;
  kind: WatchKind;
  symbol: string | null;
  direction: WatchDirection;
  level: number;
  note: string;
  enabled: boolean;
}

export interface NewWatch {
  kind: WatchKind;
  direction: WatchDirection;
  level: number;
  symbol?: string | null;
  note?: string;
}

/** Whether anything is actually watching. An empty log only means calm if so. */
export interface WatcherStatus {
  running: boolean;
  last_run_at: number | null;
  last_error: string | null;
  /** Whether alerts are being delivered anywhere off-screen. */
  telegram: boolean;
}

export interface AlertsResponse {
  alerts: Alert[];
  /** Conditions true right now, whether or not they fired this pass. */
  active: string[];
  limits: Limits;
  watches: Watch[];
  watcher: WatcherStatus;
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  risk: "RISK",
  warn: "WARN",
  target: "TARGET",
  info: "INFO",
};

export function getAlerts(limit = 200): Promise<AlertsResponse> {
  return getJson<AlertsResponse>(`/api/alerts?limit=${limit}`);
}

export function clearAlerts(): Promise<void> {
  return request<void>("POST", "/api/alerts/clear");
}

export function addWatch(watch: NewWatch): Promise<Watch> {
  return postJson<NewWatch, Watch>("/api/alerts/watches", watch);
}

export function setWatchEnabled(id: number, enabled: boolean): Promise<Watch[]> {
  return request<Watch[]>("POST", `/api/alerts/watches/${id}/enabled?enabled=${enabled}`);
}

export function deleteWatch(id: number): Promise<void> {
  return del(`/api/alerts/watches/${id}`);
}
