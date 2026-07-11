import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BrowserHostSnapshot } from "../shared/browserTypes";

import {
  BrowserHostWebSocketBridge,
  buildBrowserHostWebSocketUrl,
  isLoopbackBackendUrl,
  isReadOnlyBrowserHostAction,
  parseBrowserHostBridgeMessage,
  sanitizeActionResultForBrowserHostBridge
} from "./browserHostBridge";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("browserHostBridge", () => {
  it("only builds websocket URLs for loopback backends", () => {
    expect(isLoopbackBackendUrl("http://127.0.0.1:8000")).toBe(true);
    expect(isLoopbackBackendUrl("https://[::1]:8000")).toBe(true);
    expect(isLoopbackBackendUrl("https://example.test")).toBe(false);
    expect(isLoopbackBackendUrl("file:///tmp/backend")).toBe(false);

    expect(buildBrowserHostWebSocketUrl("http://127.0.0.1:8000")).toBe("ws://127.0.0.1:8000/api/ws/browser-host");
    expect(() => buildBrowserHostWebSocketUrl("https://example.test")).toThrow(/loopback backend/);
  });

  it("parses open, bounds, and read-only action messages from text or buffers", () => {
    expect(
      parseBrowserHostBridgeMessage(
        JSON.stringify({
          mode: "background",
          request_id: "req_1",
          session_id: "session_1",
          task_id: "task_1",
          title: "Docs",
          type: "open",
          url: "https://example.test"
        })
      )
    ).toEqual({
      mode: "background",
      request_id: "req_1",
      session_id: "session_1",
      task_id: "task_1",
      title: "Docs",
      type: "open",
      url: "https://example.test"
    });

    expect(
      parseBrowserHostBridgeMessage(Buffer.from(JSON.stringify({ bounds: { height: 600, width: 800, x: 1, y: 2 }, type: "set_bounds" })))
    ).toEqual({ bounds: { height: 600, width: 800, x: 1, y: 2 }, request_id: undefined, type: "set_bounds" });

    const action = { kind: "observe" };
    expect(parseBrowserHostBridgeMessage(JSON.stringify({ action, session_id: "session_1", type: "action" }))).toEqual({
      action,
      request_id: undefined,
      session_id: "session_1",
      type: "action"
    });
  });

  it("rejects malformed messages and identifies read-only actions", () => {
    expect(parseBrowserHostBridgeMessage("{")).toBeNull();
    expect(parseBrowserHostBridgeMessage(JSON.stringify({ session_id: "", type: "show" }))).toBeNull();
    expect(parseBrowserHostBridgeMessage(JSON.stringify({ bounds: { height: 1, width: 1, x: 0, y: "2" }, type: "set_bounds" }))).toBeNull();
    expect(parseBrowserHostBridgeMessage(JSON.stringify({ action: { value: "missing kind" }, session_id: "s1", type: "action" }))).toBeNull();

    expect(isReadOnlyBrowserHostAction({ kind: "observe" })).toBe(true);
    expect(isReadOnlyBrowserHostAction({ kind: "screenshot" })).toBe(true);
    expect(isReadOnlyBrowserHostAction({ kind: "click" })).toBe(false);
  });

  it("preserves only controlled screenshot artifacts for the backend bridge", () => {
    const screenshotUrl = pathToFileURL(join(
      tmpdir(),
      "lengrvis-browser-screenshots-123-123e4567-e89b-12d3-a456-426614174000",
      "123e4567-e89b-12d3-a456-426614174001.png"
    )).toString();

    const sanitized = sanitizeActionResultForBrowserHostBridge({
      ok: true,
      event: {
        id: "event-1",
        session_id: "session-1",
        created_at: "2026-01-01T00:00:00.000Z",
        type: "action.screenshot",
        action: { kind: "screenshot", selector: "#secret-token" },
        ok: true,
        screenshot_url: screenshotUrl
      }
    });

    expect(sanitized.event?.screenshot_url).toBe(screenshotUrl);
    expect(sanitized.event?.action?.selector).toBe("[redacted]");

    const unsafe = sanitizeActionResultForBrowserHostBridge({
      ...sanitized,
      event: { ...sanitized.event!, screenshot_url: "file:///private/arbitrary.png" }
    });
    expect(unsafe.event?.screenshot_url).toBe("[redacted:screenshot]");
  });

  it("sends sanitized snapshots and handles connected read-only requests", async () => {
    const sockets: FakeWebSocket[] = [];
    class TestWebSocket extends FakeWebSocket {
      static readonly OPEN = 1;

      constructor(url: string, protocols: string[]) {
        super(url, protocols);
        sockets.push(this);
      }
    }
    vi.stubGlobal("WebSocket", TestWebSocket);
    const snapshot = bridgeSnapshot("data:image/png;base64,raw-secret-image");
    const performAction = vi.fn(async () => ({
      ok: true,
      event: {
        id: "observe-1",
        session_id: "session-1",
        created_at: "2026-01-01T00:00:01.000Z",
        type: "action.observe",
        action: { kind: "observe" as const },
        ok: true
      },
      snapshot
    }));
    const bridge = new BrowserHostWebSocketBridge({
      getSnapshot: () => snapshot,
      hide: () => ({ ok: true }),
      onSnapshot: () => () => undefined,
      open: async () => ({ ok: true }),
      pause: () => ({ ok: true }),
      performAction,
      release: () => ({ ok: true }),
      resume: () => ({ ok: true }),
      setBounds: () => ({ ok: true }),
      show: () => ({ ok: true }),
      stop: async () => ({ ok: true })
    }, () => "http://127.0.0.1:8000", () => "desktop-token");

    bridge.start();
    const socket = sockets[0];
    socket.open();
    const initial = JSON.parse(socket.sent[0]) as { snapshot: BrowserHostSnapshot };
    expect(initial.snapshot.events[0]?.screenshot_url).toBe("[redacted:screenshot]");

    socket.message(JSON.stringify({
      type: "action",
      request_id: "request-1",
      session_id: "session-1",
      action: { kind: "observe" }
    }));
    await vi.waitFor(() => expect(performAction).toHaveBeenCalledWith("session-1", { kind: "observe" }));
    const response = socket.sent.map((payload) => JSON.parse(payload) as { type: string; request_id?: string })
      .find((payload) => payload.type === "result");
    expect(response?.request_id).toBe("request-1");
    bridge.stop();
  });
});

class FakeWebSocket {
  static readonly OPEN = 1;
  readonly sent: string[] = [];
  readyState = 0;
  private readonly listeners = new Map<string, Array<(event: { data?: unknown }) => void>>();

  constructor(readonly url: string, readonly protocols: string[]) {}

  addEventListener(type: string, listener: (event: { data?: unknown }) => void): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  send(payload: string): void {
    this.sent.push(payload);
  }

  close(): void {
    this.readyState = 3;
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.emit("open", {});
  }

  message(data: unknown): void {
    this.emit("message", { data });
  }

  private emit(type: string, event: { data?: unknown }): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function bridgeSnapshot(screenshotUrl?: string): BrowserHostSnapshot {
  return {
    sessions: [],
    events: [{
      id: "event-1",
      session_id: "session-1",
      created_at: "2026-01-01T00:00:00.000Z",
      type: "action.screenshot",
      action: { kind: "screenshot" },
      ok: true,
      screenshot_url: screenshotUrl
    }],
    activeSessionId: "session-1",
    visible: false,
    hostAvailable: true
  };
}
