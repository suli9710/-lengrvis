import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../shared/desktopBridgeTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { SchedulePanel } from "./SchedulePanel";

describe("SchedulePanel", () => {
  it("shows loading and load failure without presenting the result as an empty schedule", async () => {
    const pending = deferred<ApiResponse<unknown[]>>();
    const api = scheduleApi({ listSchedules: vi.fn(() => pending.promise) });
    let renderer!: ReactTestRenderer;

    act(() => {
      renderer = create(createElement(SchedulePanel, { api }));
    });

    expect(textContent(renderer)).toContain("正在读取定时任务");
    expect(textContent(renderer)).not.toContain("还没有定时任务");

    await act(async () => {
      pending.resolve(failure("无法连接定时任务服务"));
      await pending.promise;
    });

    expect(textContent(renderer)).toContain("无法连接定时任务服务");
    expect(textContent(renderer)).not.toContain("还没有定时任务");
    expect(renderer.root.findByProps({ role: "alert" })).toBeTruthy();
  });

  it("blocks duplicate toggles while pending and reports mutation failure", async () => {
    const togglePending = deferred<ApiResponse<unknown>>();
    const listSchedules = vi.fn().mockResolvedValue(success([schedule("schedule-1", true)]));
    const enableSchedule = vi.fn(() => togglePending.promise);
    const api = scheduleApi({ listSchedules, enableSchedule });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(SchedulePanel, { api }));
    });

    const toggleButton = renderer.root.findByProps({ "aria-label": "暂停定时任务：整理下载目录" });
    expect(toggleButton.props["aria-pressed"]).toBeUndefined();
    act(() => {
      toggleButton.props.onClick();
      toggleButton.props.onClick();
    });

    expect(enableSchedule).toHaveBeenCalledTimes(1);
    expect(renderer.root.findByProps({ "aria-label": "暂停定时任务：整理下载目录" }).props.disabled).toBe(true);
    expect(textContent(renderer)).toContain("正在暂停");

    await act(async () => {
      togglePending.resolve(failure("暂停任务失败"));
      await togglePending.promise;
    });

    expect(textContent(renderer)).toContain("暂停任务失败");
    expect(listSchedules).toHaveBeenCalledTimes(1);
  });

  it("refreshes the list after a successful create", async () => {
    const created = schedule("schedule-2", true, "每日生成工作摘要");
    const listSchedules = vi.fn().mockResolvedValueOnce(success([])).mockResolvedValueOnce(success([created]));
    const createSchedule = vi.fn().mockResolvedValue(success(created));
    const api = scheduleApi({ listSchedules, createSchedule });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(SchedulePanel, { api }));
    });

    act(() => {
      renderer.root.findByProps({ "aria-label": "任务目标" }).props.onChange({
        currentTarget: { value: "每日生成工作摘要" },
        target: { value: "每日生成工作摘要" }
      });
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "创建定时任务" }).props.onClick();
    });

    expect(createSchedule).toHaveBeenCalledTimes(1);
    expect(listSchedules).toHaveBeenCalledTimes(2);
    expect(textContent(renderer)).toContain("每日生成工作摘要");
    expect(renderer.root.findByProps({ "aria-label": "任务目标" }).props.value).toBe("");
  });

  it("keeps a schedule visible when deletion fails", async () => {
    const deleteSchedule = vi.fn().mockResolvedValue(failure("删除任务失败"));
    const listSchedules = vi.fn().mockResolvedValue(success([schedule("schedule-3", false, "保留这项任务")]));
    const api = scheduleApi({ listSchedules, deleteSchedule });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(SchedulePanel, { api }));
    });
    await act(async () => {
      renderer.root.findByProps({ "aria-label": "删除定时任务：保留这项任务" }).props.onClick();
    });

    expect(deleteSchedule).toHaveBeenCalledWith("schedule-3");
    expect(listSchedules).toHaveBeenCalledTimes(1);
    expect(textContent(renderer)).toContain("删除任务失败");
    expect(textContent(renderer)).toContain("保留这项任务");
  });
});

function scheduleApi(overrides: Partial<Record<"listSchedules" | "createSchedule" | "deleteSchedule" | "enableSchedule", unknown>>) {
  return {
    listSchedules: vi.fn().mockResolvedValue(success([])),
    createSchedule: vi.fn().mockResolvedValue(success({})),
    deleteSchedule: vi.fn().mockResolvedValue(success({ ok: true })),
    enableSchedule: vi.fn().mockResolvedValue(success({})),
    ...overrides
  } as unknown as LengrvisApiClient;
}

function schedule(id: string, enabled: boolean, goal = "整理下载目录") {
  return {
    id,
    enabled,
    goal,
    cron: "*/30 * * * *",
    mode: "privacy",
    next_run_at: "2026-07-12T01:00:00.000Z"
  };
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
