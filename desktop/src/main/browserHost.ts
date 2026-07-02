import {
  BrowserView,
  BrowserWindow,
  WebContentsView,
  ipcMain,
  type IpcMainInvokeEvent,
  type Rectangle,
  type WebContents
} from "electron";
import { randomUUID } from "node:crypto";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  BrowserAction,
  BrowserActivityEvent,
  BrowserHostActionRequest,
  BrowserHostActionResult,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  BrowserSession
} from "../shared/types";
import {
  sanitizeActionResultForRenderer,
  sanitizeEventForRenderer,
  sanitizeSessionForRenderer,
  sanitizeSnapshotForRenderer
} from "../shared/browserHostRedaction";
import {
  assertBrowserHostUrlAllowed,
  isBlockedBrowserHostNavigation,
  isBrowserHostRequestAllowed
} from "./browserHostNetworkGuard";
import { isReadOnlyBrowserHostAction } from "./browserHostBridge";
import { domClickScript, domFillScript, domScrollScript, domSubmitScript, observeScript } from "./browserHostDomActions";
import { confirmNativeDesktopAction, isTrustedRendererUrl } from "./ipc";

export { BrowserHostWebSocketBridge, buildBrowserHostWebSocketUrl, isLoopbackBackendUrl } from "./browserHostBridge";

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

type BrowserHostIpcRegistrar = {
  handle: (channel: string, listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown) => void;
};

type BrowserHostIpcTarget = Pick<
  BrowserHost,
  | "getSnapshot"
  | "open"
  | "show"
  | "hide"
  | "setBounds"
  | "pause"
  | "resume"
  | "takeover"
  | "release"
  | "stop"
  | "performAction"
>;

