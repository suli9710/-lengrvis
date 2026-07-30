import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../shared/desktopBridgeTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { MemoryPanel } from "./MemoryPanel";

describe("MemoryPanel", () => {
  it("shows loading and load failure without presenting the result as an empty library", async () => {
    const pending = deferred<ApiResponse<unknown[]>>();
    const api = memoryApi({ listMemories: vi.fn(() => pending.promise) });
    let renderer!: ReactTestRenderer;

    act(() => {
      renderer = create(createElement(MemoryPanel, { api }));
    });

    expect(textContent(renderer)).toContain("正在读取记忆");
    expect(textContent(renderer)).not.toContain("还没有记忆");

    await act(async () => {
      pending.resolve(failure("无法连接记忆服务"));
      await pending.promise;
    });

    expect(textContent(renderer)).toContain("无法连接记忆服务");
    expect(textContent(renderer)).not.toContain("还没有记忆");
    expect(renderer.root.findByProps({ role: "alert" })).toBeTruthy();
  });

  it("blocks duplicate saves while pending and keeps the draft on mutation failure", async () => {
    const savePending = deferred<ApiResponse<unknown>>();
    const listMemories = vi.fn().mockResolvedValue(success([memory("mem-1", "偏好月度归档")]));
    const saveMemory = vi.fn(() => savePending.promise);
    const api = memoryApi({ listMemories, saveMemory });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });

    const contentInput = renderer.root.findByProps({ "aria-label": "记忆内容" });
    act(() => {
      contentInput.props.onChange({ currentTarget: { value: "保留这条草稿" }, target: { value: "保留这条草稿" } });
    });

    const saveButton = renderer.root.findByProps({ "aria-label": "保存记忆" });
    act(() => {
      saveButton.props.onClick();
      saveButton.props.onClick();
    });

    expect(saveMemory).toHaveBeenCalledTimes(1);
    expect(renderer.root.findByProps({ "aria-label": "保存记忆" }).props.disabled).toBe(true);
    expect(renderer.root.findByProps({ "aria-label": "保存记忆" }).props["aria-busy"]).toBe(true);
    expect(textContent(renderer)).toContain("正在保存");

    await act(async () => {
      savePending.resolve(failure("保存记忆失败"));
      await savePending.promise;
    });

    expect(textContent(renderer)).toContain("保存记忆失败");
    expect(renderer.root.findByProps({ "aria-label": "记忆内容" }).props.value).toBe("保留这条草稿");
    expect(listMemories).toHaveBeenCalledTimes(1);
  });

  it("refreshes the list after a successful save", async () => {
    const listMemories = vi
      .fn()
      .mockResolvedValueOnce(success([]))
      .mockResolvedValueOnce(success([memory("mem-2", "新记忆已经出现")]));
    const saveMemory = vi.fn().mockResolvedValue(success(memory("mem-2", "新记忆已经出现")));
    const api = memoryApi({ listMemories, saveMemory });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });

    act(() => {
      renderer.root.findByProps({ "aria-label": "记忆内容" }).props.onChange({
        currentTarget: { value: "新记忆已经出现" },
        target: { value: "新记忆已经出现" }
      });
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "保存记忆" }).props.onClick();
    });

    expect(saveMemory).toHaveBeenCalledTimes(1);
    expect(listMemories).toHaveBeenCalledTimes(2);
    expect(textContent(renderer)).toContain("新记忆已经出现");
    expect(renderer.root.findByProps({ "aria-label": "记忆内容" }).props.value).toBe("");
  });

  it("keeps the last good results when recall fails", async () => {
    const recallMemory = vi.fn().mockResolvedValue(failure("搜索服务暂时不可用"));
    const api = memoryApi({
      listMemories: vi.fn().mockResolvedValue(success([memory("mem-3", "不会被搜索失败抹掉")])),
      recallMemory
    });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    act(() => {
      renderer.root.findByProps({ "aria-label": "搜索记忆内容" }).props.onChange({
        currentTarget: { value: "归档" },
        target: { value: "归档" }
      });
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "执行记忆搜索" }).props.onClick();
    });

    expect(recallMemory).toHaveBeenCalledTimes(1);
    expect(textContent(renderer)).toContain("搜索服务暂时不可用");
    expect(textContent(renderer)).toContain("不会被搜索失败抹掉");
    expect(textContent(renderer)).not.toContain("还没有记忆");
  });

  it("shows a no-match state after a successful non-empty search", async () => {
    const recallMemory = vi.fn().mockResolvedValue(success([]));
    const api = memoryApi({ listMemories: vi.fn().mockResolvedValue(success([])), recallMemory });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    act(() => {
      renderer.root.findByProps({ "aria-label": "搜索记忆内容" }).props.onChange({
        currentTarget: { value: "不存在的内容" },
        target: { value: "不存在的内容" }
      });
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "执行记忆搜索" }).props.onClick();
    });

    expect(recallMemory).toHaveBeenCalledWith("不存在的内容", { k: 10 });
    expect(textContent(renderer)).toContain("没有找到匹配的记忆");
    expect(textContent(renderer)).not.toContain("还没有记忆");
  });

  it("ignores composing Enter and locks the query while a search is pending", async () => {
    const pending = deferred<ApiResponse<unknown[]>>();
    const recallMemory = vi.fn(() => pending.promise);
    const api = memoryApi({ listMemories: vi.fn().mockResolvedValue(success([])), recallMemory });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    const input = renderer.root.findByProps({ "aria-label": "搜索记忆内容" });
    act(() => {
      input.props.onChange({ currentTarget: { value: "中文输入" }, target: { value: "中文输入" } });
      input.props.onKeyDown({ key: "Enter", nativeEvent: { isComposing: true } });
    });
    expect(recallMemory).not.toHaveBeenCalled();

    act(() => {
      renderer.root.findByProps({ "aria-label": "执行记忆搜索" }).props.onClick();
    });
    expect(recallMemory).toHaveBeenCalledTimes(1);
    expect(renderer.root.findByProps({ "aria-label": "搜索记忆内容" }).props.disabled).toBe(true);

    await act(async () => {
      pending.resolve(success([]));
      await pending.promise;
    });
  });

  it("keeps a memory visible when forgetting it fails", async () => {
    const forgetMemory = vi.fn().mockResolvedValue(failure("删除记忆失败"));
    const listMemories = vi.fn().mockResolvedValue(success([memory("mem-4", "仍然保留的记忆")]));
    const api = memoryApi({ listMemories, forgetMemory });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "忘记记忆：仍然保留的记忆" }).props.onClick();
    });

    expect(forgetMemory).toHaveBeenCalledWith("mem-4");
    expect(listMemories).toHaveBeenCalledTimes(1);
    expect(textContent(renderer)).toContain("删除记忆失败");
    expect(textContent(renderer)).toContain("仍然保留的记忆");
  });

  it("shows lifecycle/version/conflict state and resolves a conflict before promotion", async () => {
    const conflicting = {
      ...memory("mem-conflict", "存在冲突的偏好"),
      state: "quarantined",
      version: 3,
      conflict_status: "conflicting",
      user_confirmed: false
    };
    const promoteMemory = vi.fn().mockResolvedValue(success({
      ...conflicting,
      state: "active",
      conflict_status: "resolved",
      user_confirmed: true
    }));
    const listMemories = vi
      .fn()
      .mockResolvedValueOnce(success([conflicting]))
      .mockResolvedValueOnce(success([{ ...conflicting, state: "active", conflict_status: "resolved", user_confirmed: true }]));
    const api = memoryApi({ listMemories, promoteMemory });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });

    expect(textContent(renderer)).toContain("待审阅");
    expect(textContent(renderer)).toContain("v3");
    expect(textContent(renderer)).toContain("存在冲突");
    expect(textContent(renderer)).toContain("未人工确认");

    await act(async () => {
      renderer.root.findByProps({ "aria-label": "审核并启用记忆：存在冲突的偏好" }).props.onClick();
    });

    expect(promoteMemory).toHaveBeenCalledWith("mem-conflict", {
      reviewedBy: "desktop-user",
      resolveConflict: true
    });
    expect(textContent(renderer)).toContain("已启用");
    expect(textContent(renderer)).toContain("冲突已解决");
  });

  it("revokes an active memory through the explicit review action", async () => {
    const active = { ...memory("mem-active", "不再使用的偏好"), state: "active" };
    const revokeMemory = vi.fn().mockResolvedValue(success({ ...active, state: "revoked" }));
    const listMemories = vi
      .fn()
      .mockResolvedValueOnce(success([active]))
      .mockResolvedValueOnce(success([{ ...active, state: "revoked" }]));
    const api = memoryApi({ listMemories, revokeMemory });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "撤销记忆：不再使用的偏好" }).props.onClick();
    });

    expect(revokeMemory).toHaveBeenCalledWith("mem-active", { reviewedBy: "desktop-user" });
    expect(textContent(renderer)).toContain("已撤销");
  });

  it("ignores an older recall response after a newer refresh succeeds", async () => {
    const staleRecall = deferred<ApiResponse<unknown[]>>();
    const listMemories = vi
      .fn()
      .mockResolvedValueOnce(success([memory("mem-old", "刷新前的记忆")]))
      .mockResolvedValueOnce(success([memory("mem-new", "刷新后的最新记忆")]));
    const api = memoryApi({
      listMemories,
      recallMemory: vi.fn(() => staleRecall.promise),
      saveMemory: vi.fn().mockResolvedValue(success(memory("mem-new", "刷新后的最新记忆")))
    });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    act(() => {
      renderer.root.findByProps({ "aria-label": "搜索记忆内容" }).props.onChange({
        currentTarget: { value: "旧查询" },
        target: { value: "旧查询" }
      });
      renderer.root.findByProps({ "aria-label": "记忆内容" }).props.onChange({
        currentTarget: { value: "触发新刷新" },
        target: { value: "触发新刷新" }
      });
    });
    act(() => {
      renderer.root.findByProps({ "aria-label": "执行记忆搜索" }).props.onClick();
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "保存记忆" }).props.onClick();
    });
    await act(async () => {
      staleRecall.resolve(success([memory("mem-stale", "迟到的旧搜索结果")]));
      await staleRecall.promise;
    });

    expect(textContent(renderer)).toContain("刷新后的最新记忆");
    expect(textContent(renderer)).not.toContain("迟到的旧搜索结果");
    expect(listMemories).toHaveBeenCalledTimes(2);
    expect(renderer.root.findByProps({ "aria-label": "搜索记忆内容" }).props.value).toBe("");
  });

  it("clears a failed search query when retry returns the full library", async () => {
    const listMemories = vi
      .fn()
      .mockResolvedValueOnce(success([memory("mem-old", "原有记忆")]))
      .mockResolvedValueOnce(success([memory("mem-new", "重试后的完整记忆")]));
    const api = memoryApi({
      listMemories,
      recallMemory: vi.fn().mockResolvedValue(failure("搜索暂时失败"))
    });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    act(() => {
      renderer.root.findByProps({ "aria-label": "搜索记忆内容" }).props.onChange({
        currentTarget: { value: "旧条件" },
        target: { value: "旧条件" }
      });
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "执行记忆搜索" }).props.onClick();
    });
    await act(async () => {
      renderer.root.findByProps({ role: "alert" }).findByType("button").props.onClick();
    });

    expect(textContent(renderer)).toContain("重试后的完整记忆");
    expect(renderer.root.findByProps({ "aria-label": "搜索记忆内容" }).props.value).toBe("");
  });

  it("does not refresh or update panel state after unmounting during a mutation", async () => {
    const savePending = deferred<ApiResponse<unknown>>();
    const listMemories = vi.fn().mockResolvedValue(success([]));
    const api = memoryApi({ listMemories, saveMemory: vi.fn(() => savePending.promise) });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(MemoryPanel, { api }));
    });
    act(() => {
      renderer.root.findByProps({ "aria-label": "记忆内容" }).props.onChange({
        currentTarget: { value: "卸载中的写入" },
        target: { value: "卸载中的写入" }
      });
      renderer.root.findByProps({ "aria-label": "保存记忆" }).props.onClick();
    });
    act(() => renderer.unmount());
    await act(async () => {
      savePending.resolve(success({}));
      await savePending.promise;
    });

    expect(listMemories).toHaveBeenCalledTimes(1);
  });
});

function memoryApi(overrides: Partial<Record<
  "listMemories" | "recallMemory" | "saveMemory" | "promoteMemory" | "revokeMemory" | "forgetMemory",
  unknown
>>) {
  return {
    listMemories: vi.fn().mockResolvedValue(success([])),
    recallMemory: vi.fn().mockResolvedValue(success([])),
    saveMemory: vi.fn().mockResolvedValue(success({})),
    promoteMemory: vi.fn().mockResolvedValue(success({})),
    revokeMemory: vi.fn().mockResolvedValue(success({})),
    forgetMemory: vi.fn().mockResolvedValue(success({ ok: true })),
    ...overrides
  } as unknown as LengrvisApiClient;
}

function memory(id: string, content: string) {
  return { id, content, kind: "fact", tags: ["test"], created_at: "2026-07-12T00:00:00.000Z" };
}

function success<T>(data: T): ApiResponse<T> {
  return { ok: true, status: 200, data, receivedAt: "2026-07-12T00:00:00.000Z" };
}

function failure(message: string): ApiResponse<never> {
  return { ok: false, status: 503, error: { message }, receivedAt: "2026-07-12T00:00:00.000Z" };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
