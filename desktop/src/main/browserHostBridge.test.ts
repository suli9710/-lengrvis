import { describe, expect, it } from "vitest";

import {
  buildBrowserHostWebSocketUrl,
  isLoopbackBackendUrl,
  isReadOnlyBrowserHostAction,
  parseBrowserHostBridgeMessage
} from "./browserHostBridge";

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
});
