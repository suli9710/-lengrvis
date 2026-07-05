import { afterEach, describe, expect, it, vi } from "vitest";

import {
  classifyRealtimeIssue,
  parseJsonRealtimeMessage,
  realtimeStatusFromClose,
  shouldRetryRealtime,
  subscribeJsonRealtime,
  webOnlyDevDesktopWebSocketProtocols
} from "./realtimeTransport";

describe("realtime transport", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("uses the Electron realtime adapter and forwards parsed messages", () => {
    const unsubscribe = vi.fn();
    const subscribe = vi.fn().mockReturnValue(unsubscribe);
    (window as unknown as { lengrvis?: { realtime: { subscribe: typeof subscribe } } }).lengrvis = {
      realtime: { subscribe }
    };
    const onMessage = vi.fn();
    const onOpen = vi.fn();
    const onStatus = vi.fn();

    const stop = subscribeJsonRealtime<{ taskId: string }>(
      { endpoint: "/ws/tasks/task-one" },
      { onMessage, onOpen, onStatus }
    );
    const bridgeHandlers = subscribe.mock.calls[0][1] as {
      onOpen: () => void;
      onMessage: (data: string) => void;
    };
    bridgeHandlers.onOpen();
    bridgeHandlers.onMessage('{"taskId":"task-one"}');
    stop();

    expect(subscribe).toHaveBeenCalledWith(
      { endpoint: "/ws/tasks/task-one" },
      expect.any(Object)
    );
    expect(onStatus.mock.calls.map(([status]) => status.state)).toEqual(["connecting", "open"]);
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onMessage).toHaveBeenCalledWith({ taskId: "task-one" });
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it("reports malformed messages through the bad-message interface", () => {
    const onMessage = vi.fn();
    const onBadMessage = vi.fn();
    const onStatus = vi.fn();

    parseJsonRealtimeMessage("not-json", { endpoint: "/ws/tasks/task-one" }, {
      onMessage,
      onBadMessage,
      onStatus
    });

    expect(onMessage).not.toHaveBeenCalled();
    expect(onBadMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        state: "bad_message",
        endpoint: "/ws/tasks/task-one",
        rawMessage: "not-json"
      })
    );
    expect(onStatus).toHaveBeenCalledWith(expect.objectContaining({ state: "bad_message" }));
  });

  it("classifies terminal issues and retryable closes", () => {
    expect(classifyRealtimeIssue(1008, "")).toBe("policy_violation");
    expect(classifyRealtimeIssue(undefined, "HTTP 401 unauthorized")).toBe("unauthorized");

    const clean = realtimeStatusFromClose(
      { endpoint: "/ws/runs/run-one" },
      { code: 1000, reason: "done", wasClean: true },
      1
    );
    const retry = realtimeStatusFromClose(
      { endpoint: "/ws/runs/run-one" },
      { code: 1006, reason: "lost", wasClean: false },
      2
    );

    expect(clean.state).toBe("closed");
    expect(shouldRetryRealtime(clean)).toBe(false);
    expect(retry.state).toBe("reconnecting");
    expect(shouldRetryRealtime(retry)).toBe(true);
  });

  it("builds the desktop token WebSocket subprotocol", () => {
    expect(webOnlyDevDesktopWebSocketProtocols(" token-value ")).toEqual([
      "lengrvis.desktop.token.token-value"
    ]);
  });
});
