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
import { buildBackendWebSocketUrl, createDesktopWebSocket } from "./desktopWebSocket";
import { confirmNativeDesktopAction, isTrustedRendererUrl } from "./ipc";

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
const SENSITIVE_QUERY_KEY_NAMES = [
  "access_token",
  "api_key",
  "apikey",
  "auth",
  "auth_token",
  "authorization",
  "client_secret",
  "code",
  "cookie",
  "id_token",
  "jwt",
  "key",
  "oauth_token",
  "password",
  "refresh_token",
  "secret",
  "session",
  "session_id",
  "token"
] as const;
const SENSITIVE_QUERY_KEYS = new Set<string>(SENSITIVE_QUERY_KEY_NAMES);
const SENSITIVE_URL_PARAM_REGEX = new RegExp(`([?&#](?:${SENSITIVE_QUERY_KEY_NAMES.join("|")})=)[^&#\\s"'<>]+`, "gi");
const URL_IN_TEXT_REGEX = /\b(?:https?:\/\/|file:\/\/|app:\/\/)[^\s"'<>]+/gi;

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

function isReadOnlyBrowserHostAction(action: BrowserAction | undefined): action is BrowserAction {
  return action?.kind === "observe" || action?.kind === "screenshot";
}

function deniedRendererBrowserHostWrite(host: Pick<BrowserHostIpcTarget, "getSnapshot">, error: string): BrowserHostActionResult {
  return sanitizeActionResultForRenderer({
    ok: false,
    error,
    snapshot: host.getSnapshot()
  });
}

export class BrowserHostWebSocketBridge {
  private socket: WebSocket | null = null;
  private retryTimer: NodeJS.Timeout | null = null;
  private stopped = true;
  private unsubscribeSnapshots: (() => void) | null = null;

  constructor(
    private readonly browserHost: BrowserHost,
    private readonly getBaseUrl: () => string,
    private readonly getDesktopApiToken: () => string
  ) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.unsubscribeSnapshots = this.browserHost.onSnapshot((snapshot) => {
      this.send({ type: "snapshot", snapshot });
    });
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.unsubscribeSnapshots?.();
    this.unsubscribeSnapshots = null;
    this.socket?.close();
    this.socket = null;
  }

  private connect(): void {
    if (this.stopped || typeof WebSocket === "undefined") return;
    try {
      const desktopApiToken = this.getDesktopApiToken();
      this.socket = createDesktopWebSocket(buildBrowserHostWebSocketUrl(this.getBaseUrl()), desktopApiToken);
      this.socket.addEventListener("open", () => {
        this.send({ type: "snapshot", snapshot: this.browserHost.getSnapshot() });
      });
      this.socket.addEventListener("message", (event) => {
        void this.handleMessage(event.data);
      });
      this.socket.addEventListener("close", () => {
        this.socket = null;
        this.scheduleReconnect();
      });
      this.socket.addEventListener("error", () => {
        this.socket?.close();
      });
    } catch {
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.retryTimer) return;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, 2500);
  }

  private async handleMessage(rawData: unknown): Promise<void> {
    const message = parseBridgeMessage(rawData);
    if (!message) return;

    let result: BrowserHostActionResult;
    switch (message.type) {
      case "open":
        result = await this.browserHost.open({
          sessionId: message.session_id,
          taskId: message.task_id,
          url: message.url,
          title: message.title,
          mode: message.mode
        });
        break;
      case "show":
        result = this.browserHost.show(message.session_id);
        break;
      case "hide":
        result = this.browserHost.hide();
        break;
      case "set_bounds":
        result = this.browserHost.setBounds(message.bounds);
        break;
      case "pause":
        result = this.browserHost.pause(message.session_id);
        break;
      case "resume":
        result = this.browserHost.resume(message.session_id);
        break;
      case "takeover":
        result = this.deniedRemoteWriteAction("BrowserHost remote takeover requires a desktop approval grant.");
        break;
      case "release":
        result = this.browserHost.release(message.session_id);
        break;
      case "stop":
        result = await this.browserHost.stop(message.session_id);
        break;
      case "action":
        result = isReadOnlyBrowserHostAction(message.action)
          ? await this.browserHost.performAction(message.session_id, message.action)
          : this.deniedRemoteWriteAction("BrowserHost remote actions require a desktop approval grant.");
        break;
      default:
        result = { ok: true, snapshot: this.browserHost.getSnapshot() };
        break;
    }
    result = sanitizeActionResultForRenderer(result);

    this.send({
      type: "result",
      request_id: message.request_id,
      ok: result.ok,
      session: result.session,
      event: result.event,
      error: result.error
    });
  }

  private send(payload: unknown): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(payload));
  }

  private deniedRemoteWriteAction(error: string): BrowserHostActionResult {
    return {
      ok: false,
      error,
      snapshot: this.browserHost.getSnapshot()
    };
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

export function hardenEmbeddedWebContents(webContents: WebContents): void {
  webContents.setWindowOpenHandler(() => {
    return { action: "deny" };
  });
  webContents.on("will-navigate", (event, url) => {
    if (url.startsWith("file:") || url.startsWith("javascript:")) {
      event.preventDefault();
    }
  });
  webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  webContents.session.setPermissionCheckHandler(() => false);
  webContents.setAudioMuted(true);
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

function sanitizeSessionForRenderer(session: BrowserSession): BrowserSession {
  return {
    ...session,
    current_url: redactUrl(session.current_url),
    last_observation: sanitizeObservationForRenderer(session.last_observation)
  };
}

function sanitizeEventForRenderer(event: BrowserActivityEvent): BrowserActivityEvent {
  return {
    ...event,
    action: sanitizeActionForRenderer(event.action),
    url: redactUrl(event.url),
    screenshot_url: event.screenshot_url ? "[redacted:screenshot]" : undefined,
    error: event.error ? redactSensitiveText(event.error) : undefined
  };
}

function sanitizeActionForRenderer(action: BrowserAction | undefined): BrowserAction | undefined {
  if (!action) return undefined;
  const sanitized: BrowserAction = { ...action };
  if (typeof sanitized.url === "string") {
    sanitized.url = redactUrl(sanitized.url);
  }
  if (typeof sanitized.selector === "string") {
    sanitized.selector = "[redacted]";
  }
  if (typeof sanitized.text === "string") {
    sanitized.text = "[redacted]";
  }
  if (sanitized.fields && Object.keys(sanitized.fields).length) {
    sanitized.fields = Object.fromEntries(
      Object.entries(sanitized.fields).map(([key, value], index) => [
        `field_${index + 1}`,
        typeof value === "string" ? "[redacted]" : value
      ])
    ) as Record<string, string>;
  }
  return sanitized;
}

function sanitizeActionResultForRenderer(result: BrowserHostActionResult): BrowserHostActionResult {
  return {
    ...result,
    session: result.session ? sanitizeSessionForRenderer(result.session) : undefined,
    event: result.event ? sanitizeEventForRenderer(result.event) : undefined,
    snapshot: result.snapshot ? sanitizeSnapshotForRenderer(result.snapshot) : undefined,
    error: result.error ? redactSensitiveText(result.error) : undefined
  };
}

function sanitizeSnapshotForRenderer(snapshot: BrowserHostSnapshot): BrowserHostSnapshot {
  return {
    ...snapshot,
    sessions: snapshot.sessions.map(sanitizeSessionForRenderer),
    events: snapshot.events.map(sanitizeEventForRenderer)
  };
}

function sanitizeObservationForRenderer(value: BrowserSession["last_observation"]): BrowserSession["last_observation"] {
  if (typeof value === "string") {
    return value;
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  return sanitizeRecordForRenderer(value);
}

function sanitizeRecordForRenderer(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      const lowered = key.toLowerCase();
      if (typeof item === "string") {
        if (lowered === "url" || lowered.endsWith("_url") || lowered === "href") {
          return [key, redactUrl(item)];
        }
        if (lowered === "text" || lowered.endsWith("_text")) {
          return [key, item ? "[redacted:text]" : ""];
        }
        if (SENSITIVE_QUERY_KEYS.has(lowered)) {
          return [key, "[redacted]"];
        }
        return [key, redactSensitiveText(item)];
      }
      if (Array.isArray(item)) {
        return [key, item.map((child) => (child && typeof child === "object" ? sanitizeRecordForRenderer(child as Record<string, unknown>) : child))];
      }
      if (item && typeof item === "object") {
        return [key, sanitizeRecordForRenderer(item as Record<string, unknown>)];
      }
      return [key, item];
    })
  );
}

