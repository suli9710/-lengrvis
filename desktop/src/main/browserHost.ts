import {
  BrowserView,
  BrowserWindow,
  WebContentsView,
  ipcMain,
  type Rectangle,
  type WebContents
} from "electron";
import { randomUUID } from "node:crypto";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  BrowserAction,
  BrowserActivityEvent,
  BrowserHostActionResult,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  BrowserSession
} from "../shared/browserTypes";
import {
  sanitizeEventForRenderer,
  sanitizeSessionForRenderer
} from "../shared/browserHostRedaction";
import { domClickScript, domFillScript, domScrollScript, domSubmitScript, observeScript } from "./browserHostDomActions";
import { registerBrowserHostIpcHandlers } from "./browserHostIpcHandlers";
import {
  browserHostErrorMessage,
  browserHostTimestamp,
  normalizeBrowserHostBounds,
  normalizeBrowserHostId,
  normalizeBrowserHostUrl,
  requireBrowserActionSelector,
  requireBrowserActionUrl
} from "./browserHostValidation";
import { hardenEmbeddedWebContents } from "./browserHostWebContentsHardening";

export { BrowserHostWebSocketBridge, buildBrowserHostWebSocketUrl, isLoopbackBackendUrl } from "./browserHostBridge";
export { registerBrowserHostIpcHandlers } from "./browserHostIpcHandlers";
export { hardenEmbeddedWebContents } from "./browserHostWebContentsHardening";

type BrowserContainer =
  | {
      kind: "webContentsView";
      view: WebContentsView;
    }
  | {
      kind: "browserView";
      view: BrowserView;
    };

interface HostedBrowserSession {
  container: BrowserContainer;
  session: BrowserSession;
  events: BrowserActivityEvent[];
  cssKey?: string;
}

const DEFAULT_HOME_URL = "about:blank";
const MAX_EVENTS_PER_SESSION = 300;

export class BrowserHost {
  private sessions = new Map<string, HostedBrowserSession>();
  private activeSessionId: string | null = null;
  private bounds: BrowserHostBounds | null = null;
  private visible = false;
  private snapshotListeners = new Set<(snapshot: BrowserHostSnapshot) => void>();

  constructor(private readonly getMainWindow: () => BrowserWindow | null) {}

  registerIpcHandlers(): void {
    registerBrowserHostIpcHandlers({
      handle: (channel, listener) => ipcMain.handle(channel, listener),
      host: this
    });
  }

  destroy(): void {
    this.snapshotListeners.clear();
    for (const sessionId of [...this.sessions.keys()]) {
      void this.stop(sessionId);
    }
  }

  onSnapshot(listener: (snapshot: BrowserHostSnapshot) => void): () => void {
    this.snapshotListeners.add(listener);
    return () => {
      this.snapshotListeners.delete(listener);
    };
  }

  getSnapshot(): BrowserHostSnapshot {
    return {
      sessions: [...this.sessions.values()].map((entry) => sanitizeSessionForRenderer(entry.session)),
      events: [...this.sessions.values()]
        .flatMap((entry) => entry.events)
        .map(sanitizeEventForRenderer)
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
      activeSessionId: this.activeSessionId,
      visible: this.visible,
      hostAvailable: this.hasAttachableWindow()
    };
  }

  async open(request: BrowserHostOpenRequest = {}): Promise<BrowserHostActionResult> {
    const sessionId = normalizeBrowserHostId(request.sessionId) ?? randomUUID();

    try {
      const current = this.sessions.get(sessionId);
      const targetUrl = normalizeBrowserHostUrl(request.url) ?? current?.session.current_url ?? DEFAULT_HOME_URL;
      const entry = current ?? this.createHostedSession(sessionId, request);
      this.sessions.set(sessionId, entry);
      this.activeSessionId = sessionId;
      entry.session.status = "loading";
      entry.session.mode = request.mode ?? entry.session.mode;
      entry.session.updated_at = browserHostTimestamp();
      this.attachActiveView();
      this.applyBounds();
      this.setViewVisible(true);

      if (targetUrl && targetUrl !== "about:blank") {
        await entry.container.view.webContents.loadURL(targetUrl);
      } else if (!entry.container.view.webContents.getURL()) {
        await entry.container.view.webContents.loadURL(DEFAULT_HOME_URL);
      }

      const event = this.addEvent(entry, {
        type: "session.opened",
        action: { kind: "open", url: targetUrl },
        ok: true,
        url: entry.session.current_url,
        title: entry.session.title
      });
      this.updateSessionFromWebContents(entry, "idle");
      this.emitSnapshot();
      return this.ok(entry, event);
    } catch (error) { // broad-exception-boundary
      const entry = this.sessions.get(sessionId);
      const message = browserHostErrorMessage(error);
      if (entry) {
        entry.session.status = "error";
        entry.session.updated_at = browserHostTimestamp();
        const event = this.addEvent(entry, {
          type: "session.open_failed",
          action: { kind: "open", url: request.url },
          ok: false,
          error: message
        });
        this.emitSnapshot();
        return this.fail(message, entry, event);
      }
      return this.fail(message);
    }
  }

