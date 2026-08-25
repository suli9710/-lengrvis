import { describe, expect, it } from "vitest";

import {
  mapCommerceLicenseStatus,
  mapCommercePlanStatus,
  mapCommerceQuotaStatus,
  normalizeCommercePlan
} from "./commerceMappers";

describe("commerce mappers", () => {
  it("normalizes legacy max and team plans to pro", () => {
    expect(normalizeCommercePlan("plus")).toBe("plus");
    expect(normalizeCommercePlan("max")).toBe("pro");
    expect(
      mapCommercePlanStatus({
        plan: "team",
        monthly_price_cny: 129,
        model_tier: "advanced",
        remote_desktop_enabled: 1 as unknown as boolean,
        features: { remote_control: true },
        high_risk_features: ["remote_control"]
      })
    ).toEqual({
      plan: "pro",
      monthlyPriceCny: 129,
      modelTier: "advanced",
      remoteDesktopEnabled: true,
      features: { remote_control: true },
      highRiskFeatures: ["remote_control"]
    });
  });

  it("maps license metadata without leaking null values", () => {
    expect(
      mapCommerceLicenseStatus({
        state: "active",
        present: true,
        active: true,
        expired: false,
        verifier_configured: true,
        managed_by: "file",
        requested_env_plan: "team",
        license_id: "lic_123",
        issuer: null,
        replaces: null,
        revocation_capable: true,
        plan: "pro",
        subject: "",
        seats: 2,
        subscription_id: "sub_123",
        subscription_status: "trialing",
        renews_at: null,
        cancel_at_period_end: true,
        device_id: "device_1",
        order_ref: null,
        issued_at: "2026-01-01T00:00:00Z",
        expires_at: null,
        error_code: undefined
      })
    ).toMatchObject({
      state: "active",
      present: true,
      active: true,
      requestedEnvPlan: "pro",
      licenseId: "lic_123",
      issuer: undefined,
      plan: "pro",
      subject: undefined,
      seats: 2,
      subscriptionId: "sub_123",
      subscriptionStatus: "trialing",
      cancelAtPeriodEnd: true,
      deviceId: "device_1",
      issuedAt: "2026-01-01T00:00:00Z"
    });
  });

  it("maps quota windows and fallback windows", () => {
    const withWindows = mapCommerceQuotaStatus({
      plan: "team",
      enforced: true,
      unlimited: false,
      available: true,
      state: "available",
      window_hours: 24,
      limits: { total_tokens: 1000, calls: 50, total_cost_usd: 2 },
      usage: { calls: 5, total_tokens: 100, total_cost_usd: 0.25, window_hours: 24 },
      exceeded: ["calls"],
      windows: [
        {
          window_hours: 1,
          available: true,
          limits: { total_tokens: 100, calls: 10, total_cost_usd: 1 },
          usage: { calls: 2, total_tokens: 20, total_cost_usd: 0.1, window_hours: 1 },
          exceeded: undefined
        }
      ]
    });

    expect(withWindows).toMatchObject({
      plan: "pro",
      windowHours: 24,
      windows: [
        {
          key: "1h",
          windowHours: 1,
          limits: { totalTokens: 100, calls: 10, totalCostUsd: 1 },
          usage: { calls: 2, totalTokens: 20, totalCostUsd: 0.1, windowHours: 1 },
          exceeded: []
        }
      ]
    });

    const fallbackWindow = mapCommerceQuotaStatus({
      plan: "free",
      enforced: false,
      unlimited: true,
      available: true,
      state: "available",
      window_hours: 12,
      limits: { total_tokens: null, calls: null, total_cost_usd: null },
      usage: null,
      exceeded: []
    });

    expect(fallbackWindow.windows).toEqual([
      {
        key: "12h",
        windowHours: 12,
        available: true,
        limits: { totalTokens: null, calls: null, totalCostUsd: null },
        usage: null,
        exceeded: []
      }
    ]);
  });

  it("preserves unavailable metering instead of synthesizing zero usage", () => {
    const quota = mapCommerceQuotaStatus({
      plan: "free",
      enforced: true,
      unlimited: false,
      available: false,
      state: "metering_unavailable",
      window_hours: 5,
      limits: { total_tokens: 5_000_000, calls: null, total_cost_usd: null },
      usage: null,
      exceeded: ["usage_unavailable"],
      windows: [
        {
          key: "5h",
          window_hours: 5,
          available: false,
          limits: { total_tokens: 5_000_000, calls: null, total_cost_usd: null },
          usage: null,
          exceeded: ["usage_unavailable"]
        }
      ]
    });

    expect(quota).toMatchObject({
      available: false,
      state: "metering_unavailable",
      usage: null,
      windows: [{ available: false, usage: null }]
    });
  });

  it("fails closed when an available response omits finite-window usage", () => {
    const quota = mapCommerceQuotaStatus({
      plan: "plus",
      enforced: true,
      unlimited: false,
      available: true,
      state: "available",
      window_hours: 24,
      limits: { total_tokens: 10_000_000, calls: null, total_cost_usd: null },
      usage: null,
      exceeded: [],
      windows: [
        {
          key: "24h",
          window_hours: 24,
          available: true,
          limits: { total_tokens: 10_000_000, calls: null, total_cost_usd: null },
          usage: null,
          exceeded: []
        }
      ]
    });

    expect(quota.available).toBe(false);
    expect(quota.state).toBe("metering_unavailable");
    expect(quota.windows[0]).toMatchObject({ available: false, usage: null });
  });
});
