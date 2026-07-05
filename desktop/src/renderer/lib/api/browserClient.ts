import type {
  BrowserAction,
  BrowserActivityEvent,
  BrowserHostActionResult,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  BrowserLinkResult,
  BrowserPageSnapshot,
  BrowserReplayExport,
  BrowserSession
} from "../../../shared/browserTypes";
import type { ApiRequest, ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  BackendBrowserActivityEnvelope,
  BackendBrowserEvents,
  BackendBrowserLinks,
  BackendBrowserPage,
  BackendBrowserReplayExport,
  BackendBrowserSessionStreamEvent,
  BackendBrowserSessions
} from "./browserBackendTypes";
import {
  mapBrowserActivityEnvelope,
  mapBrowserActivityEvent,
  mapBrowserLink,
  mapBrowserPage,
  mapBrowserReplayExport,
  mapBrowserSession
} from "./browserMappers";
import { emptyBrowserHostSnapshot, mergeBrowserSessionArrays } from "./emptyStateMappers";
import { mapResponse } from "./transport";

export type BrowserEndpointRequest = <TResponse, TBody = unknown>(
  request: ApiRequest<TBody>
) => Promise<ApiResponse<TResponse>>;

type BrowserSessionCommand = "pause" | "resume" | "takeover" | "release";

function unavailableBrowserHostResult(): BrowserHostActionResult {
  return {
    ok: false,
    snapshot: emptyBrowserHostSnapshot(false),
    error: "Desktop browser host is unavailable"
  };
}

export function readBrowserPageEndpoint(
  request: BrowserEndpointRequest,
  url: string
): Promise<ApiResponse<BrowserPageSnapshot>> {
  return request<BackendBrowserPage>({
    endpoint: "/api/browser/read",
    query: { url },
    timeoutMs: 20_000
  }).then((response) => mapResponse(response, mapBrowserPage));
}

export function getBrowserLinksEndpoint(
  request: BrowserEndpointRequest,
  url: string
): Promise<ApiResponse<BrowserLinkResult[]>> {
  return request<BackendBrowserLinks>({
    endpoint: "/api/browser/links",
    query: { url },
    timeoutMs: 20_000
  }).then((response) => mapResponse(response, (data) => data.links.map(mapBrowserLink)));
}

export async function listBrowserSessionsEndpoint(
  request: BrowserEndpointRequest
): Promise<ApiResponse<BrowserSession[]>> {
  const receivedAt = new Date().toISOString();
  const [hostResult, backendResult] = await Promise.allSettled([
    getBrowserHostSnapshotEndpoint(),
    request<BackendBrowserSessions>({ endpoint: "/api/browser/sessions", timeoutMs: 2500 })
  ]);

  const snapshot = hostResult.status === "fulfilled" ? hostResult.value : emptyBrowserHostSnapshot(false);
  const backendSessions =
    backendResult.status === "fulfilled" && backendResult.value.ok && backendResult.value.data?.ok !== false
      ? (backendResult.value.data?.sessions ?? []).map(mapBrowserSession)
      : [];

  if (hostResult.status === "fulfilled" || backendResult.status === "fulfilled") {
    return {
      ok: true,
      status: snapshot.hostAvailable || backendSessions.length ? 200 : 204,
      data: mergeBrowserSessionArrays(backendSessions, snapshot.sessions),
      receivedAt
    };
  }

  return {
    ok: false,
    status: 0,
    error: {
      code: "BROWSER_ACTIVITY_UNAVAILABLE",
      message: "Browser activity state is unavailable"
    },
    receivedAt
  };
}

export async function listBrowserSessionEventsEndpoint(
  request: BrowserEndpointRequest,
  sessionId: string,
  limit = 200
): Promise<ApiResponse<BrowserActivityEvent[]>> {
  const response = await request<BackendBrowserEvents>({
    endpoint: `/api/browser/session/${encodeURIComponent(sessionId)}/events`,
    query: { limit },
    timeoutMs: 2500
  });
  const mapped = mapResponse(response, (data) => (data.events ?? []).map(mapBrowserActivityEvent));
  if (mapped.ok && response.data?.ok === false) {
    return {
      ok: false,
      status: response.status,
      error: {
        message: response.data.error ?? "Browser session events unavailable",
        details: response.data
      },
      receivedAt: response.receivedAt
    };
  }
  return mapped;
}

export async function observeBrowserSessionEndpoint(
  request: BrowserEndpointRequest,
  sessionId: string
): Promise<ApiResponse<BrowserActivityEvent>> {
  const hostSession = await hasBrowserHostSessionEndpoint(sessionId);
  if (hostSession) {
    return {
      ok: false,
      status: 204,
      error: {
        code: "DESKTOP_BROWSER_HOST_SESSION",
        message: "Using desktop browser host observation."
      },
      receivedAt: new Date().toISOString()
    };
  }

  const response = window.lengrvis?.browserBackend
    ? await window.lengrvis.browserBackend.observe({ sessionId }) as ApiResponse<BackendBrowserActivityEnvelope>
    : await request<BackendBrowserActivityEnvelope, { session_id: string }>({
        endpoint: "/api/browser/observe",
        method: "POST",
        body: { session_id: sessionId },
        timeoutMs: 10_000
      });
  const mapped = mapResponse(response, mapBrowserActivityEnvelope);
  if (mapped.ok && mapped.data?.ok === false) {
    return {
      ok: false,
      status: response.status,
      error: {
        message: mapped.data.error ?? "Browser observe failed",
        details: response.data
      },
      receivedAt: response.receivedAt
    };
  }
  return mapped;
}

