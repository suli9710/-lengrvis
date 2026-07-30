import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../shared/desktopBridgeTypes";
import type { LocalModelSetupPlan } from "../../shared/localModelTypes";
import type { LengrvisApiClient } from "../lib/apiClient";
import { defaultSettings, disconnectedStatus } from "../store/defaults";
import { SettingsPanel } from "./SettingsPanel";

vi.mock("./settings/CommercePanel", () => ({ CommercePanel: () => null }));
vi.mock("./settings/PrivacyDataPanel", () => ({ PrivacyDataPanel: () => null }));
vi.mock("./settings/AppearanceSettingsSection", () => ({ AppearanceSettingsSection: () => null }));
vi.mock("./settings/HardwareAccelerationCard", async () => {
  const { createElement: createMockElement } = await import("react");
  return {
    HardwareAccelerationCard: ({ error, loading }: { error: string; loading: boolean }) =>
      createMockElement("div", null, loading ? "正在检测硬件加速" : error)
  };
});

describe("SettingsPanel asynchronous probes", () => {
  it("renders IPC probe failures and settles loading state", async () => {
    const onLocalLlmHealthChange = vi.fn();
    const api = settingsApi({
      getLocalLlmHealth: vi.fn().mockRejectedValue(new Error("本地 AI IPC 已断开")),
      getHardwareAccelerationStatus: vi.fn().mockRejectedValue(new Error("硬件探测 IPC 已断开")),
      listMobileDevices: vi.fn().mockRejectedValue(new Error("手机列表 IPC 已断开")),
      request: vi.fn().mockRejectedValue(new Error("权限策略 IPC 已断开"))
    });
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(SettingsPanel, {
        settings: { ...defaultSettings, mode: "privacy" },
        backendStatus: disconnectedStatus,
        localLlmHealth: null,
        llmHealth: null,
        llmCostSummary: null,
        onSave: vi.fn(),
        onLocalLlmHealthChange,
        onStartBackend: vi.fn(),
        onStopBackend: vi.fn(),
        api
      }));
    });

    const content = textContent(renderer);
    expect(content).toContain("硬件探测 IPC 已断开");
    expect(content).toContain("手机列表 IPC 已断开");
    expect(content).toContain("权限策略 IPC 已断开");
    expect(content).not.toContain("正在检测硬件加速");
    expect(onLocalLlmHealthChange).toHaveBeenCalledWith(expect.objectContaining({
      available: false,
      error: "本地 AI IPC 已断开"
    }));
  });
});

function settingsApi(overrides: Partial<Record<string, unknown>>) {
  return {
    getLocalModelSetupPlan: vi.fn().mockResolvedValue(success<LocalModelSetupPlan | null>(null)),
    getLocalLlmHealth: vi.fn().mockResolvedValue(success(null)),
    getHardwareAccelerationStatus: vi.fn().mockResolvedValue(success(null)),
    listMobileDevices: vi.fn().mockResolvedValue(success({ devices: [] })),
    request: vi.fn().mockResolvedValue(success({ mode: "default", rules: [] })),
    ...overrides
  } as unknown as LengrvisApiClient;
}

function success<T>(data: T): ApiResponse<T> {
  return { ok: true, status: 200, data, receivedAt: "2026-07-12T00:00:00.000Z" };
}

function textContent(renderer: ReactTestRenderer): string {
  return renderer.root
    .findAll(() => true)
    .flatMap((node) => node.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
