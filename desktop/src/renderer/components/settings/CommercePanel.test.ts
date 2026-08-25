import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";

import type { ApiResponse } from "../../../shared/desktopBridgeTypes";
import type {
  CommerceLicenseStatus,
  CommercePlanStatus,
  CommerceQuotaStatus
} from "../../../shared/commerceTypes";
import type { LengrvisApiClient } from "../../lib/apiClient";
import { CommercePanel } from "./CommercePanel";

describe("CommercePanel cloud quota status", () => {
  it("shows that cloud calls are paused when metering is unavailable", async () => {
    const api = {
      getCommercePlan: vi.fn().mockResolvedValue(success(planStatus())),
      getCommerceLicense: vi.fn().mockResolvedValue(success(licenseStatus())),
      getCommerceQuota: vi.fn().mockResolvedValue(success(unavailableQuota()))
    } as unknown as LengrvisApiClient;
    let renderer!: ReactTestRenderer;

    await act(async () => {
      renderer = create(createElement(CommercePanel, { api }));
    });

    const content = textContent(renderer);
    expect(content).toContain("计量不可用，云调用已暂停");
    expect(content).not.toContain("0 / 5,000,000 令牌");
  });
});

function planStatus(): CommercePlanStatus {
  return {
    plan: "free",
    monthlyPriceCny: 0,
    modelTier: "standard",
    remoteDesktopEnabled: false,
    features: {
      local_read_only: true,
      basic_tasks: true,
      cloud_quota: true,
      document_ai: false,
      scheduling: false,
      remote_view: false,
      remote_control: false,
      audit_export: false,
      policy_management: false,
      private_deployment: false,
      advanced_models: false
    },
    highRiskFeatures: []
  };
}

function licenseStatus(): CommerceLicenseStatus {
  return {
    state: "absent",
    present: false,
    active: false,
    expired: false,
    verifierConfigured: true
  };
}

function unavailableQuota(): CommerceQuotaStatus {
  const limits = { totalTokens: 5_000_000, calls: null, totalCostUsd: null };
  return {
    plan: "free",
    enforced: true,
    unlimited: false,
    available: false,
    state: "metering_unavailable",
    windowHours: 5,
    limits,
    usage: null,
    exceeded: ["usage_unavailable"],
    windows: [
      {
        key: "5h",
        windowHours: 5,
        available: false,
        limits,
        usage: null,
        exceeded: ["usage_unavailable"]
      }
    ]
  };
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
