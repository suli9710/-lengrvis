import {
  BrowserWindow,
  ipcMain,
  type Rectangle,
  type WebContents
} from "electron";
import { createHash, randomUUID } from "node:crypto";

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
import type {
  CredentialBrokerResult,
  CredentialFillRequest,
  CredentialRef,
  CredentialRefRequest,
  CredentialSessionRequest,
  CredentialUseTicketRequest
} from "../shared/credentialTypes";
import {
  sanitizeEventForRenderer,
  sanitizeSessionForRenderer,
  redactSensitiveText,
  redactUrl
} from "../shared/browserHostRedaction";
import { domClickScript, domFillScript, domScrollScript, domSubmitScript, observeScript } from "./browserHostDomActions";
import {
  registerBrowserCredentialIpcHandlers,
  type BrowserCredentialPreview
} from "./browserCredentialIpcHandlers";
import { registerBrowserHostIpcHandlers } from "./browserHostIpcHandlers";
import {
  createBrowserContainer,
  delay,
  destroyWebContents,
  runDomAction,
  type BrowserContainer
} from "./browserHostRuntime";
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
import { BrowserHostPinnedProxy } from "./browserHostPinnedProxy";
import { BrowserScreenshotStore } from "./browserScreenshotStore";
import { CredentialVault } from "./credentialVault";
import { BrowserHostCredentialBroker } from "./browserHostCredentials";
import { BrowserPrivacyBoundary } from "./browserPrivacyBoundary";
import { eraseBrowserPrivateData } from "./browserPrivacyErase";
import { CredentialUseTicketBroker } from "./credentialUseTicketBroker";
import type { BrowserExecutionClaim } from "./browserExecutionGrant";
import { browserTargetSnapshotScript, guardedBrowserActionScript } from "./browserTargetSnapshot";
import { BrowserProfileRegistry } from "./browserProfileRegistry";
import { browserObservationScript } from "./browserObservation";
import { BrowserSubmissionGuard, type NativeFormSubmission } from "./browserSubmissionGuard";

export { BrowserHostWebSocketBridge, buildBrowserHostWebSocketUrl, isLoopbackBackendUrl } from "./browserHostBridge";
export { registerBrowserHostIpcHandlers } from "./browserHostIpcHandlers";
export { registerBrowserCredentialIpcHandlers } from "./browserCredentialIpcHandlers";
export { hardenEmbeddedWebContents } from "./browserHostWebContentsHardening";

interface HostedBrowserSession {
  container: BrowserContainer;
  session: BrowserSession;
  events: BrowserActivityEvent[];
  proxyReady: Promise<void>;
  submissionGuard: BrowserSubmissionGuard;
  cssKey?: string;
}

interface ManagedSessionOptions {
  background?: boolean;
  profileKey?: string;
  accountId?: string;
  allowedOrigins?: string[];
}

const DEFAULT_HOME_URL = "about:blank";
const MAX_EVENTS_PER_SESSION = 300;

export class BrowserHost {
  private sessions = new Map<string, HostedBrowserSession>();
  private activeSessionId: string | null = null;
  private bounds: BrowserHostBounds | null = null;
  private visible = false;
  private snapshotListeners = new Set<(snapshot: BrowserHostSnapshot) => void>();
  private pinnedProxyPromise: Promise<BrowserHostPinnedProxy> | null = null;
  private readonly credentialBroker: BrowserHostCredentialBroker;
  private readonly targetPreviews = new Map<string, { sessionId: string; state: unknown; expires: number }>();
  private readonly revokedScopeVersions = new Map<string, number>();

