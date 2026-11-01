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
  it.each([true, false])("opens the reply only after a successful home submission: %s", async (succeeds) => {
    Object.assign(window, { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout });
    const connected = backendStatus({ state: "running", health: { ok: true } });
    const sendChat = succeeds
      ? vi.fn().mockResolvedValue({ ok: true, data: { message: {
        id: "reply", role: "assistant", author: "Lengrvis", content: "已收到任务", createdAt: "now", status: "sent"
      } } })
      : vi.fn().mockRejectedValue(new Error("连接中断"));
    const api = { getBackendStatus: vi.fn().mockResolvedValue(connected), sendChat } as unknown as LengrvisApiClient;
    const onHeroSubmitted = vi.fn();
    let actions!: TaskSubmissionActions;
    function Harness() {
      actions = useTaskSubmission({
        api, mode: "efficiency", backendStatusRef: { current: connected },
        chatStartedTaskIds: { current: new Set<string>() },
        setMessages: () => undefined, setTasks: () => undefined,
        setFocusedTaskId: () => undefined, setBackendStatus: () => undefined,
        refreshTaskSnapshot: async () => undefined, onHeroSubmitted
      });
      return null;
    }
    let renderer!: ReturnType<typeof create>;
    await act(async () => { renderer = create(createElement(Harness)); });
    act(() => { actions.setDraft("整理项目资料"); });
    await act(async () => { await actions.submitHeroPrompt(); });
    expect(onHeroSubmitted).toHaveBeenCalledTimes(succeeds ? 1 : 0);
    expect(actions.draft).toBe(succeeds ? "" : "整理项目资料");
    expect(actions.heroSubmitting).toBe(false);
    expect(actions.heroSubmitError).toBe(succeeds ? null : "连接中断");
    // A failed request must release both locks so a deliberate retry can proceed.
    if (!succeeds) {
      await act(async () => { await actions.submitHeroPrompt(); });
      expect(sendChat).toHaveBeenCalledTimes(2);
    }
    act(() => { actions.setDraft(""); renderer.unmount(); });
  });

  it("blocks duplicate requests across entry points and preserves a newer home draft", async () => {
    Object.assign(window, { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout });
    const connected = backendStatus({ state: "running", health: { ok: true } });
    let complete!: (result: Awaited<ReturnType<LengrvisApiClient["sendChat"]>>) => void;
    const sendChat = vi.fn(() => new Promise<Awaited<ReturnType<LengrvisApiClient["sendChat"]>>>((resolve) => { complete = resolve; }));
    const api = { getBackendStatus: vi.fn().mockResolvedValue(connected), sendChat } as unknown as LengrvisApiClient;
    let actions!: TaskSubmissionActions;
    function Harness() {
      actions = useTaskSubmission({
        api, mode: "efficiency", backendStatusRef: { current: connected },
        chatStartedTaskIds: { current: new Set<string>() },
        setMessages: () => undefined, setTasks: () => undefined,
        setFocusedTaskId: () => undefined, setBackendStatus: () => undefined,
        refreshTaskSnapshot: async () => undefined
      });
      return null;
    }
    let renderer!: ReturnType<typeof create>;
    await act(async () => { renderer = create(createElement(Harness)); });
    act(() => { actions.setDraft("寻找文档"); });
    let pending!: Promise<void>;
    await act(async () => { pending = actions.submitHeroPrompt(); });
    expect(actions.heroSubmitting).toBe(true);
    expect(await actions.sendMessage("寻找文档")).toMatchObject({ ok: false });
    expect(sendChat).toHaveBeenCalledOnce();
    act(() => { actions.setDraft("用户重新选择的任务"); });
    await act(async () => {
      complete({
        ok: true, status: 200, receivedAt: "now", data: {
          message: { id: "assistant", role: "assistant", author: "Lengrvis", content: "已收到", createdAt: "now", status: "sent" }
        }
      });
      await pending;
    });
    expect(actions.heroSubmitting).toBe(false);
    expect(actions.draft).toBe("用户重新选择的任务");
    act(() => { actions.setDraft(""); renderer.unmount(); });
  });

  it.each([false, true])("uses persisted Butler IDs when workspace history arrives first: %s", async (historyFirst) => {
    Object.assign(window, { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout });
    const connected = backendStatus({ state: "running", health: { ok: true } });
    const user = createLocalUserMessage("你好", "chat-user", "2026-10-09T01:00:00Z");
    const assistant: ChatMessage = {
      id: "chat-assistant", role: "assistant", author: "Lengrvis", content: "我在。",
      createdAt: "2026-10-09T01:00:01Z", status: "sent"
    };
    let messages: ChatMessage[] = [];
    let actions!: TaskSubmissionActions;
    const sendChat = vi.fn();
    const api = { getBackendStatus: vi.fn().mockResolvedValue(connected), sendChat } as unknown as LengrvisApiClient;
    function Harness() {
      actions = useTaskSubmission({
        api, mode: "efficiency", backendStatusRef: { current: connected },
        chatStartedTaskIds: { current: new Set<string>() },
        setMessages: (update) => { messages = typeof update === "function" ? update(messages) : update; },
        setTasks: () => undefined, setFocusedTaskId: () => undefined, setBackendStatus: () => undefined,
        refreshTaskSnapshot: async () => undefined,
        submitButlerMessage: async () => {
          if (historyFirst) messages = [user, assistant];
          return {
            ok: true, status: 200, receivedAt: assistant.createdAt,
            data: { reply: "我在。", user_message: user, assistant_message: assistant }
          };
        }
      });
      return null;
    }
    await act(async () => { create(createElement(Harness)); });
    await act(async () => { expect(await actions.sendMessage("你好")).toEqual({ ok: true }); });
    expect(messages).toEqual([user, assistant]);
    expect(sendChat).not.toHaveBeenCalled();
    // Backend history restores the same rows, without local-only identities.
    messages = [user, assistant];
    expect(messages.map((message) => message.id)).toEqual(["chat-user", "chat-assistant"]);
  });

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