  show(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    this.activeSessionId = sessionId;
    this.attachActiveView();
    this.applyBounds();
    this.setViewVisible(true);
    const event = this.addEvent(entry, { type: "session.shown", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  hide(): BrowserHostActionResult {
    this.detachActiveView();
    this.visible = false;
    this.emitSnapshot();
    return { ok: true, snapshot: this.getSnapshot() };
  }

  setBounds(bounds: BrowserHostBounds): BrowserHostActionResult {
    this.bounds = normalizeBrowserHostBounds(bounds);
    this.applyBounds();
    return { ok: true, snapshot: this.getSnapshot() };
  }

  pause(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.paused = true;
    entry.session.status = "paused";
    entry.session.updated_at = browserHostTimestamp();
    const event = this.addEvent(entry, { type: "session.paused", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  resume(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.paused = false;
    entry.session.status = entry.container.view.webContents.isLoading() ? "loading" : "idle";
    entry.session.updated_at = browserHostTimestamp();
    const event = this.addEvent(entry, { type: "session.resumed", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  takeover(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.takeover = true;
    entry.session.mode = "takeover";
    entry.session.updated_at = browserHostTimestamp();
    this.setInteractionBlocked(entry, false);
    this.show(sessionId);
    const event = this.addEvent(entry, { type: "session.takeover", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  release(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.takeover = false;
    entry.session.mode = "watch";
    entry.session.updated_at = browserHostTimestamp();
    this.setInteractionBlocked(entry, true);
    const event = this.addEvent(entry, { type: "session.release", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  async stop(sessionId: string): Promise<BrowserHostActionResult> {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    const event = this.addEvent(entry, { type: "session.stopped", ok: true });
    this.detachView(entry);
    this.sessions.delete(sessionId);
    if (this.activeSessionId === sessionId) {
      this.activeSessionId = this.sessions.keys().next().value ?? null;
      this.attachActiveView();
    }
    destroyWebContents(entry.container.view.webContents);
    this.emitSnapshot();
    return {
      ok: true,
      session: sanitizeSessionForRenderer({ ...entry.session, status: "stopped", updated_at: browserHostTimestamp() }),
      event: sanitizeEventForRenderer(event),
      snapshot: this.getSnapshot()
    };
  }

  async performAction(sessionId: string, action: BrowserAction): Promise<BrowserHostActionResult> {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    if (entry.session.paused && action.kind !== "observe" && action.kind !== "screenshot") {
      const event = this.addEvent(entry, {
        type: "action.skipped",
        action,
        ok: false,
        error: "Browser session is paused"
      });
      this.emitSnapshot();
      return this.fail("Browser session is paused", entry, event);
    }

    try {
      const event = await this.executeAction(entry, action);
      this.updateSessionFromWebContents(entry);
      this.emitSnapshot();
      return this.ok(entry, event);
    } catch (error) { // broad-exception-boundary
      const event = this.addEvent(entry, {
        type: "action.failed",
        action,
        ok: false,
        error: browserHostErrorMessage(error)
      });
      entry.session.status = "error";
      entry.session.updated_at = browserHostTimestamp();
      this.emitSnapshot();
      return this.fail(browserHostErrorMessage(error), entry, event);
    }
  }

  private createHostedSession(sessionId: string, request: BrowserHostOpenRequest): HostedBrowserSession {
    const partition = `lengrvis-watch-${sessionId}-${Date.now()}`;
    const container = createBrowserContainer(partition);
    const webContents = container.view.webContents;
    const now = browserHostTimestamp();
    const entry: HostedBrowserSession = {
      container,
      session: {
        id: sessionId,
        task_id: request.taskId,
        current_url: normalizeBrowserHostUrl(request.url) ?? DEFAULT_HOME_URL,
        title: request.title ?? "Browser Watch",
        status: "idle",
        mode: request.mode ?? "watch",
        created_at: now,
        updated_at: now,
        paused: false,
        takeover: false,
        last_observation: null
      },
      events: []
    };

    hardenEmbeddedWebContents(webContents);
    this.bindWebContentsEvents(entry);
    this.setInteractionBlocked(entry, true);
    return entry;
  }

  private bindWebContentsEvents(entry: HostedBrowserSession): void {
    const { webContents } = entry.container.view;

    webContents.on("did-start-loading", () => {
      this.updateSessionFromWebContents(entry, "loading");
      this.addEvent(entry, { type: "page.loading", ok: true });
      this.emitSnapshot();
    });

    webContents.on("did-stop-loading", () => {
      this.updateSessionFromWebContents(entry, entry.session.paused ? "paused" : "idle");
      if (!entry.session.takeover) {
        void this.setInteractionBlocked(entry, true);
      }
      this.addEvent(entry, {
        type: "page.loaded",
        ok: true,
        url: entry.session.current_url,
        title: entry.session.title
      });
      this.emitSnapshot();
    });

    webContents.on("did-navigate", (_event, url) => {
      entry.session.current_url = url;
      this.updateSessionFromWebContents(entry);
      this.addEvent(entry, { type: "page.navigated", ok: true, url, title: entry.session.title });
      this.emitSnapshot();
    });

    webContents.on("did-navigate-in-page", (_event, url) => {
      entry.session.current_url = url;
      this.updateSessionFromWebContents(entry);
      this.addEvent(entry, { type: "page.navigated_in_page", ok: true, url, title: entry.session.title });
      this.emitSnapshot();
    });

    webContents.on("page-title-updated", (_event, title) => {
      entry.session.title = title;
      entry.session.updated_at = browserHostTimestamp();
      this.emitSnapshot();
    });

    webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedUrl, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) return;
      entry.session.status = "error";
      entry.session.current_url = validatedUrl || entry.session.current_url;
      entry.session.updated_at = browserHostTimestamp();
      this.addEvent(entry, {
        type: "page.load_failed",
        ok: false,
        url: validatedUrl,
        title: entry.session.title,
        error: errorDescription
      });
      this.emitSnapshot();
    });

    webContents.on("render-process-gone", (_event, details) => {
      entry.session.status = "error";
      entry.session.updated_at = browserHostTimestamp();
      this.addEvent(entry, {
        type: "page.crashed",
        ok: false,
        error: details.reason
      });
      this.emitSnapshot();
    });
  }

  private async executeAction(entry: HostedBrowserSession, action: BrowserAction): Promise<BrowserActivityEvent> {
    const webContents = entry.container.view.webContents;
    const startedAt = browserHostTimestamp();

    switch (action.kind) {
      case "open":
      case "navigate": {
        const url = requireBrowserActionUrl(action);
        entry.session.status = "loading";
        entry.session.updated_at = startedAt;
        await webContents.loadURL(url);
        return this.addEvent(entry, {
          type: `action.${action.kind}`,
          action,
          ok: true,
          url: webContents.getURL(),
          title: webContents.getTitle()
        });
      }
      case "click": {
        await runDomAction(webContents, domClickScript(requireBrowserActionSelector(action)));
        return this.addEvent(entry, { type: "action.click", action, ok: true });
      }
      case "fill": {
        if (action.fields && Object.keys(action.fields).length) {
          for (const [selector, text] of Object.entries(action.fields)) {
            await runDomAction(webContents, domFillScript(selector, text));
          }
        } else {
          await runDomAction(webContents, domFillScript(requireBrowserActionSelector(action), action.text ?? ""));
        }
        return this.addEvent(entry, { type: "action.fill", action, ok: true });
      }
      case "submit": {
        await runDomAction(webContents, domSubmitScript(requireBrowserActionSelector(action)));
        return this.addEvent(entry, { type: "action.submit", action, ok: true });
      }
      case "scroll": {
        await runDomAction(webContents, domScrollScript(Number(action.fields?.y ?? action.fields?.deltaY ?? 700)));
        return this.addEvent(entry, { type: "action.scroll", action, ok: true });
      }
      case "wait": {
        await delay(Number(action.fields?.ms ?? 1000));
        return this.addEvent(entry, { type: "action.wait", action, ok: true });
      }
      case "screenshot": {
        const image = await webContents.capturePage();
        const screenshot_url = image.toDataURL();
        return this.addEvent(entry, { type: "action.screenshot", action, ok: true, screenshot_url });
      }
      case "observe":
      case "cua": {
        const observation = await webContents.executeJavaScript(observeScript(), true) as Record<string, unknown>;
        entry.session.last_observation = observation;
        return this.addEvent(entry, {
          type: `action.${action.kind}`,
          action,
          ok: true,
          url: String(observation.url ?? webContents.getURL()),
          title: String(observation.title ?? webContents.getTitle())
        });
      }
      default:
        throw new Error(`Unsupported browser action: ${String(action.kind)}`);
    }
  }

  private updateSessionFromWebContents(entry: HostedBrowserSession, status?: BrowserSession["status"]): void {
    const webContents = entry.container.view.webContents;
    entry.session.current_url = webContents.getURL() || entry.session.current_url;
    entry.session.title = webContents.getTitle() || entry.session.title;
    entry.session.status = status ?? (webContents.isLoading() ? "loading" : entry.session.paused ? "paused" : "idle");
    entry.session.updated_at = browserHostTimestamp();
  }

  private addEvent(
    entry: HostedBrowserSession,
    event: Omit<BrowserActivityEvent, "id" | "session_id" | "task_id" | "created_at">
  ): BrowserActivityEvent {
    const activity: BrowserActivityEvent = {
      id: randomUUID(),
      session_id: entry.session.id,
      task_id: entry.session.task_id,
      created_at: browserHostTimestamp(),
      ...event
    };
    entry.events.unshift(activity);
    if (entry.events.length > MAX_EVENTS_PER_SESSION) {
      entry.events.length = MAX_EVENTS_PER_SESSION;
    }
    return activity;
  }

  private async setInteractionBlocked(entry: HostedBrowserSession, blocked: boolean): Promise<void> {
    if (entry.cssKey) {
      await entry.container.view.webContents.removeInsertedCSS(entry.cssKey).catch(() => undefined);
      entry.cssKey = undefined;
    }
    if (!blocked) return;
    entry.cssKey = await entry.container.view.webContents.insertCSS(`
      html::after {
        content: "";
        position: fixed;
        inset: 0;
        z-index: 2147483647;
        pointer-events: auto;
        background: transparent;
      }
    `);
  }

  private ok(entry: HostedBrowserSession, event?: BrowserActivityEvent): BrowserHostActionResult {
    return {
      ok: true,
      session: sanitizeSessionForRenderer(entry.session),
      event: event ? sanitizeEventForRenderer(event) : undefined,
      snapshot: this.getSnapshot()
    };
  }

  private fail(message: string, entry?: HostedBrowserSession, event?: BrowserActivityEvent): BrowserHostActionResult {
    return {
      ok: false,
      session: entry ? sanitizeSessionForRenderer(entry.session) : undefined,
      event: event ? sanitizeEventForRenderer(event) : undefined,
      snapshot: this.getSnapshot(),
      error: message
    };
  }

  private attachActiveView(): void {
    const entry = this.activeSessionId ? this.sessions.get(this.activeSessionId) : null;
    const window = this.getMainWindow();
    if (!entry || !window || window.isDestroyed()) return;

    for (const candidate of this.sessions.values()) {
      if (candidate !== entry) {
        this.detachView(candidate);
      }
    }

    if (entry.container.kind === "webContentsView") {
      window.contentView.addChildView(entry.container.view);
      entry.container.view.setVisible(this.visible);
    } else {
      window.addBrowserView(entry.container.view);
      entry.container.view.setAutoResize({ width: false, height: false });
    }
  }

  private detachActiveView(): void {
    const entry = this.activeSessionId ? this.sessions.get(this.activeSessionId) : null;
    if (entry) {
      this.detachView(entry);
    }
  }

  private detachView(entry: HostedBrowserSession): void {
    const window = this.getMainWindow();
    if (!window || window.isDestroyed()) return;
    if (entry.container.kind === "webContentsView") {
      window.contentView.removeChildView(entry.container.view);
      return;
    }
    window.removeBrowserView(entry.container.view);
  }

  private applyBounds(): void {
    const entry = this.activeSessionId ? this.sessions.get(this.activeSessionId) : null;
    if (!entry || !this.bounds) return;
    const bounds: Rectangle = this.bounds;
    entry.container.view.setBounds(bounds);
  }

  private setViewVisible(visible: boolean): void {
    this.visible = visible;
    const entry = this.activeSessionId ? this.sessions.get(this.activeSessionId) : null;
    if (!entry) return;
    if (entry.container.kind === "webContentsView") {
      entry.container.view.setVisible(visible);
    } else if (!visible) {
      this.detachView(entry);
    }
  }

  private hasAttachableWindow(): boolean {
    const window = this.getMainWindow();
    return Boolean(window && !window.isDestroyed());
  }

  private emitSnapshot(): void {
    const snapshot = this.getSnapshot();
    for (const listener of this.snapshotListeners) {
      listener(snapshot);
    }
    const window = this.getMainWindow();
    if (!window || window.isDestroyed()) return;
    window.webContents.send(IPC_CHANNELS.browserHostSnapshotChanged, snapshot);
  }
}

function createBrowserContainer(partition: string): BrowserContainer {
  const webPreferences = {
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    partition
  };

  if (typeof WebContentsView === "function") {
    return {
      kind: "webContentsView",
      view: new WebContentsView({ webPreferences })
    };
  }

  return {
    kind: "browserView",
    view: new BrowserView({ webPreferences })
  };
}

function destroyWebContents(webContents: WebContents): void {
  if (!webContents.isDestroyed()) {
    webContents.close({ waitForBeforeUnload: false });
  }
}

function runDomAction(webContents: WebContents, script: string): Promise<unknown> {
  return webContents.executeJavaScript(script, true);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, Math.max(0, Math.min(ms, 30_000)));
  });
}
