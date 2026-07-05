import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { BackendStatus } from "../../shared/desktopBridgeTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import {
  backendTaskSubmitUnavailableMessage,
  createFailedAssistantMessage,
  createLocalUserMessage,
  useTaskSubmission
} from "./useTaskSubmission";

function backendStatus(overrides: Partial<BackendStatus> = {}): BackendStatus {
  return {
    state: "stopped",
    baseUrl: "http://127.0.0.1:8000",
    lastCheckedAt: "2026-07-05T00:00:00.000Z",
    ...overrides
  };
}

describe("task submission view model", () => {
  it("creates local user and failed assistant chat messages with stable roles", () => {
    expect(createLocalUserMessage("打开下载目录", "local-user-1", "now")).toEqual({
      id: "local-user-1",
      role: "user",
      author: "你",
      content: "打开下载目录",
      createdAt: "now",
      status: "sent"
    });

    expect(createFailedAssistantMessage("服务还没连上", "local-assistant-1", "later")).toEqual({
      id: "local-assistant-1",
      role: "assistant",
      author: "Lengrvis",
      content: "服务还没连上",
      createdAt: "later",
      status: "failed"
    });
  });

  it("keeps backend readiness failure copy actionable", () => {
    expect(backendTaskSubmitUnavailableMessage(backendStatus({ message: "端口未响应" }))).toBe(
      "Lengrvis 服务还没连上：端口未响应。输入内容已保留，可以稍后重试。"
    );

    expect(backendTaskSubmitUnavailableMessage(backendStatus({ state: "running", health: { ok: false } }))).toBe(
      "Lengrvis 服务还没连上：健康检查还没通过。输入内容已保留，可以稍后重试。"
    );
  });
});

describe("useTaskSubmission", () => {
  it("exposes a clean initial submission surface", () => {
    function Harness() {
      const submission = useTaskSubmission({
        api: {} as LengrvisApiClient,
        mode: "efficiency",
        backendStatusRef: { current: backendStatus() },
        chatStartedTaskIds: { current: new Set<string>() },
        setMessages: () => undefined,
        setTasks: () => undefined,
        setFocusedTaskId: () => undefined,
        setBackendStatus: () => undefined,
        refreshTaskSnapshot: async () => undefined
      });

      return createElement("pre", null, JSON.stringify({
        draft: submission.draft,
        heroSubmitting: submission.heroSubmitting,
        heroSubmitError: submission.heroSubmitError
      }));
    }

    const markup = renderToStaticMarkup(createElement(Harness));

    expect(markup).toContain("&quot;draft&quot;:&quot;&quot;");
    expect(markup).toContain("&quot;heroSubmitting&quot;:false");
    expect(markup).toContain("&quot;heroSubmitError&quot;:null");
  });
});
