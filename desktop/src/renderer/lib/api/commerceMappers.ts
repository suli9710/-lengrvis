import type {
  CommerceFeature,
  CommerceLicenseStatus,
  CommercePlan,
  CommercePlanStatus,
  CommerceQuotaStatus
} from "../../../shared/commerceTypes";
import type {
  BackendCommerceLicenseStatus,
  BackendCommercePlanStatus,
  BackendCommerceQuotaStatus
} from "./commerceBackendTypes";

export function mapCommercePlanStatus(data: BackendCommercePlanStatus): CommercePlanStatus {
  return {
    plan: normalizeCommercePlan(data.plan),
    remoteDesktopEnabled: Boolean(data.remote_desktop_enabled),
    features: data.features as Record<CommerceFeature, boolean>,
    highRiskFeatures: data.high_risk_features as CommerceFeature[]
  };
}

export function mapCommerceLicenseStatus(data: BackendCommerceLicenseStatus): CommerceLicenseStatus {
  return {
    state: data.state,
    present: Boolean(data.present),
    active: Boolean(data.active),
    expired: Boolean(data.expired),
    revoked: Boolean(data.revoked),
    verifierConfigured: Boolean(data.verifier_configured),
    managedBy: data.managed_by ?? undefined,
    requestedEnvPlan: data.requested_env_plan ? normalizeCommercePlan(data.requested_env_plan) : undefined,
    planEnvIgnored: Boolean(data.plan_env_ignored),
    licenseId: data.license_id ?? undefined,
    issuer: data.issuer ?? undefined,
    replaces: data.replaces ?? undefined,
    revocationCapable: Boolean(data.revocation_capable),
    revocationSource: data.revocation_source ?? undefined,
    revocationGeneratedAt: data.revocation_generated_at ?? undefined,
    plan: data.plan ? normalizeCommercePlan(data.plan) : undefined,
    subject: data.subject || undefined,
    seats: data.seats,
    subscriptionId: data.subscription_id ?? undefined,
    subscriptionStatus: data.subscription_status ?? undefined,
    renewsAt: data.renews_at ?? undefined,
    cancelAtPeriodEnd: Boolean(data.cancel_at_period_end),
    deviceId: data.device_id ?? undefined,
    orderRef: data.order_ref ?? undefined,
    issuedAt: data.issued_at ?? undefined,
    expiresAt: data.expires_at ?? undefined,
    errorCode: data.error_code
  };
}

export function mapCommerceQuotaStatus(data: BackendCommerceQuotaStatus): CommerceQuotaStatus {
  const usage = data.usage
    ? {
        calls: Number(data.usage.calls || 0),
        totalTokens: Number(data.usage.total_tokens || 0),
        totalCostUsd: Number(data.usage.total_cost_usd || 0),
        windowHours: Number(data.usage.window_hours || data.window_hours || 0),
        lastEventAt: data.usage.last_event_at || undefined
      }
    : undefined;
  const windows = Array.isArray(data.windows)
    ? data.windows.map((window) => ({
        key: window.key || `${Number(window.window_hours || 0)}h`,
        windowHours: Number(window.window_hours || 0),
        limits: {
          totalTokens: window.limits.total_tokens,
          calls: window.limits.calls,
          totalCostUsd: window.limits.total_cost_usd
        },
        usage: window.usage
          ? {
              calls: Number(window.usage.calls || 0),
              totalTokens: Number(window.usage.total_tokens || 0),
              totalCostUsd: Number(window.usage.total_cost_usd || 0),
              windowHours: Number(window.usage.window_hours || window.window_hours || 0),
              lastEventAt: window.usage.last_event_at || undefined
            }
          : undefined,
        exceeded: Array.isArray(window.exceeded) ? window.exceeded.map(String) : []
      }))
    : [];
  return {
    plan: normalizeCommercePlan(data.plan),
    enforced: Boolean(data.enforced),
    unlimited: Boolean(data.unlimited),
    windowHours: Number(data.window_hours || 0),
    limits: {
      totalTokens: data.limits.total_tokens,
      calls: data.limits.calls,
      totalCostUsd: data.limits.total_cost_usd
    },
    usage,
    exceeded: Array.isArray(data.exceeded) ? data.exceeded.map(String) : [],
    windows:
      windows.length > 0
        ? windows
        : [
            {
              key: `${Number(data.window_hours || 0)}h`,
              windowHours: Number(data.window_hours || 0),
              limits: {
                totalTokens: data.limits.total_tokens,
                calls: data.limits.calls,
                totalCostUsd: data.limits.total_cost_usd
              },
              usage,
              exceeded: Array.isArray(data.exceeded) ? data.exceeded.map(String) : []
            }
          ]
  };
}

export function normalizeCommercePlan(plan: "free" | "pro" | "max" | "team"): CommercePlan {
  return plan === "team" ? "max" : plan;
}