export function browserSessionCommandEndpoint(
  sessionId: string,
  command: BrowserSessionCommand
): Promise<ApiResponse<BrowserSession>> {
  void sessionId;
  return Promise.resolve({
    ok: false,
    status: 501,
    error: {
      code: "UNSUPPORTED_BROWSER_SESSION_COMMAND",
      message: `Backend browser ${command} is not exposed; using desktop browser host state.`
    },
    receivedAt: new Date().toISOString()
  });
}

export async function exportBrowserReplayEndpoint(
  request: BrowserEndpointRequest,
  sessionId: string
): Promise<ApiResponse<BrowserReplayExport>> {
  const hostReplay = await exportBrowserHostReplayEndpoint(sessionId);
  if (hostReplay) return hostReplay;

  const response = window.lengrvis?.browserBackend
    ? await window.lengrvis.browserBackend.replayExport({ sessionId }) as ApiResponse<BackendBrowserReplayExport>
    : await request<BackendBrowserReplayExport, { session_id: string }>({
        endpoint: "/api/browser/replay-export",
        method: "POST",
        body: { session_id: sessionId },
        timeoutMs: 20_000
      });
  const mapped = mapResponse(response, mapBrowserReplayExport);
  if (mapped.ok && mapped.data?.ok === false) {
    return {
      ok: false,
      status: response.status,
      error: {
        message: mapped.data.error ?? "Replay export failed",
        details: response.data
      },
      receivedAt: response.receivedAt
    };
  }
  return mapped;
}

export function subscribeBrowserSessionEndpoint(
  sessionId: string,
  handlers: {
    onMessage: (message: BackendBrowserSessionStreamEvent) => void;
    onError?: (error: Event) => void;
    onOpen?: () => void;
  }
): () => void {
  void sessionId;
  void handlers;
  // Current backend has HTTP browser activity endpoints and Electron host snapshots, but no per-session browser WebSocket.
  return () => undefined;
}

export function getBrowserHostSnapshotEndpoint(): Promise<BrowserHostSnapshot> {
  return window.lengrvis?.browserHost.getSnapshot() ?? Promise.resolve(emptyBrowserHostSnapshot(false));
}

export function openBrowserHostEndpoint(request: BrowserHostOpenRequest): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.open(request) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function showBrowserHostEndpoint(sessionId: string): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.show(sessionId) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function hideBrowserHostEndpoint(): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.hide() ?? Promise.resolve(unavailableBrowserHostResult());
}

export function setBrowserHostBoundsEndpoint(bounds: {
  x: number;
  y: number;
  width: number;
  height: number;
}): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.setBounds(bounds) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function pauseBrowserHostEndpoint(sessionId: string): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.pause(sessionId) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function resumeBrowserHostEndpoint(sessionId: string): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.resume(sessionId) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function takeoverBrowserHostEndpoint(sessionId: string): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.takeover(sessionId) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function releaseBrowserHostEndpoint(sessionId: string): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.release(sessionId) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function stopBrowserHostEndpoint(sessionId: string): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.stop(sessionId) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function performBrowserHostActionEndpoint(
  sessionId: string,
  action: BrowserAction
): Promise<BrowserHostActionResult> {
  return window.lengrvis?.browserHost.performAction({ sessionId, action }) ?? Promise.resolve(unavailableBrowserHostResult());
}

export function subscribeBrowserHostSnapshotsEndpoint(handler: (snapshot: BrowserHostSnapshot) => void): () => void {
  return window.lengrvis?.browserHost.onSnapshot(handler) ?? (() => undefined);
}

export async function exportBrowserHostReplayEndpoint(
  sessionId: string
): Promise<ApiResponse<BrowserReplayExport> | null> {
  if (!window.lengrvis?.browserHost) return null;
  const receivedAt = new Date().toISOString();
  try {
    const snapshot = await getBrowserHostSnapshotEndpoint();
    const session = snapshot.sessions.find((item) => item.id === sessionId);
    if (!session) return null;
    return {
      ok: true,
      status: 200,
      data: {
        ok: true,
        session,
        events: snapshot.events.filter((event) => event.session_id === sessionId)
      },
      receivedAt
    };
  } catch (error) { // broad-exception-boundary
    return {
      ok: false,
      status: 0,
      error: {
        code: "BROWSER_HOST_UNAVAILABLE",
        message: error instanceof Error ? error.message : "Browser host replay is unavailable"
      },
      receivedAt
    };
  }
}

export async function hasBrowserHostSessionEndpoint(sessionId: string): Promise<boolean> {
  if (!window.lengrvis?.browserHost) return false;
  try {
    const snapshot = await getBrowserHostSnapshotEndpoint();
    return snapshot.sessions.some((session) => session.id === sessionId);
  } catch {
    return false;
  }
}