function redactUrl(value: string): string;
function redactUrl(value: undefined): undefined;
function redactUrl(value: string | undefined): string | undefined;
function redactUrl(value: string | undefined): string | undefined {
  if (!value) return value;
  try {
    const parsed = new URL(value);
    for (const key of [...parsed.searchParams.keys()]) {
      if (SENSITIVE_QUERY_KEYS.has(key.toLowerCase())) {
        parsed.searchParams.set(key, "[redacted]");
      }
    }
    parsed.hash = redactUrlFragment(parsed.hash);
    if (parsed.username) parsed.username = "[redacted]";
    if (parsed.password) parsed.password = "[redacted]";
    return parsed.toString();
  } catch {
    return value.replace(SENSITIVE_URL_PARAM_REGEX, "$1[redacted]");
  }
}

function redactUrlFragment(hash: string): string {
  if (!hash) return hash;
  return hash.replace(SENSITIVE_URL_PARAM_REGEX, "$1[redacted]");
}

function redactSensitiveText(value: string): string {
  return value
    .replace(URL_IN_TEXT_REGEX, (match) => redactUrl(match))
    .replace(/\b[a-z][\w-]*\[[^\]]*(?:password|token|secret|cookie|session|auth|key)[^\]]*\]/gi, "[redacted]")
    .replace(/\[[^\]]*(?:password|token|secret|cookie|session|auth|key)[^\]]*\]/gi, "[redacted]")
    .replace(/#[A-Za-z0-9_-]*(?:password|token|secret|cookie|session|auth|key)[A-Za-z0-9_-]*/gi, "#[redacted]")
    .replace(/\b(?:token|password|secret|api[_-]?key|authorization|cookie|session|jwt|oauth)[\w.-]*\s*[:=]\s*[^\s"'<>]+/gi, (match) =>
      match.replace(/([:=]\s*)[^\s"'<>]+/, "$1[redacted]")
    )
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b/gi, "Bearer [redacted]")
    .replace(/\bsk-[A-Za-z0-9_-]{8,}\b/g, "sk-[redacted]");
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

function domClickScript(selector: string): string {
  return `
    (() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) throw new Error("Selector not found: ${escapeForScriptMessage(selector)}");
      element.scrollIntoView({ block: "center", inline: "center" });
      element.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      return true;
    })()
  `;
}

function domFillScript(selector: string, text: string): string {
  return `
    (() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) throw new Error("Selector not found: ${escapeForScriptMessage(selector)}");
      element.scrollIntoView({ block: "center", inline: "center" });
      element.focus();
      if (!("value" in element)) throw new Error("Selector is not fillable: ${escapeForScriptMessage(selector)}");
      element.value = ${JSON.stringify(text)};
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()
  `;
}

function domSubmitScript(selector: string): string {
  return `
    (() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!element) throw new Error("Selector not found: ${escapeForScriptMessage(selector)}");
      const form = element instanceof HTMLFormElement ? element : element.closest("form");
      if (!form) throw new Error("No form found for selector: ${escapeForScriptMessage(selector)}");
      form.requestSubmit();
      return true;
    })()
  `;
}

function domScrollScript(deltaY: number): string {
  return `
    (() => {
      window.scrollBy({ top: ${JSON.stringify(deltaY)}, behavior: "smooth" });
      return { x: window.scrollX, y: window.scrollY };
    })()
  `;
}

function observeScript(): string {
  return `
    (() => ({
      url: location.href,
      title: document.title,
      text: document.body ? document.body.innerText.slice(0, 4000) : "",
      links: Array.from(document.links).slice(0, 40).map((link) => ({
        text: link.innerText.slice(0, 120),
        url: link.href
      }))
    }))()
  `;
}

function escapeForScriptMessage(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, Math.max(0, Math.min(ms, 30_000)));
  });
}

