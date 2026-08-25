import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { act, create } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { BrowserHostSnapshot, BrowserSession } from "../../shared/browserTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { BrowserActivityPanel } from "./BrowserActivityPanel";

const activeSession: BrowserSession = {
  id: "session_1",
  current_url: "https://example.test/",
  title: "Example",
  status: "idle",
  mode: "watch",
  created_at: "2026-07-10T00:00:00.000Z",
  updated_at: "2026-07-10T00:00:00.000Z",
  paused: false,
  takeover: false,
  last_observation: null
};

const hostSnapshot: BrowserHostSnapshot = {
  sessions: [activeSession],
  events: [],
  activeSessionId: activeSession.id,
  visible: true,
  hostAvailable: true
};

function renderedPanel(): string {
  return renderToStaticMarkup(
    createElement(BrowserActivityPanel, {
      api: {} as LengrvisApiClient,
      sessions: [],
      events: [],
      hostSnapshot,
      activeSessionId: activeSession.id,
      error: null,
      onSessionsChange: () => undefined,
      onEventsChange: () => undefined,
      onHostSnapshotChange: () => undefined,
      onActiveSessionChange: () => undefined,
      onErrorChange: () => undefined
    })
  );
}

describe("BrowserActivityPanel", () => {
  it("enables takeover now that the main process requires native confirmation", () => {
    const markup = renderedPanel();
    const buttonEnd = markup.indexOf(">接管</button>");
    const buttonStart = markup.lastIndexOf("<button", buttonEnd);
    const takeoverButton = buttonStart >= 0 && buttonEnd >= 0 ? markup.slice(buttonStart, buttonEnd + ">接管</button>".length) : "";

    expect(takeoverButton).not.toContain("disabled");
    expect(markup).not.toContain("当前版本尚未启用");
  });

  it("reports rejected host and backend refreshes instead of leaving stale state silently", async () => {
    const onErrorChange = vi.fn();
    const api = {
      subscribeBrowserHostSnapshots: vi.fn().mockReturnValue(() => undefined),
      getBrowserHostSnapshot: vi.fn().mockRejectedValue(new Error("浏览器 Host IPC 已断开")),
      listBrowserSessions: vi.fn().mockRejectedValue(new Error("浏览器会话 IPC 已断开")),
      hideBrowserHost: vi.fn().mockResolvedValue({ ok: true })
    } as unknown as LengrvisApiClient;

    await act(async () => {
      create(createElement(BrowserActivityPanel, {
        api,
        sessions: [],
        events: [],
        hostSnapshot: null,
        activeSessionId: null,
        error: null,
        onSessionsChange: vi.fn(),
        onEventsChange: vi.fn(),
        onHostSnapshotChange: vi.fn(),
        onActiveSessionChange: vi.fn(),
        onErrorChange
      }));
    });

    expect(onErrorChange).toHaveBeenCalledWith("内置浏览器状态读取失败；浏览器会话列表读取失败");
    expect(onErrorChange.mock.calls.flat().join(" ")).not.toContain("IPC 已断开");
  });

  it("does not render raw bridge response errors", async () => {
    const onErrorChange = vi.fn();
    const api = {
      subscribeBrowserHostSnapshots: vi.fn().mockReturnValue(() => undefined),
      getBrowserHostSnapshot: vi.fn().mockResolvedValue(hostSnapshot),
      listBrowserSessions: vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        error: { message: "C:\\private\\browser-host.log" }
      }),
      hideBrowserHost: vi.fn().mockResolvedValue({ ok: true })
    } as unknown as LengrvisApiClient;

    await act(async () => {
      create(createElement(BrowserActivityPanel, {
        api,
        sessions: [],
        events: [],
        hostSnapshot: null,
        activeSessionId: null,
        error: null,
        onSessionsChange: vi.fn(),
        onEventsChange: vi.fn(),
        onHostSnapshotChange: vi.fn(),
        onActiveSessionChange: vi.fn(),
        onErrorChange
      }));
    });

    expect(onErrorChange).toHaveBeenCalledWith("浏览器会话列表读取失败");
    expect(onErrorChange.mock.calls.flat().join(" ")).not.toContain("browser-host.log");
  });
});