  constructor(
    private readonly getMainWindow: () => BrowserWindow | null,
    private readonly credentialVault = new CredentialVault(),
    private readonly credentialTickets = new CredentialUseTicketBroker(),
    private readonly browserProfiles = new BrowserProfileRegistry(),
    private readonly screenshotStore = new BrowserScreenshotStore(),
    private readonly privacyBoundary = new BrowserPrivacyBoundary()
  ) {
    this.credentialBroker = new BrowserHostCredentialBroker({
      getSession: id => this.sessions.get(id),
      addEvent: (entry, input) => this.addEvent(entry as HostedBrowserSession, input),
      emitSnapshot: () => this.emitSnapshot()
    }, credentialVault, credentialTickets, privacyBoundary);
  }

  registerIpcHandlers(): void {
    registerBrowserHostIpcHandlers({
      handle: (channel, listener) => ipcMain.handle(channel, listener),
      host: this
    });
    registerBrowserCredentialIpcHandlers({
      handle: (channel, listener) => ipcMain.handle(channel, listener),
      host: this
    });
  }

  destroy(): void {
    this.snapshotListeners.clear();
    for (const sessionId of [...this.sessions.keys()]) {
      void this.stop(sessionId);
    }
    const proxy = this.pinnedProxyPromise;
    this.pinnedProxyPromise = null;
    if (proxy) void proxy.then((instance) => instance.close());
    void this.screenshotStore.clear();
    this.credentialTickets.clear();
  }

  beginLocalPrivateDataErase(): boolean { return this.privacyBoundary.reserve(); }
  finishLocalPrivateDataErase(): void { this.privacyBoundary.release(); }
  initializeScreenshotStore(): Promise<void> { return this.screenshotStore.initialize(); }

