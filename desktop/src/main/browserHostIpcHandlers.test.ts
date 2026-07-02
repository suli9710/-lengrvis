import type { IpcMainInvokeEvent } from "electron";
import { describe, expect, it, vi } from "vitest";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  BrowserAction,
  BrowserHostActionResult,
  BrowserHostSnapshot
} from "../shared/types";
import { sanitizeActionResultForRenderer } from "../shared/browserHostRedaction";
import {
  assertBrowserHostRenderer,
  deniedRendererBrowserHostWrite,
  isRendererBrowserHostActionAllowed,
  registerBrowserHostIpcHandlers,
  type BrowserHostActionConfirmation,
  type BrowserHostActionResultSanitizer,
  type BrowserHostIpcListener,
  type BrowserHostIpcTarget
} from "./browserHostIpcHandlers";

describe("browserHostIpcHandlers", () => {
  it("asserts that renderer requests come from an attached trusted renderer", () => {
    const sender = {};
    const trustedEvent = ipcEvent(sender, "app://local/index.html");

    expect(() =>
      assertBrowserHostRenderer(trustedEvent, {
        hasAttachedWindow: (candidate) => candidate === sender,
        isTrustedRendererUrl: (url) => url === "app://local/index.html"
      })
    ).not.toThrow();

    expect(() =>
      assertBrowserHostRenderer(trustedEvent, {
        hasAttachedWindow: () => false,
        isTrustedRendererUrl: () => true
      })
    ).toThrow(/unknown renderer/);

    expect(() =>
      assertBrowserHostRenderer(ipcEvent(sender, "https://example.test"), {
        hasAttachedWindow: () => true,
        isTrustedRendererUrl: () => false
      })
    ).toThrow(/unknown renderer/);
  });

  it("confirms browser opens before delegating to the host", async () => {
    const order: string[] = [];
    const { confirmNativeDesktopAction, host, invoke } = registerForTest({
      host: createHost({
        open: vi.fn(async (request) => {
          order.push("open");
          return actionResult({ sessionId: request?.sessionId });
        })
      }),
      confirmNativeDesktopAction: vi.fn(async () => {
        order.push("confirm");
      })
    });
    const request = { sessionId: "session_1", url: "https://example.test" };

    const result = await invoke(IPC_CHANNELS.browserHostOpen, request);

    expect(confirmNativeDesktopAction).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ title: "Confirm browser session" })
    );
    expect(host.open).toHaveBeenCalledWith(request);
    expect(order).toEqual(["confirm", "open"]);
    expect(result).toMatchObject({ ok: true, snapshot: expect.any(Object) });
  });

  it("allows renderer read-only actions and denies input actions", async () => {
    const { host, invoke, sanitizeActionResult } = registerForTest();
    const observeAction = { kind: "observe" } satisfies BrowserAction;

    const readOnlyResult = await invoke(IPC_CHANNELS.browserHostAction, {
      sessionId: "session_1",
      action: observeAction
    });

    expect(host.performAction).toHaveBeenCalledWith("session_1", observeAction);
    expect(readOnlyResult).toMatchObject({ ok: true });

    const deniedResult = await invoke(IPC_CHANNELS.browserHostAction, {
      sessionId: "session_1",
      action: { kind: "click", selector: "button[data-token='secret']" } satisfies BrowserAction
    });

    expect(host.performAction).toHaveBeenCalledTimes(1);
    expect(sanitizeActionResult).toHaveBeenCalled();
    expect(deniedResult).toMatchObject({
      ok: false,
      error: "BrowserHost input actions require an approval grant."
    });
    expect(JSON.stringify(deniedResult)).not.toContain("secret-token");
  });

  it("denies renderer takeover without invoking the host write method", async () => {
    const { host, invoke } = registerForTest();

    const result = await invoke(IPC_CHANNELS.browserHostTakeover, "session_1");

    expect(host.takeover).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      ok: false,
      error: "BrowserHost takeover requires an approval grant."
    });
  });

  it("exposes pure helpers for action gating and write denial", () => {
    expect(isRendererBrowserHostActionAllowed({ kind: "screenshot" })).toBe(true);
    expect(isRendererBrowserHostActionAllowed({ kind: "fill", text: "secret" })).toBe(false);

    const sanitizer = vi.fn((result: BrowserHostActionResult) => ({
      ...result,
      error: result.error ? `sanitized:${result.error}` : result.error
    }));
    const result = deniedRendererBrowserHostWrite(createHost(), "blocked", sanitizer);

    expect(sanitizer).toHaveBeenCalled();
    expect(result).toMatchObject({ ok: false, error: "sanitized:blocked" });
  });
});