export function buildBrowserHostWebSocketUrl(baseUrl: string): string {
  if (!isLoopbackBackendUrl(baseUrl)) {
    throw new Error("BrowserHost WebSocket bridge requires a loopback backend baseUrl");
  }
  return buildBackendWebSocketUrl(baseUrl, "/api/ws/browser-host");
}

export function isLoopbackBackendUrl(baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    if (!["http:", "https:"].includes(url.protocol)) {
      return false;
    }
    return isLoopbackHostname(url.hostname);
  } catch {
    return false;
  }
}

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized === "localhost" || normalized === "::1" || normalized === "0:0:0:0:0:0:0:1") {
    return true;
  }
  const ipv4Match = normalized.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (!ipv4Match) return false;
  return ipv4Match.slice(1).every((part) => Number(part) >= 0 && Number(part) <= 255) && Number(ipv4Match[1]) === 127;
}

type BrowserHostBridgeMessage =
  | {
      type: "open";
      request_id?: string;
      session_id?: string;
      task_id?: string;
      url?: string;
      title?: string;
      mode?: string;
    }
  | { type: "show" | "pause" | "resume" | "takeover" | "release" | "stop"; request_id?: string; session_id: string }
  | { type: "hide"; request_id?: string }
  | { type: "set_bounds"; request_id?: string; bounds: BrowserHostBounds }
  | { type: "action"; request_id?: string; session_id: string; action: BrowserAction }
  | { type: "ping"; request_id?: string };

