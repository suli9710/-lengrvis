import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  BrowserActivityEvent,
  BrowserHostSnapshot,
  BrowserSession
} from "../../../shared/browserTypes";
import {
  exportBrowserReplayEndpoint,
  listBrowserSessionsEndpoint,
  observeBrowserSessionEndpoint,
  openBrowserHostEndpoint,
  type BrowserEndpointRequest
} from "./browserClient";

function browserSession(id: string, updatedAt: string): BrowserSession {
  return {
    id,
    current_url: `https://example.test/${id}`,
    title: id,
    status: "running",
    mode: "agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: updatedAt,
    paused: false,
    takeover: false
  };
}

function browserEvent(id: string, sessionId: string): BrowserActivityEvent {
  return {
    id,
    session_id: sessionId,
    type: "observe",
    ok: true,
    created_at: "2026-01-01T00:03:00Z"
  };
}

function installBrowserHost(snapshot: BrowserHostSnapshot) {
  const getSnapshot = vi.fn().mockResolvedValue(snapshot);
  (window as unknown as { lengrvis?: { browserHost: { getSnapshot: typeof getSnapshot } } }).lengrvis = {
    browserHost: { getSnapshot }
  };
  return getSnapshot;
}

describe("browser client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("merges backend and host sessions with the newest session first", async () => {
    installBrowserHost({
      sessions: [browserSession("host_session", "2026-01-01T00:04:00Z")],
      events: [],
      activeSessionId: "host_session",
      visible: true,
      hostAvailable: true
    });
    const request = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        ok: true,
        sessions: [
          {
            id: "backend_session",
            current_url: "https://example.test/backend",
            updated_at: "2026-01-01T00:02:00Z"
          }
        ]
      }
    });

    const response = await listBrowserSessionsEndpoint(request as BrowserEndpointRequest);

    expect(request).toHaveBeenCalledWith({ endpoint: "/api/browser/sessions", timeoutMs: 2500 });
    expect(response.ok).toBe(true);
    expect(response.data?.map((session) => session.id)).toEqual(["host_session", "backend_session"]);
  });

  it("keeps host-owned observation away from the backend adapter", async () => {
    installBrowserHost({
      sessions: [browserSession("host_session", "2026-01-01T00:04:00Z")],
      events: [],
      activeSessionId: "host_session",
      visible: true,
      hostAvailable: true
    });
    const request = vi.fn();

    const response = await observeBrowserSessionEndpoint(
      request as BrowserEndpointRequest,
      "host_session"
    );

    expect(request).not.toHaveBeenCalled();
    expect(response).toMatchObject({
      ok: false,
      status: 204,
      error: { code: "DESKTOP_BROWSER_HOST_SESSION" }
    });
  });

  it("exports a host replay and filters unrelated events", async () => {
    const session = browserSession("host_session", "2026-01-01T00:04:00Z");
    installBrowserHost({
      sessions: [session],
      events: [browserEvent("wanted", session.id), browserEvent("unrelated", "other_session")],
      activeSessionId: session.id,
      visible: true,
      hostAvailable: true
    });
    const request = vi.fn();

    const response = await exportBrowserReplayEndpoint(
      request as BrowserEndpointRequest,
      session.id
    );

    expect(request).not.toHaveBeenCalled();
    expect(response.data).toMatchObject({
      ok: true,
      session: { id: session.id },
      events: [{ id: "wanted", session_id: session.id }]
    });
  });

  it("returns a stable fallback when the desktop host is unavailable", async () => {
    const response = await openBrowserHostEndpoint({ url: "https://example.test" });

    expect(response).toEqual({
      ok: false,
      snapshot: {
        sessions: [],
        events: [],
        activeSessionId: null,
        visible: false,
        hostAvailable: false
      },
      error: "Desktop browser host is unavailable"
    });
  });
});
