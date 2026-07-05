export interface BackendBrowserLink {
  title?: string;
  url?: string;
}

export interface BackendBrowserPage {
  ok?: boolean;
  url?: string;
  title?: string;
  text?: string;
  links?: BackendBrowserLink[];
  truncated?: boolean;
  adapter?: string;
  error?: string;
}

export interface BackendBrowserSession {
  id?: string;
  task_id?: string | null;
  current_url?: string;
  url?: string;
  title?: string;
  status?: string;
  mode?: string;
  created_at?: string;
  updated_at?: string;
  paused?: boolean;
  takeover?: boolean;
  last_observation?: string | Record<string, unknown> | null;
}

export interface BackendBrowserActivityEvent {
  id?: string;
  session_id?: string;
  task_id?: string | null;
  step_id?: string | null;
  type?: string;
  action?: unknown;
  url?: string;
  title?: string;
  risk_level?: string;
  verdict?: string;
  ok?: boolean;
  error?: string;
  screenshot_url?: string;
  created_at?: string;
}

export interface BackendBrowserActivityEnvelope extends BackendBrowserActivityEvent {
  ok?: boolean;
  event?: BackendBrowserActivityEvent;
  session?: BackendBrowserSession;
}

export interface BackendBrowserSessions {
  ok?: boolean;
  sessions?: BackendBrowserSession[];
  error?: string;
}

export interface BackendBrowserEvents {
  ok?: boolean;
  events?: BackendBrowserActivityEvent[];
  error?: string;
}

export interface BackendBrowserReplayExport {
  ok?: boolean;
  url?: string;
  path?: string;
  events?: BackendBrowserActivityEvent[];
  session?: BackendBrowserSession;
  error?: string;
}

export type BackendBrowserSessionStreamEvent =
  | { type: "connected"; session_id: string }
  | { type: "heartbeat"; session_id?: string }
  | { type: "session"; session: BackendBrowserSession }
  | { type: "event"; event: BackendBrowserActivityEvent }
  | BackendBrowserActivityEvent;

export interface BackendBrowserLinks {
  ok?: boolean;
  url?: string;
  title?: string;
  links: BackendBrowserLink[];
  error?: string;
}