const DEFAULT_HOME_URL = "about:blank";
const MAX_EVENTS_PER_SESSION = 300;
const MIN_BROWSER_SIZE = 80;

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
    const sessionId = normalizeId(request.sessionId) ?? randomUUID();

    try {
      const current = this.sessions.get(sessionId);
      const targetUrl = normalizeUrl(request.url) ?? current?.session.current_url ?? DEFAULT_HOME_URL;
      const entry = current ?? this.createHostedSession(sessionId, request);
      this.sessions.set(sessionId, entry);
      this.activeSessionId = sessionId;
      entry.session.status = "loading";
      entry.session.mode = request.mode ?? entry.session.mode;
      entry.session.updated_at = timestamp();
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
    } catch (error) {
      const entry = this.sessions.get(sessionId);
      const message = errorMessage(error);
      if (entry) {
        entry.session.status = "error";
        entry.session.updated_at = timestamp();
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
    this.bounds = normalizeBounds(bounds);
    this.applyBounds();
    return { ok: true, snapshot: this.getSnapshot() };
  }

  pause(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.paused = true;
    entry.session.status = "paused";
    entry.session.updated_at = timestamp();
    const event = this.addEvent(entry, { type: "session.paused", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  resume(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.paused = false;
    entry.session.status = entry.container.view.webContents.isLoading() ? "loading" : "idle";
    entry.session.updated_at = timestamp();
    const event = this.addEvent(entry, { type: "session.resumed", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  takeover(sessionId: string): BrowserHostActionResult {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.takeover = true;
    entry.session.mode = "takeover";
    entry.session.updated_at = timestamp();
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
    entry.session.updated_at = timestamp();
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
      session: sanitizeSessionForRenderer({ ...entry.session, status: "stopped", updated_at: timestamp() }),
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
    } catch (error) {
      const event = this.addEvent(entry, {
        type: "action.failed",
        action,
        ok: false,
        error: errorMessage(error)
      });
      entry.session.status = "error";
      entry.session.updated_at = timestamp();
      this.emitSnapshot();
      return this.fail(errorMessage(error), entry, event);
    }
  }

  private createHostedSession(sessionId: string, request: BrowserHostOpenRequest): HostedBrowserSession {
    const partition = `lengrvis-watch-${sessionId}-${Date.now()}`;
    const container = createBrowserContainer(partition);
    const webContents = container.view.webContents;
    const now = timestamp();
    const entry: HostedBrowserSession = {
      container,
      session: {
        id: sessionId,
        task_id: request.taskId,
        current_url: normalizeUrl(request.url) ?? DEFAULT_HOME_URL,
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
      entry.session.updated_at = timestamp();
      this.emitSnapshot();
    });

    webContents.on("did-fail-load", (_event, errorCode, errorDescription, validatedUrl, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) return;
      entry.session.status = "error";
      entry.session.current_url = validatedUrl || entry.session.current_url;
      entry.session.updated_at = timestamp();
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
      entry.session.updated_at = timestamp();
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
    const startedAt = timestamp();

    switch (action.kind) {
      case "open":
      case "navigate": {
        const url = requireActionUrl(action);
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
        await runDomAction(webContents, domClickScript(requireSelector(action)));
        return this.addEvent(entry, { type: "action.click", action, ok: true });
      }
      case "fill": {
        if (action.fields && Object.keys(action.fields).length) {
          for (const [selector, text] of Object.entries(action.fields)) {
            await runDomAction(webContents, domFillScript(selector, text));
          }
        } else {
          await runDomAction(webContents, domFillScript(requireSelector(action), action.text ?? ""));
        }
        return this.addEvent(entry, { type: "action.fill", action, ok: true });
      }
      case "submit": {
        await runDomAction(webContents, domSubmitScript(requireSelector(action)));
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
    entry.session.updated_at = timestamp();
  }

  private addEvent(
    entry: HostedBrowserSession,
    event: Omit<BrowserActivityEvent, "id" | "session_id" | "task_id" | "created_at">
  ): BrowserActivityEvent {
    const activity: BrowserActivityEvent = {
      id: randomUUID(),
      session_id: entry.session.id,
      task_id: entry.session.task_id,
      created_at: timestamp(),
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

export function registerBrowserHostIpcHandlers({
  handle,
  host
}: BrowserHostIpcRegistrar & { host: BrowserHostIpcTarget }): void {
  handle(IPC_CHANNELS.browserHostSnapshot, (event) => {
    assertBrowserHostRenderer(event);
    return sanitizeSnapshotForRenderer(host.getSnapshot());
  });
  handle(IPC_CHANNELS.browserHostOpen, async (event, request) => {
    assertBrowserHostRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm browser session",
      message: "Open a managed browser session?",
      detail: "The session may navigate to external websites using the app's configured browser-network permissions."
    });
    return sanitizeActionResultForRenderer(await host.open(request as BrowserHostOpenRequest));
  });
  handle(IPC_CHANNELS.browserHostShow, (event, sessionId) => {
    assertBrowserHostRenderer(event);
    return sanitizeActionResultForRenderer(host.show(String(sessionId)));
  });
  handle(IPC_CHANNELS.browserHostHide, (event) => {
    assertBrowserHostRenderer(event);
    return sanitizeActionResultForRenderer(host.hide());
  });
  handle(IPC_CHANNELS.browserHostSetBounds, (event, bounds) => {
    assertBrowserHostRenderer(event);
    return sanitizeActionResultForRenderer(host.setBounds(bounds as BrowserHostBounds));
  });
  handle(IPC_CHANNELS.browserHostPause, (event, sessionId) => {
    assertBrowserHostRenderer(event);
    return sanitizeActionResultForRenderer(host.pause(String(sessionId)));
  });
  handle(IPC_CHANNELS.browserHostResume, (event, sessionId) => {
    assertBrowserHostRenderer(event);
    return sanitizeActionResultForRenderer(host.resume(String(sessionId)));
  });
  handle(IPC_CHANNELS.browserHostTakeover, (event, sessionId) => {
    assertBrowserHostRenderer(event);
    void sessionId;
    return deniedRendererBrowserHostWrite(host, "BrowserHost takeover requires an approval grant.");
  });
  handle(IPC_CHANNELS.browserHostRelease, (event, sessionId) => {
    assertBrowserHostRenderer(event);
    return sanitizeActionResultForRenderer(host.release(String(sessionId)));
  });
  handle(IPC_CHANNELS.browserHostStop, (event, sessionId) => {
    assertBrowserHostRenderer(event);
    return Promise.resolve(host.stop(String(sessionId))).then(sanitizeActionResultForRenderer);
  });
  handle(IPC_CHANNELS.browserHostAction, async (event, request) => {
    assertBrowserHostRenderer(event);
    const actionRequest = request as Partial<BrowserHostActionRequest> | undefined;
    const action = actionRequest?.action;
    if (!isReadOnlyBrowserHostAction(action)) {
      return deniedRendererBrowserHostWrite(host, "BrowserHost input actions require an approval grant.");
    }
    return sanitizeActionResultForRenderer(await host.performAction(String(actionRequest?.sessionId ?? ""), action));
  });
}

function deniedRendererBrowserHostWrite(host: Pick<BrowserHostIpcTarget, "getSnapshot">, error: string): BrowserHostActionResult {
  return sanitizeActionResultForRenderer({
    ok: false,
    error,
    snapshot: host.getSnapshot()
  });
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

export function hardenEmbeddedWebContents(webContents: WebContents): void {
  webContents.setWindowOpenHandler(() => {
    return { action: "deny" };
  });
  webContents.on("will-navigate", (event, url) => {
    if (isBlockedBrowserHostNavigation(url)) {
      event.preventDefault();
    }
  });
  webContents.session.webRequest.onBeforeRequest((details, callback) => {
    void handleBrowserHostBeforeRequest(details, callback);
  });
  webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  webContents.session.setPermissionCheckHandler(() => false);
  webContents.setAudioMuted(true);
}

async function handleBrowserHostBeforeRequest(
  details: { url: string },
  callback: (response: { cancel?: boolean }) => void
): Promise<void> {
  try {
    callback({ cancel: !(await isBrowserHostRequestAllowed(details.url)) });
  } catch {
    callback({ cancel: true });
  }
}

function destroyWebContents(webContents: WebContents): void {
  if (!webContents.isDestroyed()) {
    webContents.close({ waitForBeforeUnload: false });
  }
}

function normalizeId(value?: string): string | undefined {
  const trimmed = value?.trim();
  return trimmed || undefined;
}

function normalizeUrl(value?: string): string | undefined {
  const trimmed = value?.trim();
  if (!trimmed) return undefined;
  if (trimmed === "about:blank") return trimmed;
  const withProtocol = /^[a-z][a-z0-9+.-]*:/i.test(trimmed) ? trimmed : `https://${trimmed}`;
  const parsed = new URL(withProtocol);
  if (!["https:", "http:"].includes(parsed.protocol)) {
    throw new Error("Only http and https URLs can be opened in Watch Mode");
  }
  assertBrowserHostUrlAllowed(parsed);
  return parsed.toString();
}

function normalizeBounds(bounds: BrowserHostBounds): BrowserHostBounds {
  return {
    x: Math.max(0, Math.round(bounds.x)),
    y: Math.max(0, Math.round(bounds.y)),
    width: Math.max(MIN_BROWSER_SIZE, Math.round(bounds.width)),
    height: Math.max(MIN_BROWSER_SIZE, Math.round(bounds.height))
  };
}

function timestamp(): string {
  return new Date().toISOString();
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Browser host action failed";
}

function requireActionUrl(action: BrowserAction): string {
  const url = normalizeUrl(action.url);
  if (!url) throw new Error("Browser action requires a URL");
  return url;
}

function requireSelector(action: BrowserAction): string {
  const selector = action.selector?.trim();
  if (!selector) throw new Error("Browser action requires a selector");
  return selector;
}

function runDomAction(webContents: WebContents, script: string): Promise<unknown> {
  return webContents.executeJavaScript(script, true);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, Math.max(0, Math.min(ms, 30_000)));
  });
}

function assertBrowserHostRenderer(event: IpcMainInvokeEvent): void {
  const url = event.senderFrame?.url ?? "";
  if (!BrowserWindow.fromWebContents(event.sender) || !isTrustedRendererUrl(url)) {
    throw new Error("Browser host request came from an unknown renderer");
  }
}