  async eraseLocalPrivateData(): Promise<void> {
    await eraseBrowserPrivateData({
      sessions: [...this.sessions.values()], stop: id => this.stop(id),
      screenshotStore: this.screenshotStore, browserProfiles: this.browserProfiles,
      credentialTickets: this.credentialTickets, credentialVault: this.credentialVault
    });
    this.targetPreviews.clear();
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

  async open(request: BrowserHostOpenRequest = {}, managed: ManagedSessionOptions = {}): Promise<BrowserHostActionResult> {
    const release = this.privacyBoundary.enter();
    try {
    const sessionId = normalizeBrowserHostId(request.sessionId) ?? randomUUID();

    try {
      const current = this.sessions.get(sessionId);
      const targetUrl = normalizeBrowserHostUrl(request.url) ?? current?.session.current_url ?? DEFAULT_HOME_URL;
      const entry = current ?? this.createHostedSession(sessionId, request, managed);
      this.sessions.set(sessionId, entry);
      if (!managed.background) this.activeSessionId = sessionId;
      entry.session.status = "loading";
      entry.session.mode = request.mode ?? entry.session.mode;
      entry.session.updated_at = browserHostTimestamp();
      if (!managed.background) {
        this.attachActiveView();
        this.applyBounds();
        this.setViewVisible(true);
      }
      await entry.proxyReady;

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
    } finally { release(); }
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
    this.privacyBoundary.assertAvailable();
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    if (entry.session.takeover) return this.fail("Return manual control before resuming the agent", entry);
    if (entry.session.status === "awaiting_observation") {
      return this.fail("Observe the current page before resuming the agent", entry);
    }
    entry.session.paused = false;
    entry.session.status = entry.container.view.webContents.isLoading() ? "loading" : "idle";
    entry.session.updated_at = browserHostTimestamp();
    const event = this.addEvent(entry, { type: "session.resumed", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  takeover(sessionId: string): BrowserHostActionResult {
    this.privacyBoundary.assertAvailable();
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.submissionGuard.release();
    entry.session.paused = true;
    entry.session.takeover = true;
    entry.session.mode = "takeover";
    entry.session.status = "paused";
    entry.session.updated_at = browserHostTimestamp();
    this.setInteractionBlocked(entry, false);
    this.show(sessionId);
    const event = this.addEvent(entry, { type: "session.takeover", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  release(sessionId: string): BrowserHostActionResult {
    this.privacyBoundary.assertAvailable();
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.session.takeover = false;
    entry.session.mode = "watch";
    entry.session.paused = true;
    entry.session.status = "awaiting_observation";
    entry.session.updated_at = browserHostTimestamp();
    this.setInteractionBlocked(entry, true);
    const event = this.addEvent(entry, { type: "session.release", ok: true });
    this.emitSnapshot();
    return this.ok(entry, event);
  }

  async stop(sessionId: string): Promise<BrowserHostActionResult> {
    const entry = this.sessions.get(sessionId);
    if (!entry) return this.fail("Browser session is no longer available");
    entry.submissionGuard.close();
    const event = this.addEvent(entry, { type: "session.stopped", ok: true });
    this.detachView(entry);
    // Close the page before any fallible asynchronous cleanup. A locked
    // screenshot must never leave a live, untracked page recreating storage.
    destroyWebContents(entry.container.view.webContents);
    this.sessions.delete(sessionId);
    await this.screenshotStore.removeSession(sessionId);
    if (this.activeSessionId === sessionId) {
      this.activeSessionId = this.sessions.keys().next().value ?? null;
      this.attachActiveView();
    }
    this.emitSnapshot();
    return {
      ok: true,
      session: sanitizeSessionForRenderer({ ...entry.session, status: "stopped", updated_at: browserHostTimestamp() }),
      event: sanitizeEventForRenderer(event),
      snapshot: this.getSnapshot()
    };
  }

  async performAction(sessionId: string, action: BrowserAction): Promise<BrowserHostActionResult> {
    const release = this.privacyBoundary.enter();
    try {
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
      if (action.kind === "observe" && entry.session.status === "awaiting_observation") {
        entry.session.status = "paused";
        entry.session.updated_at = browserHostTimestamp();
      }
      this.updateSessionFromWebContents(entry);
      this.emitSnapshot();
      return this.ok(entry, event, action.kind === "screenshot");
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
    } finally { release(); }
  }

  async cancelScope(scopeId: string, version: number): Promise<BrowserHostActionResult> {
    this.revokedScopeVersions.set(scopeId, Math.max(version, this.revokedScopeVersions.get(scopeId) ?? 0));
    const owned = this.getSnapshot().sessions.filter(session => session.scope_id === scopeId);
    for (const session of owned) await this.stop(session.id);
    await this.browserProfiles.clearScope(scopeId);
    return { ok: true, snapshot: this.getSnapshot() };
  }

  async performAuthorized(claim: BrowserExecutionClaim): Promise<BrowserHostActionResult> {
    const release = this.privacyBoundary.enter();
    try {
    for (const [id, version] of Object.entries(claim.scope_versions)) {
      if (version < (this.revokedScopeVersions.get(id) ?? 0)) return this.fail("Browser scope grant is obsolete");
    }
    let entry = this.sessions.get(claim.session_id);
    if (claim.operation === "adopt") {
      if (!entry || entry.session.task_id !== claim.previous_task_id || (entry.session.account_id ?? "") !== claim.account_id ||
          entry.session.takeover || entry.session.paused || !claim.allowed_origins.includes(new URL(entry.container.view.webContents.getURL()).origin) ||
          claim.allowed_origins.some(origin => !entry!.session.allowed_origins?.includes(origin))) return this.fail("Original browser ownership or scope changed");
      for (const [key, preview] of this.targetPreviews) if (preview.sessionId === claim.session_id) this.targetPreviews.delete(key);
      entry.session.task_id = claim.task_id;
      entry.session.updated_at = browserHostTimestamp();
      this.emitSnapshot();
      return this.ok(entry);
    }
    if (claim.operation === "open") {
      if (entry && entry.session.task_id !== claim.task_id) return this.fail("Browser session belongs to another task");
      if (entry) return this.ok(entry);
      const opened = await this.open({ sessionId: claim.session_id, taskId: claim.task_id, url: claim.action.url, mode: "agent" }, {
        background: true, profileKey: claim.profile_key, accountId: claim.account_id, allowedOrigins: claim.allowed_origins
      });
      return opened;
    }
    if (!entry || entry.session.task_id !== claim.task_id || (entry.session.account_id ?? "") !== claim.account_id) {
      return this.fail("Browser command belongs to another task or account");
    }
    const contents = entry.container.view.webContents;
    const origin = new URL(contents.getURL()).origin;
    if (!claim.allowed_origins.includes(origin) || !entry.session.allowed_origins?.includes(origin)) return this.fail("Browser origin changed");
    if (entry.session.takeover || (entry.session.paused && claim.action.kind !== "observe")) return this.fail("Return user control before continuing the task");
    if (claim.operation === "inspect") {
      const state = await this.runManagedScript(contents, browserTargetSnapshotScript(claim.action));
      const fingerprint = createHash("sha256").update(JSON.stringify(state)).digest("hex");
      for (const [key, preview] of this.targetPreviews) if (preview.expires < Date.now()) this.targetPreviews.delete(key);
      if (this.targetPreviews.size >= 256) this.targetPreviews.delete(this.targetPreviews.keys().next().value!);
      this.targetPreviews.set(fingerprint, { sessionId: claim.session_id, state, expires: Date.now() + 10 * 60_000 });
      const preview = (state as { targets?: Array<{ preview?: unknown }> }).targets?.map(target => target.preview) ?? [];
      return { ...this.ok(entry), inspection: { fingerprint, origin, preview } };
    }
    if (["click", "fill", "submit", "scroll"].includes(claim.action.kind)) {
      const preview = this.targetPreviews.get(claim.expected_fingerprint);
      if (!preview || preview.sessionId !== claim.session_id || preview.expires < Date.now()) return this.fail("Approved browser target snapshot expired");
      this.targetPreviews.delete(claim.expected_fingerprint);
      const submission = (preview.state as { submission?: NativeFormSubmission }).submission;
      const attempt = submission ? entry.submissionGuard.arm(submission) : undefined;
      let performed: boolean | { ok: boolean; commit_state?: "not_submitted" | "submitted"; error?: string };
      try {
        performed = await this.runManagedScript(contents, guardedBrowserActionScript(claim.action, preview.state)) as typeof performed;
      } catch (error) { // broad-exception-boundary: close the pending permit and propagate the original DOM failure.
        attempt?.cancel();
        throw error;
      }
      if (typeof performed === "object" && !performed.ok) {
        attempt?.notSubmitted();
        return { ...this.fail(performed.error ?? "Form was not submitted", entry), commit_state: performed.commit_state };
      }
      if (attempt) {
        const decision = await attempt.wait();
        if (decision !== "submitted") {
          // A cancelled navigation emits did-fail-load asynchronously. Let it
          // settle before a recovery loadURL, or Electron can reject that new
          // navigation with the old request's error.
          if (decision === "not_submitted" && contents.isLoading()) {
            await new Promise<void>(resolve => {
              const done = () => { clearTimeout(timer); contents.removeListener("did-stop-loading", done); resolve(); };
              const timer = setTimeout(done, 2000);
              contents.once("did-stop-loading", done);
              if (!contents.isLoading()) done();
            });
          }
          return {
            ...this.fail(decision === "not_submitted" ? "Actual form request did not match the approved submission" : "Form request outcome is unknown; inspect the original page before continuing", entry),
            ...(decision === "not_submitted" ? { commit_state: "not_submitted" as const } : {})
          };
        }
      }
      const event = this.addEvent(entry, { type: `action.${claim.action.kind}`, action: claim.action, ok: true });
      this.updateSessionFromWebContents(entry);
      this.emitSnapshot();
      return { ...this.ok(entry, event), ...(typeof performed === "object" ? { commit_state: performed.commit_state } : {}) };
    }
    if (claim.action.kind === "cua") return this.fail("Visual input requires a separately bound target");
    if (claim.action.kind === "observe" || claim.action.kind === "wait") {
      if (claim.action.kind === "wait") {
        const selector = requireBrowserActionSelector(claim.action);
        const timeout = Math.min(30_000, Math.max(0, Number(claim.action.timeout_ms ?? 10_000)));
        if (!Number.isFinite(timeout)) return this.fail("Invalid selector wait timeout");
        const deadline = Date.now() + timeout;
        let found = false;
        do {
          if (entry.session.paused || entry.session.takeover || contents.isDestroyed()) return this.fail("Browser wait interrupted");
          if (!claim.allowed_origins.includes(new URL(contents.getURL()).origin)) return this.fail("Browser origin changed while waiting");
          // Electron may defer script evaluation until an in-flight navigation
          // finishes. Bound that read too, so a slow receipt respects the wait.
          let timer: ReturnType<typeof setTimeout> | undefined;
          try {
            found = await Promise.race([
              this.runManagedScript(contents, `document.querySelectorAll(${JSON.stringify(selector)}).length === 1`),
              new Promise<boolean>(resolve => { timer = setTimeout(() => resolve(false), Math.max(0, deadline - Date.now())); })
            ]) === true;
          } finally { if (timer) clearTimeout(timer); }
          if (found) break;
          await delay(100);
        } while (Date.now() < deadline);
        if (!found) return this.fail("The requested browser element did not appear");
      }
      const value = await this.runManagedScript(contents, browserObservationScript(claim.action)) as Record<string, unknown>;
      const observation = {
        url: redactUrl(String(value.url ?? contents.getURL())),
        title: redactSensitiveText(String(value.title ?? contents.getTitle())),
        text: redactSensitiveText(String(value.text ?? "")),
        links: Array.isArray(value.links) ? value.links.slice(0, 40).map((link: { text?: string; url?: string }) => ({ title: redactSensitiveText(String(link.text ?? "")), url: redactUrl(String(link.url ?? "")) })) : [],
        observed_at: browserHostTimestamp(), truncated: Boolean(value.truncated),
        extracted: Object.fromEntries(Object.entries((value.extracted ?? {}) as Record<string, { text: string; matched: boolean; truncated: boolean }>).map(([key, field]) => [key, { ...field, text: redactSensitiveText(field.text) }])),
        elements: Array.isArray(value.elements) ? value.elements.map(element => ({ ...element, text: redactSensitiveText(String(element.text ?? "")) })) : [],
        controls: Array.isArray(value.controls) ? value.controls.map((control: Record<string, unknown>) => ({
          ...control, value: redactSensitiveText(String(control.value ?? "")), name: redactSensitiveText(String(control.name ?? ""))
        })) : [],
        ...(claim.action.kind === "wait" ? { present: true } : {})
      };
      entry.session.last_observation = observation;
      this.updateSessionFromWebContents(entry);
      const event = this.addEvent(entry, { type: "observe", action: claim.action, ok: true });
      this.emitSnapshot();
      return { ...this.ok(entry, event), observation };
    }
    return this.performAction(claim.session_id, claim.action);
    } finally { release(); }
  }

  private async runManagedScript(contents: WebContents, code: string): Promise<unknown> {
    const results = await contents.executeJavaScriptInIsolatedWorld(1009, [{ code }], true);
    return results;
  }

  listCredentialRefs(request: CredentialSessionRequest): CredentialRef[] { return this.credentialBroker.listCredentialRefs(request); }
  previewCredentialCapture(request: CredentialSessionRequest): Promise<BrowserCredentialPreview> { return this.credentialBroker.previewCredentialCapture(request); }
  captureCredential(request: CredentialSessionRequest, preview: BrowserCredentialPreview): Promise<CredentialBrokerResult> { return this.credentialBroker.captureCredential(request, preview); }
  previewCredentialUse(request: CredentialUseTicketRequest): Promise<BrowserCredentialPreview> { return this.credentialBroker.previewCredentialUse(request); }
  issueCredentialUseTicket(request: CredentialUseTicketRequest, preview: BrowserCredentialPreview): Promise<CredentialBrokerResult> { return this.credentialBroker.issueCredentialUseTicket(request, preview); }
  fillCredential(request: CredentialFillRequest): Promise<CredentialBrokerResult> { return this.credentialBroker.fillCredential(request); }
  previewCredentialDelete(request: CredentialRefRequest): BrowserCredentialPreview { return this.credentialBroker.previewCredentialDelete(request); }
  deleteCredential(request: CredentialRefRequest): CredentialBrokerResult { return this.credentialBroker.deleteCredential(request); }

  private createHostedSession(sessionId: string, request: BrowserHostOpenRequest, managed: ManagedSessionOptions = {}): HostedBrowserSession {
    const partition = managed.profileKey
      ? `persist:lengrvis-butler-${createHash("sha256").update(`${managed.profileKey}:${managed.accountId ?? ""}`).digest("hex").slice(0, 32)}`
      : `lengrvis-watch-${sessionId}-${Date.now()}`;
    if (managed.profileKey) this.browserProfiles.remember(partition, managed.profileKey);
    const container = createBrowserContainer(partition);
    const webContents = container.view.webContents;
    const now = browserHostTimestamp();
    const entry: HostedBrowserSession = {
      container,
      submissionGuard: new BrowserSubmissionGuard(),
      proxyReady: this.configurePinnedProxy(webContents),
      session: {
        id: sessionId,
        task_id: request.taskId,
        account_id: managed.accountId,
        scope_id: managed.profileKey,
        allowed_origins: managed.allowedOrigins,
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

    hardenEmbeddedWebContents(webContents, {
      onBeforeRequest: (request, allowed) => entry.submissionGuard.allowRequest(request, allowed, entry.session.takeover),
      onDownloadBlocked: ({ url }) => {
        this.addEvent(entry, {
          type: "download.blocked",
          ok: false,
          url,
          error: "BrowserHost downloads require an explicit desktop broker"
        });
        this.emitSnapshot();
      }
    });
    this.bindWebContentsEvents(entry);
    this.setInteractionBlocked(entry, true);
    return entry;
  }

  private configurePinnedProxy(webContents: WebContents): Promise<void> {
    this.pinnedProxyPromise ??= BrowserHostPinnedProxy.start();
    return this.pinnedProxyPromise.then(async (proxy) => {
      await webContents.session.setProxy({
        proxyRules: proxy.url,
        // Chromium implicitly bypasses proxies for loopback names unless this
        // subtraction rule is present; the proxy must see those requests so it
        // can enforce the private-network policy at connect time.
        proxyBypassRules: "<-loopback>"
      });
    });
  }

  private bindWebContentsEvents(entry: HostedBrowserSession): void {
    const { webContents } = entry.container.view;
    const guardOrigin = (event: { preventDefault: () => void }, url: string) => {
      if (!entry.session.allowed_origins?.length) return;
      try {
        if (!entry.session.allowed_origins.includes(new URL(url).origin)) event.preventDefault();
      } catch { event.preventDefault(); }
    };
    webContents.on("will-navigate", guardOrigin);
    webContents.on("will-redirect", guardOrigin);

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
        const screenshot_url = await this.screenshotStore.save(entry.session.id, image.toPNG());
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

  private ok(
    entry: HostedBrowserSession,
    event?: BrowserActivityEvent,
    preserveScreenshotArtifact = false
  ): BrowserHostActionResult {
    const sanitizedEvent = event ? sanitizeEventForRenderer(event) : undefined;
    if (preserveScreenshotArtifact && sanitizedEvent && event?.screenshot_url) {
      sanitizedEvent.screenshot_url = event.screenshot_url;
    }
    return {
      ok: true,
      session: sanitizeSessionForRenderer(entry.session),
      event: sanitizedEvent,
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
