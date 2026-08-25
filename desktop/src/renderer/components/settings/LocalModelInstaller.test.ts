import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../../shared/desktopBridgeTypes";
import type { LocalModelSetupPlan } from "../../../shared/localModelTypes";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { LocalModelInstaller } from "./LocalModelInstaller";

describe("LocalModelInstaller async recovery", () => {
  afterEach(() => {
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("settles an IPC-rejected install and lets the user retry", async () => {
    (window as unknown as { lengrvis?: { realtime: object } }).lengrvis = { realtime: {} };
    const setupPlan = readySetupPlan();
    const api = {
      getLocalModelSetupPlan: vi.fn().mockResolvedValue(success(setupPlan)),
      installLocalModel: vi.fn().mockRejectedValue(new Error("本地模型安装 IPC 已断开"))
    } as unknown as LengrvisApiClient;
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(LocalModelInstaller, {
        api,
        apiBaseUrl: "http://127.0.0.1:8000",
        readiness: setupPlan.readiness,
        health: null,
        setupPlan,
        mode: "privacy"
      }));
    });

    const installButton = renderer.root
      .findAllByType("button")
      .find((button) => textContentNode(button).includes("安装所选模型"));
    expect(installButton).toBeDefined();

    await act(async () => {
      installButton?.props.onClick();
      await Promise.resolve();
    });

    expect(textContent(renderer)).toContain("本地模型安装 IPC 已断开");
    expect(installButton?.props.disabled).toBe(false);
  });
});

function readySetupPlan(): LocalModelSetupPlan {
  return {
    ready: false,
    canInstall: true,
    model: "qwen2.5:3b",
    readiness: {
      canInstall: true,
      recommendedModel: "qwen2.5:3b",
      reason: "ready",
      checks: [],
      memoryTotalBytes: 16 * 1024 ** 3,
      diskFreeBytes: 64 * 1024 ** 3,
      cpuLogicalCores: 8
    },
    installed: true,
    running: true,
    models: [],
    hasModel: false,
    runtimeSource: "system",
    bundledRuntimeAvailable: false,
    bundledRuntimePath: "",
    bundledModelsAvailable: false,
    bundledModelsPath: "",
    bundledModelAvailable: false,
    bundledModelConfigured: false,
    bundleManifest: { present: false },
    steps: [],
    nextAction: "download_model",
    evidence: []
  };
}

function success<T>(data: T): ApiResponse<T> {
  return { ok: true, status: 200, data, receivedAt: "2026-07-15T00:00:00.000Z" };
}

function textContent(renderer: ReactTestRenderer): string {
  return textContentNode(renderer.root);
}

function textContentNode(node: { findAll: (predicate: () => boolean) => Array<{ children: unknown[] }> }): string {
  return node
    .findAll(() => true)
    .flatMap((child) => child.children)
    .filter((child): child is string => typeof child === "string")
    .join(" ");
}
