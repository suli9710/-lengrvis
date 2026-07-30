import type {
  BrowserActivityEvent,
  BrowserAction,
  BrowserHostSnapshot,
  BrowserLinkResult,
  BrowserPageSnapshot,
  BrowserReplayExport,
  BrowserSession
} from "../../../shared/browserTypes";
import type {
  BackendBrowserActivityEnvelope,
  BackendBrowserActivityEvent,
  BackendBrowserLink,
  BackendBrowserPage,
  BackendBrowserReplayExport,
  BackendBrowserSession
} from "./browserBackendTypes";
import { optionalString } from "./mapperPrimitives";

export function mapBrowserLink(link: BackendBrowserLink): BrowserLinkResult {
  return {
    title: String(link.title ?? link.url ?? ""),
    url: String(link.url ?? "")
  };
}

export function mapBrowserPage(page: BackendBrowserPage): BrowserPageSnapshot {
  return {
    ok: Boolean(page.ok),
    url: String(page.url ?? ""),
    title: String(page.title ?? ""),
    text: String(page.text ?? ""),
    links: (page.links ?? []).map(mapBrowserLink),
    truncated: page.truncated,
    adapter: page.adapter,
    error: page.error
  };
}

export function mapBrowserSession(session: BackendBrowserSession): BrowserSession {
  return {
    id: String(session.id ?? ""),
    task_id: optionalString(session.task_id),
    current_url: String(session.current_url ?? session.url ?? ""),
    title: String(session.title ?? ""),
    status: String(session.status ?? "idle"),
    mode: String(session.mode ?? "watch"),
    created_at: String(session.created_at ?? new Date().toISOString()),
    updated_at: String(session.updated_at ?? new Date().toISOString()),
    paused: Boolean(session.paused),
    takeover: Boolean(session.takeover),
    last_observation: session.last_observation ?? null
  };
}

export function mapBrowserActivityEvent(event: BackendBrowserActivityEvent): BrowserActivityEvent {
  return {
    id: String(event.id ?? crypto.randomUUID()),
    session_id: String(event.session_id ?? ""),
    task_id: optionalString(event.task_id),
    step_id: optionalString(event.step_id),
    type: String(event.type ?? "event"),
    action: isBrowserAction(event.action) ? event.action : undefined,
    url: optionalString(event.url),
    title: optionalString(event.title),
    risk_level: optionalString(event.risk_level),
    verdict: optionalString(event.verdict),
    ok: event.ok !== false,
    error: optionalString(event.error),
    screenshot_url: optionalString(event.screenshot_url),
    created_at: String(event.created_at ?? new Date().toISOString())
  };
}

export function mapBrowserActivityEnvelope(data: BackendBrowserActivityEnvelope): BrowserActivityEvent {
  return mapBrowserActivityEvent(data.event ?? {
    id: crypto.randomUUID(),
    session_id: data.session?.id,
    type: data.ok === false ? "observe.failed" : "observe",
    action: { kind: "observe" },
    url: data.url ?? data.session?.current_url ?? data.session?.url,
    title: data.title ?? data.session?.title,
    ok: data.ok !== false,
    error: data.error,
    created_at: new Date().toISOString()
  });
}

export function mapBrowserReplayExport(data: BackendBrowserReplayExport): BrowserReplayExport {
  return {
    ok: data.ok !== false,
    url: optionalString(data.url),
    path: optionalString(data.path),
    session: data.session ? mapBrowserSession(data.session) : undefined,
    events: Array.isArray(data.events) ? data.events.map(mapBrowserActivityEvent) : undefined,
    error: optionalString(data.error)
  };
}

export function isBrowserAction(value: unknown): value is BrowserAction {
  return Boolean(value && typeof value === "object" && typeof (value as { kind?: unknown }).kind === "string");
}
