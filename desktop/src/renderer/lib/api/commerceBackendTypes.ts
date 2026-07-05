export type BackendCommercePlan = "free" | "pro" | "max" | "team";

export interface BackendCommercePlanStatus {
  plan: BackendCommercePlan;
  remote_desktop_enabled: boolean;
  features: Record<string, boolean>;
  high_risk_features: string[];
}

export interface BackendCommerceLicenseStatus {
  state:
    | "absent"
    | "active"
    | "expired"
    | "revoked"
    | "revocation_required"
    | "revocation_stale"
    | "device_mismatch"
    | "device_unverified"
    | "device_fingerprint_missing"
    | "device_fingerprint_mismatch"
    | "device_proof_missing"
    | "device_proof_mismatch"
    | "device_proof_weak"
    | "subscription_inactive"
    | "subscription_confirmation_required"
    | "subscription_confirmation_failed"
    | "invalid"
    | "revocation_data_invalid"
    | "verifier_unconfigured";
  present: boolean;
  active: boolean;
  expired: boolean;
  revoked?: boolean;
  verifier_configured: boolean;
  managed_by?: "environment" | "file" | null;
  requested_env_plan?: BackendCommercePlan;
  plan_env_ignored?: boolean;
  license_id?: string | null;
  issuer?: string | null;
  replaces?: string | null;
  revocation_capable?: boolean;
  revocation_source?: "environment" | "file" | null;
  revocation_generated_at?: string | null;
  plan?: BackendCommercePlan;
  subject?: string;
  seats?: number;
  subscription_id?: string | null;
  subscription_status?: "active" | "trialing" | "past_due" | "canceled" | "expired" | "revoked" | null;
  renews_at?: string | null;
  cancel_at_period_end?: boolean;
  device_id?: string | null;
  order_ref?: string | null;
  issued_at?: string | null;
  expires_at?: string | null;
  error_code?: string;
}

export interface BackendCommerceQuotaStatus {
  plan: BackendCommercePlan;
  enforced: boolean;
  unlimited: boolean;
  window_hours: number;
  limits: {
    total_tokens: number | null;
    calls: number | null;
    total_cost_usd: number | null;
  };
  usage?: {
    calls: number;
    total_tokens: number;
    total_cost_usd: number;
    window_hours: number;
    last_event_at?: string;
  } | null;
  exceeded: string[];
  windows?: Array<{
    key?: string;
    window_hours: number;
    limits: {
      total_tokens: number | null;
      calls: number | null;
      total_cost_usd: number | null;
    };
    usage?: {
      calls: number;
      total_tokens: number;
      total_cost_usd: number;
      window_hours: number;
      last_event_at?: string;
    } | null;
    exceeded?: string[];
  }>;
}
