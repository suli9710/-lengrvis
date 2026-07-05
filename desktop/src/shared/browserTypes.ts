export interface BrowserLinkResult {
  title: string;
  url: string;
}

export interface BrowserPageSnapshot {
  ok: boolean;
  url: string;
  title: string;
  text: string;
  links: BrowserLinkResult[];
  truncated?: boolean;
  adapter?: string;
  error?: string;
}

export type BrowserActionKind =
  | "open"
  | "navigate"
  | "click"
  | "fill"
  | "submit"
  | "scroll"
  | "wait"
  | "screenshot"
  | "observe"
  | "cua";

export interface BrowserAction {
  kind: BrowserActionKind;
  url?: string;
  selector?: string;
  text?: string;
  fields?: Record<string, string>;
  dry_run?: boolean;
  approved?: boolean;
  approval_id?: string;
  [key: string]: unknown;
}

export interface BrowserSession {
  id: string;
  task_id?: string;
  current_url: string;
  title: string;
  status: "idle" | "loading" | "running" | "paused" | "stopped" | "error" | "awaiting_approval" | string;
  mode: "watch" | "agent" | "takeover" | string;
  created_at: string;
  updated_at: string;
  paused: boolean;
  takeover: boolean;
  last_observation?: string | Record<string, unknown> | null;
}

export interface BrowserActivityEvent {
  id: string;
  session_id: string;
  task_id?: string;
  step_id?: string;
  type: string;
  action?: BrowserAction;
  url?: string;
  title?: string;
  risk_level?: "low" | "medium" | "high" | "critical" | string;
  verdict?: string;
  ok: boolean;
  error?: string;
  screenshot_url?: string;
  created_at: string;
}

export interface BrowserHostBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BrowserHostOpenRequest {
  sessionId?: string;
  taskId?: string;
  url?: string;
  title?: string;
  mode?: string;
}

export interface BrowserHostActionRequest {
  sessionId: string;
  action: BrowserAction;
}

export interface BrowserHostSnapshot {
  sessions: BrowserSession[];
  events: BrowserActivityEvent[];
  activeSessionId?: string | null;
  visible: boolean;
  hostAvailable: boolean;
  error?: string;
}

export interface BrowserHostActionResult {
  ok: boolean;
  session?: BrowserSession;
  event?: BrowserActivityEvent;
  snapshot?: BrowserHostSnapshot;
  error?: string;
}

export interface BrowserReplayExport {
  ok?: boolean;
  url?: string;
  path?: string;
  events?: BrowserActivityEvent[];
  session?: BrowserSession;
  error?: string;
}
