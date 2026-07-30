import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { act, create } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { BackendStatus } from "../../shared/desktopBridgeTypes";
import type { ChatMessage } from "../../shared/catalogTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import {
  backendTaskSubmitUnavailableMessage,
  createFailedAssistantMessage,
  createLocalUserMessage,
  type TaskSubmissionActions,
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

  it("adds a failed assistant reply when suggestion launch IPC rejects", async () => {
    Object.assign(window, {
      setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout
    });
    const connected = backendStatus({ state: "running", health: { ok: true } });
    const api = {
      getBackendStatus: vi.fn().mockResolvedValue(connected),
      launchPerceptionSuggestion: vi.fn().mockRejectedValue(new Error("建议启动 IPC 已断开"))
    } as unknown as LengrvisApiClient;
    const messageUpdates: Array<ChatMessage[] | ((current: ChatMessage[]) => ChatMessage[])> = [];
    let actions!: TaskSubmissionActions;

    function Harness() {
      actions = useTaskSubmission({
        api,
        mode: "efficiency",
        backendStatusRef: { current: connected },
        chatStartedTaskIds: { current: new Set<string>() },
        setMessages: (value) => messageUpdates.push(value),
        setTasks: () => undefined,
        setFocusedTaskId: () => undefined,
        setBackendStatus: () => undefined,
        refreshTaskSnapshot: async () => undefined
      });
      return null;
    }

    await act(async () => {
      create(createElement(Harness));
    });
    await act(async () => {
      await actions.executeSuggestion({
        id: "downloads/cleanup",
        title: "清理下载目录",
        prompt: "检查下载目录",
        confidence: 0.9
      });
    });

    const messages = messageUpdates.reduce<ChatMessage[]>(
      (current, update) => typeof update === "function" ? update(current) : update,
      []
    );
    expect(api.launchPerceptionSuggestion).toHaveBeenCalledOnce();
    expect(messageUpdates).toHaveLength(2);
    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({ role: "user", content: "检查下载目录" });
    expect(messages[1]).toMatchObject({ role: "assistant", status: "failed", content: "建议启动 IPC 已断开" });
  });
});