function parseBridgeMessage(rawData: unknown): BrowserHostBridgeMessage | null {
  try {
    const rawText = typeof rawData === "string" ? rawData : rawData instanceof Buffer ? rawData.toString("utf8") : String(rawData);
    const data = JSON.parse(rawText) as Record<string, unknown>;
    const type = typeof data.type === "string" ? data.type : "";
    const request_id = typeof data.request_id === "string" ? data.request_id : undefined;

    if (type === "open") {
      return {
        type,
        request_id,
        session_id: optionalMessageString(data.session_id),
        task_id: optionalMessageString(data.task_id),
        url: optionalMessageString(data.url),
        title: optionalMessageString(data.title),
        mode: optionalMessageString(data.mode)
      };
    }

    if (["show", "pause", "resume", "takeover", "release", "stop"].includes(type)) {
      const session_id = optionalMessageString(data.session_id);
      return session_id ? { type: type as "show" | "pause" | "resume" | "takeover" | "release" | "stop", request_id, session_id } : null;
    }

    if (type === "hide" || type === "ping") {
      return { type, request_id } as BrowserHostBridgeMessage;
    }

    if (type === "set_bounds" && isBounds(data.bounds)) {
      return { type, request_id, bounds: data.bounds };
    }

    if (type === "action" && isBrowserActionMessage(data.action)) {
      const session_id = optionalMessageString(data.session_id);
      return session_id ? { type, request_id, session_id, action: data.action } : null;
    }

    return null;
  } catch {
    return null;
  }
}

function isBounds(value: unknown): value is BrowserHostBounds {
  if (!value || typeof value !== "object") return false;
  const bounds = value as BrowserHostBounds;
  return [bounds.x, bounds.y, bounds.width, bounds.height].every((item) => typeof item === "number" && Number.isFinite(item));
}

function isBrowserActionMessage(value: unknown): value is BrowserAction {
  return Boolean(value && typeof value === "object" && typeof (value as { kind?: unknown }).kind === "string");
}

function optionalMessageString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function assertBrowserHostRenderer(event: IpcMainInvokeEvent): void {
  const url = event.senderFrame?.url ?? "";
  if (!BrowserWindow.fromWebContents(event.sender) || !isTrustedRendererUrl(url)) {
    throw new Error("Browser host request came from an unknown renderer");
  }
}