function registerForTest({
  confirmNativeDesktopAction = vi.fn<BrowserHostActionConfirmation>(async () => undefined),
  host = createHost()
}: {
  confirmNativeDesktopAction?: BrowserHostActionConfirmation;
  host?: BrowserHostIpcTarget;
} = {}): {
  confirmNativeDesktopAction: BrowserHostActionConfirmation;
  host: BrowserHostIpcTarget;
  invoke: (channel: string, ...args: unknown[]) => Promise<unknown>;
  sanitizeActionResult: BrowserHostActionResultSanitizer;
} {
  const handlers = new Map<string, BrowserHostIpcListener>();
  const sanitizeActionResult = vi.fn<BrowserHostActionResultSanitizer>(sanitizeActionResultForRenderer);

  registerBrowserHostIpcHandlers({
    handle: (channel, listener) => {
      handlers.set(channel, listener);
    },
    host,
    assertTrustedRenderer: vi.fn(),
    confirmNativeDesktopAction,
    sanitizeActionResult
  });

  return {
    confirmNativeDesktopAction,
    host,
    sanitizeActionResult,
    invoke: async (channel, ...args) => {
      const handler = handlers.get(channel);
      if (!handler) {
        throw new Error(`Missing handler for ${channel}`);
      }
      return handler(ipcEvent(), ...args);
    }
  };
}

function createHost(overrides: Partial<BrowserHostIpcTarget> = {}): BrowserHostIpcTarget {
  return {
    getSnapshot: vi.fn(() => snapshot()),
    open: vi.fn(async (request) => actionResult({ sessionId: request?.sessionId })),
    show: vi.fn((sessionId) => actionResult({ sessionId })),
    hide: vi.fn(() => actionResult()),
    setBounds: vi.fn(() => actionResult()),
    pause: vi.fn((sessionId) => actionResult({ sessionId })),
    resume: vi.fn((sessionId) => actionResult({ sessionId })),
    takeover: vi.fn((sessionId) => actionResult({ sessionId })),
    release: vi.fn((sessionId) => actionResult({ sessionId })),
    stop: vi.fn(async (sessionId) => actionResult({ sessionId })),
    performAction: vi.fn(async (sessionId) => actionResult({ sessionId })),
    ...overrides
  };
}

function actionResult({ sessionId = "session_1" }: { sessionId?: string } = {}): BrowserHostActionResult {
  return {
    ok: true,
    snapshot: snapshot(sessionId)
  };
}

function snapshot(sessionId = "session_1"): BrowserHostSnapshot {
  return {
    sessions: [
      {
        id: sessionId,
        task_id: "task_1",
        current_url: "https://example.test/?token=secret-token",
        title: "Example",
        status: "idle",
        mode: "watch",
        created_at: "2026-01-01T00:00:00.000Z",
        updated_at: "2026-01-01T00:00:00.000Z",
        paused: false,
        takeover: false,
        last_observation: null
      }
    ],
    events: [],
    activeSessionId: sessionId,
    visible: true,
    hostAvailable: true
  };
}

function ipcEvent(sender: unknown = {}, url = "app://local/index.html"): IpcMainInvokeEvent {
  return {
    sender,
    senderFrame: { url }
  } as IpcMainInvokeEvent;
}
