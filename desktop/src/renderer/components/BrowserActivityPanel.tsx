import {
  ArrowDownToLine,
  Bot,
  CircleAlert,
  Clock,
  Download,
  Eye,
  Globe2,
  Hand,
  Loader2,
  Pause,
  Play,
  Power,
  RotateCcw,
  ShieldAlert,
  Square,
  X
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { BrowserActivityEvent, BrowserHostSnapshot, BrowserSession } from "../../shared/types";
import type { MavrisApiClient, BrowserReplayExport, BackendBrowserSessionStreamEvent } from "../lib/apiClient";
import { Badge, Panel } from "./Panel";

interface BrowserActivityPanelProps {
  api: MavrisApiClient;
  sessions: BrowserSession[];
  events: BrowserActivityEvent[];
  hostSnapshot: BrowserHostSnapshot | null;
  activeSessionId: string | null;
  error: string | null;
  onSessionsChange: (sessions: BrowserSession[] | ((current: BrowserSession[]) => BrowserSession[])) => void;
  onEventsChange: (events: BrowserActivityEvent[] | ((current: BrowserActivityEvent[]) => BrowserActivityEvent[])) => void;
  onHostSnapshotChange: (snapshot: BrowserHostSnapshot | null) => void;
  onActiveSessionChange: (sessionId: string | null) => void;
  onErrorChange: (error: string | null) => void;
}

export function BrowserActivityPanel({
  api,
  sessions,
  events,
  hostSnapshot,
  activeSessionId,
  error,
  onSessionsChange,
  onEventsChange,
  onHostSnapshotChange,
  onActiveSessionChange,
  onErrorChange
}: BrowserActivityPanelProps) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [urlDraft, setUrlDraft] = useState("https://example.com");
  const [isWorking, setIsWorking] = useState(false);
  const [exportResult, setExportResult] = useState<string | null>(null);

  const hostSessions = hostSnapshot?.sessions ?? [];
  const mergedSessions = useMemo(() => mergeBrowserSessions(sessions, hostSessions), [hostSessions, sessions]);
  const mergedEvents = useMemo(() => mergeBrowserEvents(events, hostSnapshot?.events ?? []), [events, hostSnapshot?.events]);
  const activeSession =
    mergedSessions.find((session) => session.id === activeSessionId) ??
    mergedSessions.find((session) => session.id === hostSnapshot?.activeSessionId) ??
    mergedSessions[0] ??
    null;
  const activeSessionHasHost = Boolean(activeSession && hostSessions.some((session) => session.id === activeSession.id));
  const activeEvents = activeSession
    ? mergedEvents.filter((event) => event.session_id === activeSession.id).slice(0, 80)
    : mergedEvents.slice(0, 80);
  const hostControlDisabled = !activeSessionHasHost || isWorking;
  const sensitiveState = Boolean(
    activeSession?.paused ||
    activeSession?.status === "awaiting_approval" ||
    activeEvents.some((event) => isSensitiveEvent(event))
  );

  useEffect(() => {
    const unsubscribe = api.subscribeBrowserHostSnapshots((snapshot) => {
      onHostSnapshotChange(snapshot);
      if (snapshot.activeSessionId) {
        onActiveSessionChange(snapshot.activeSessionId);
      }
    });
    void refreshBrowserState();
    return unsubscribe;
  }, [api]);

  useEffect(() => {
    if (!activeSession?.id) return undefined;
    const unsubscribe = api.subscribeBrowserSession(activeSession.id, {
      onMessage: (message) => applyBrowserStreamEvent(message, onSessionsChange, onEventsChange)
    });
    return unsubscribe;
  }, [activeSession?.id, api, onEventsChange, onSessionsChange]);

  useEffect(() => {
    if (!stageRef.current || !activeSession) return undefined;

    let frameId = 0;
    const updateBounds = () => {
      if (!stageRef.current) return;
      const rect = stageRef.current.getBoundingClientRect();
      void api.setBrowserHostBounds({
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
      });
    };
    const scheduleUpdate = () => {
      window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(updateBounds);
    };
    const resizeObserver = new ResizeObserver(scheduleUpdate);
    resizeObserver.observe(stageRef.current);
    window.addEventListener("resize", scheduleUpdate);
    scheduleUpdate();
    return () => {
      window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      window.removeEventListener("resize", scheduleUpdate);
    };
  }, [activeSession?.id, api]);

  const refreshBrowserState = async () => {
    const [host, backendSessions] = await Promise.allSettled([
      api.getBrowserHostSnapshot(),
      api.listBrowserSessions()
    ]);

    if (host.status === "fulfilled") {
      onHostSnapshotChange(host.value);
      if (host.value.activeSessionId) onActiveSessionChange(host.value.activeSessionId);
    }

    if (backendSessions.status === "fulfilled" && backendSessions.value.ok && backendSessions.value.data) {
      onSessionsChange(backendSessions.value.data);
      if (!activeSessionId && backendSessions.value.data[0]) {
        onActiveSessionChange(backendSessions.value.data[0].id);
      }
      const sessionIdForEvents = activeSessionId ?? backendSessions.value.data[0]?.id;
      if (sessionIdForEvents) {
        const backendEvents = await api.listBrowserSessionEvents(sessionIdForEvents);
        if (backendEvents.ok && backendEvents.data) {
          onEventsChange((current) => mergeBrowserEvents(current, backendEvents.data ?? []));
        }
      }
    } else if (backendSessions.status === "fulfilled" && backendSessions.value.status !== 404) {
      onErrorChange(backendSessions.value.error?.message ?? null);
    }
  };

  const openSession = async () => {
    setIsWorking(true);
    onErrorChange(null);
    try {
      const result = await api.openBrowserHost({
        sessionId: activeSession?.id,
        taskId: activeSession?.task_id,
        url: urlDraft.trim(),
        mode: "watch"
      });
      applyHostResult(result, onHostSnapshotChange, onActiveSessionChange, onErrorChange);
    } catch (error) {
      onErrorChange(readableError(error, "Open browser session failed"));
    } finally {
      setIsWorking(false);
    }
  };

  const runSessionCommand = async (
    label: string,
    hostCommand: (sessionId: string) => Promise<{ ok: boolean; snapshot?: BrowserHostSnapshot; error?: string }>,
    backendCommand?: (sessionId: string) => Promise<{ ok: boolean; data?: BrowserSession; error?: { message: string } }>
  ) => {
    if (!activeSession) return;
    setIsWorking(true);
    onErrorChange(null);
    try {
      const result = await hostCommand(activeSession.id);
      if (result.snapshot) {
        onHostSnapshotChange(result.snapshot);
      }
      if (!result.ok) {
        onErrorChange(result.error ?? `${label} failed`);
      }
      if (backendCommand) {
        const backendResult = await backendCommand(activeSession.id);
        if (backendResult.ok && backendResult.data) {
          onSessionsChange(upsertSession(mergedSessions, backendResult.data));
        }
      }
    } catch (error) {
      onErrorChange(readableError(error, `${label} failed`));
    } finally {
      setIsWorking(false);
    }
  };

  const observeSession = async () => {
    if (!activeSession) return;
    setIsWorking(true);
    onErrorChange(null);
    try {
      if (activeSessionHasHost) {
        const hostResult = await api.performBrowserHostAction(activeSession.id, { kind: "observe" });
        applyHostResult(hostResult, onHostSnapshotChange, onActiveSessionChange, onErrorChange);
      }

      const backendResult = await api.observeBrowserSession(activeSession.id);
      if (backendResult.ok && backendResult.data) {
        const observedEvent = backendResult.data;
        onEventsChange((current) => mergeBrowserEvents(current, [observedEvent]));
      } else if (!activeSessionHasHost && !backendResult.ok) {
        onErrorChange(backendResult.error?.message ?? "Observe failed");
      }
    } catch (error) {
      onErrorChange(readableError(error, "Observe failed"));
    } finally {
      setIsWorking(false);
    }
  };

  const exportReplay = async () => {
    if (!activeSession) return;
    setIsWorking(true);
    onErrorChange(null);
    setExportResult(null);
    try {
      const result = await api.exportBrowserReplay(activeSession.id);
      if (result.ok && result.data) {
        setExportResult(formatReplayExport(result.data));
      } else if (result.status === 404) {
        setExportResult("Replay export endpoint is not available yet.");
      } else {
        onErrorChange(result.error?.message ?? "Replay export failed");
      }
    } catch (error) {
      onErrorChange(readableError(error, "Replay export failed"));
    } finally {
      setIsWorking(false);
    }
  };

  const showActiveSession = async (sessionId: string) => {
    onActiveSessionChange(sessionId);
    if (!hostSessions.some((session) => session.id === sessionId)) {
      const backendEvents = await api.listBrowserSessionEvents(sessionId);
      if (backendEvents.ok && backendEvents.data) {
        onEventsChange((current) => mergeBrowserEvents(current, backendEvents.data ?? []));
        onErrorChange(null);
      }
      return;
    }
    try {
      const result = await api.showBrowserHost(sessionId);
      applyHostResult(result, onHostSnapshotChange, onActiveSessionChange, onErrorChange);
    } catch (error) {
      onErrorChange(readableError(error, "Show browser session failed"));
    }
  };

  return (
    <Panel
      title="Watch Mode"
      eyebrow="Browser Activity"
      className="panel--browser-activity"
      action={
        <button className="icon-button" onClick={() => void refreshBrowserState()} title="Refresh" aria-label="Refresh" type="button">
          <RotateCcw size={15} aria-hidden="true" />
        </button>
      }
    >
      <div className="browser-watch">
        <aside className="browser-watch__sessions" aria-label="Browser sessions">
          <div className="browser-watch__urlbar">
            <Globe2 size={15} aria-hidden="true" />
            <input value={urlDraft} onChange={(event) => setUrlDraft(event.currentTarget.value)} placeholder="https://example.com" />
            <button className="icon-button" onClick={() => void openSession()} disabled={isWorking} title="Open URL" aria-label="Open URL" type="button">
              {isWorking ? <Loader2 size={15} aria-hidden="true" className="spin-icon" /> : <ArrowDownToLine size={15} aria-hidden="true" />}
            </button>
          </div>

          <div className="browser-session-list">
            {mergedSessions.length ? (
              mergedSessions.map((session) => (
                <button
                  className={session.id === activeSession?.id ? "browser-session browser-session--active" : "browser-session"}
                  key={session.id}
                  onClick={() => void showActiveSession(session.id)}
                  type="button"
                >
                  <span className="browser-session__title">{session.title || "Untitled page"}</span>
                  <span className="browser-session__url">{session.current_url || "No page loaded"}</span>
                  <span className="browser-session__meta">
                    <Badge tone={toneForSession(session)}>{labelForSession(session)}</Badge>
                    {session.takeover ? <Badge tone="warning">Takeover</Badge> : null}
                  </span>
                </button>
              ))
            ) : (
              <div className="browser-empty">
                <Globe2 size={20} aria-hidden="true" />
                <strong>No browser sessions</strong>
                <span>Open a URL or wait for the backend browser host to connect.</span>
              </div>
            )}
          </div>
        </aside>

        <section className="browser-watch__stage-column">
          <div className={sensitiveState ? "browser-alert browser-alert--sensitive" : "browser-alert"}>
            {sensitiveState ? <ShieldAlert size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
            <span>
              {sensitiveState
                ? "Sensitive or pending approval state is active."
                : activeSession
                  ? "Watching embedded browser activity."
                  : "No active browser to display."}
            </span>
          </div>

          <div className="browser-stage" ref={stageRef}>
            {activeSession ? (
              <>
                <div className="browser-stage__chrome">
                  <span>{activeSession.title || "Embedded browser"}</span>
                  <small>{activeSession.current_url}</small>
                </div>
                {activeSessionHasHost && !hostSnapshot?.visible ? (
                  <button className="button button--secondary" onClick={() => void api.showBrowserHost(activeSession.id)} type="button">
                    <Eye size={14} aria-hidden="true" />
                    Show embedded browser
                  </button>
                ) : null}
                {!activeSession.takeover ? <div className="browser-stage__shield">Watch-only</div> : null}
              </>
            ) : (
              <div className="browser-stage__empty">
                <Globe2 size={24} aria-hidden="true" />
                <span>Embedded browser preview appears here.</span>
              </div>
            )}
          </div>

          <div className="browser-controls" aria-label="Browser controls">
            <button
              className="button button--secondary"
              onClick={() => void runSessionCommand("Pause", api.pauseBrowserHost.bind(api), api.pauseBrowserSession.bind(api))}
              disabled={hostControlDisabled}
              type="button"
            >
              <Pause size={14} aria-hidden="true" />
              Pause
            </button>
            <button
              className="button button--secondary"
              onClick={() => void runSessionCommand("Resume", api.resumeBrowserHost.bind(api), api.resumeBrowserSession.bind(api))}
              disabled={hostControlDisabled}
              type="button"
            >
              <Play size={14} aria-hidden="true" />
              Resume
            </button>
            <button
              className="button button--primary"
              onClick={() => void runSessionCommand("Take over", api.takeoverBrowserHost.bind(api), api.takeoverBrowserSession.bind(api))}
              disabled={hostControlDisabled}
              type="button"
            >
              <Hand size={14} aria-hidden="true" />
              Take Over
            </button>
            <button
              className="button button--secondary"
              onClick={() => void runSessionCommand("Return control", api.releaseBrowserHost.bind(api), api.releaseBrowserSession.bind(api))}
              disabled={hostControlDisabled}
              type="button"
            >
              <Bot size={14} aria-hidden="true" />
              Return Control
            </button>
            <button className="button button--secondary" onClick={() => void observeSession()} disabled={!activeSession || isWorking} type="button">
              <Eye size={14} aria-hidden="true" />
              Observe
            </button>
            <button className="button button--secondary" onClick={() => void exportReplay()} disabled={!activeSession || isWorking} type="button">
              <Download size={14} aria-hidden="true" />
              Export Replay
            </button>
            <button
              className="button button--danger"
              onClick={() => void runSessionCommand("Stop", api.stopBrowserHost.bind(api))}
              disabled={hostControlDisabled}
              type="button"
            >
              <Square size={14} aria-hidden="true" />
              Stop
            </button>
            <button className="button button--ghost" onClick={() => void api.hideBrowserHost()} disabled={isWorking || !hostSnapshot?.visible} type="button">
              <X size={14} aria-hidden="true" />
              Hide
            </button>
          </div>
        </section>

        <aside className="browser-watch__events" aria-label="Browser events">
          <div className="browser-events__head">
            <strong>Event queue</strong>
            <Badge tone={activeEvents.some(isSensitiveEvent) ? "warning" : "neutral"}>{activeEvents.length}</Badge>
          </div>
          <ol className="browser-event-list">
            {activeEvents.length ? (
              activeEvents.map((event) => (
                <li className={isSensitiveEvent(event) ? "browser-event browser-event--sensitive" : "browser-event"} key={event.id}>
                  <span className="browser-event__icon">{iconForEvent(event)}</span>
                  <div>
                    <div className="row row--between">
                      <strong>{event.type}</strong>
                      <Badge tone={toneForEvent(event)}>{event.ok ? "ok" : "error"}</Badge>
                    </div>
                    <p>{event.action ? describeAction(event.action.kind, event.url) : event.url || event.title || event.error || "Session update"}</p>
                    <span className="muted">{new Date(event.created_at).toLocaleTimeString()}</span>
                  </div>
                </li>
              ))
            ) : (
              <li className="browser-empty browser-empty--events">
                <Clock size={18} aria-hidden="true" />
                <span>No browser events yet.</span>
              </li>
            )}
          </ol>
          {error ? (
            <div className="browser-error">
              <CircleAlert size={15} aria-hidden="true" />
              <span>{error}</span>
            </div>
          ) : null}
          {exportResult ? <p className="muted">{exportResult}</p> : null}
        </aside>
      </div>
    </Panel>
  );
}

function applyBrowserStreamEvent(
  message: BackendBrowserSessionStreamEvent,
  onSessionsChange: (sessions: BrowserSession[] | ((current: BrowserSession[]) => BrowserSession[])) => void,
  onEventsChange: (events: BrowserActivityEvent[] | ((current: BrowserActivityEvent[]) => BrowserActivityEvent[])) => void
) {
  if ("session" in message && message.session) {
    onSessionsChange((current) => upsertSession(current, mapStreamSession(message.session as BrowserStreamSessionPayload)));
  }
  if ("event" in message && message.event) {
    onEventsChange((current) => mergeBrowserEvents(current, [mapStreamEvent(message.event as BrowserStreamEventPayload)]));
  }
  if ("session_id" in message && "type" in message && !("event" in message) && !("session" in message)) {
    onEventsChange((current) => mergeBrowserEvents(current, [mapStreamEvent(message as BrowserStreamEventPayload)]));
  }
}

function applyHostResult(
  result: { ok: boolean; snapshot?: BrowserHostSnapshot; session?: BrowserSession; error?: string },
  onHostSnapshotChange: (snapshot: BrowserHostSnapshot | null) => void,
  onActiveSessionChange: (sessionId: string | null) => void,
  onErrorChange: (error: string | null) => void
) {
  if (result.snapshot) {
    onHostSnapshotChange(result.snapshot);
    if (result.snapshot.activeSessionId) {
      onActiveSessionChange(result.snapshot.activeSessionId);
    }
  }
  if (result.session) {
    onActiveSessionChange(result.session.id);
  }
  onErrorChange(result.ok ? null : result.error ?? "Browser host action failed");
}

function mergeBrowserSessions(primary: BrowserSession[], secondary: BrowserSession[]): BrowserSession[] {
  const byId = new Map<string, BrowserSession>();
  for (const session of primary) byId.set(session.id, session);
  for (const session of secondary) byId.set(session.id, { ...byId.get(session.id), ...session });
  return [...byId.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

function mergeBrowserEvents(primary: BrowserActivityEvent[], secondary: BrowserActivityEvent[]): BrowserActivityEvent[] {
  const byId = new Map<string, BrowserActivityEvent>();
  for (const event of primary) byId.set(event.id, event);
  for (const event of secondary) byId.set(event.id, event);
  return [...byId.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 300);
}

function upsertSession(sessions: BrowserSession[], next: BrowserSession): BrowserSession[] {
  return mergeBrowserSessions(sessions.filter((session) => session.id !== next.id), [next]);
}

type BrowserStreamSessionPayload = Partial<Omit<BrowserSession, "task_id">> & {
  task_id?: string | null;
  url?: string;
};

type BrowserStreamEventPayload = Partial<Omit<BrowserActivityEvent, "task_id" | "step_id" | "action">> & {
  task_id?: string | null;
  step_id?: string | null;
  action?: unknown;
};

function mapStreamSession(session: BrowserStreamSessionPayload): BrowserSession {
  return {
    id: String(session.id ?? ""),
    task_id: stringOrUndefined(session.task_id),
    current_url: String(session.current_url ?? session.url ?? ""),
    title: String(session.title ?? ""),
    status: String(session.status ?? "idle"),
    mode: String(session.mode ?? "watch"),
    created_at: String(session.created_at ?? new Date().toISOString()),
    updated_at: String(session.updated_at ?? new Date().toISOString()),
    paused: Boolean(session.paused),
    takeover: Boolean(session.takeover),
    last_observation: typeof session.last_observation === "string" || isPlainRecord(session.last_observation)
      ? session.last_observation
      : null
  };
}

function mapStreamEvent(event: BrowserStreamEventPayload): BrowserActivityEvent {
  return {
    id: String(event.id ?? crypto.randomUUID()),
    session_id: String(event.session_id ?? ""),
    task_id: stringOrUndefined(event.task_id),
    step_id: stringOrUndefined(event.step_id),
    type: String(event.type ?? "event"),
    action: isPlainRecord(event.action) && typeof event.action.kind === "string" ? event.action as BrowserActivityEvent["action"] : undefined,
    url: stringOrUndefined(event.url),
    title: stringOrUndefined(event.title),
    risk_level: stringOrUndefined(event.risk_level),
    verdict: stringOrUndefined(event.verdict),
    ok: event.ok !== false,
    error: stringOrUndefined(event.error),
    screenshot_url: stringOrUndefined(event.screenshot_url),
    created_at: String(event.created_at ?? new Date().toISOString())
  };
}

function stringOrUndefined(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function readableError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isSensitiveEvent(event: BrowserActivityEvent): boolean {
  const marker = `${event.type} ${event.verdict ?? ""} ${event.risk_level ?? ""}`.toLowerCase();
  return marker.includes("approval") || marker.includes("sensitive") || marker.includes("high") || marker.includes("critical");
}

function toneForSession(session: BrowserSession): "neutral" | "success" | "warning" | "danger" | "info" {
  if (session.status === "error") return "danger";
  if (session.paused || session.status === "awaiting_approval") return "warning";
  if (session.status === "loading" || session.status === "running") return "info";
  return session.takeover ? "warning" : "success";
}

function labelForSession(session: BrowserSession): string {
  if (session.takeover) return "manual";
  if (session.paused) return "paused";
  return session.status || "idle";
}

function toneForEvent(event: BrowserActivityEvent): "neutral" | "success" | "warning" | "danger" | "info" {
  if (!event.ok) return "danger";
  return isSensitiveEvent(event) ? "warning" : "info";
}

function iconForEvent(event: BrowserActivityEvent) {
  if (!event.ok) return <CircleAlert size={14} aria-hidden="true" />;
  if (isSensitiveEvent(event)) return <ShieldAlert size={14} aria-hidden="true" />;
  if (event.action?.kind === "screenshot" || event.action?.kind === "observe") return <Eye size={14} aria-hidden="true" />;
  return <Power size={14} aria-hidden="true" />;
}

function describeAction(kind: string, url?: string): string {
  return url ? `${kind} ${url}` : kind;
}

function formatReplayExport(result: BrowserReplayExport): string {
  if (result.url) return `Replay exported: ${result.url}`;
  if (result.path) return `Replay exported: ${result.path}`;
  if (result.events) return `Replay export contains ${result.events.length} events.`;
  return "Replay export is ready.";
}
