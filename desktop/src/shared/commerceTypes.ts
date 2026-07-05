export type CommercePlan = "free" | "pro" | "max";

export type CommerceFeature =
  | "local_read_only"
  | "basic_tasks"
  | "cloud_quota"
  | "document_ai"
  | "scheduling"
  | "remote_view"
  | "remote_control"
  | "audit_export"
  | "policy_management"
  | "private_deployment";

export interface CommercePlanStatus {
  plan: CommercePlan;
  remoteDesktopEnabled: boolean;
  features: Record<CommerceFeature, boolean>;
  highRiskFeatures: CommerceFeature[];
}

export type CommerceLicenseState =
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

export interface CommerceLicenseStatus {
  state: CommerceLicenseState;
  present: boolean;
  active: boolean;
  expired: boolean;
  revoked?: boolean;
  verifierConfigured: boolean;
  managedBy?: "environment" | "file";
  requestedEnvPlan?: CommercePlan;
  planEnvIgnored?: boolean;
  licenseId?: string;
  issuer?: string;
  replaces?: string;
  revocationCapable?: boolean;
  revocationSource?: "environment" | "file";
  revocationGeneratedAt?: string;
  plan?: CommercePlan;
  subject?: string;
  seats?: number;
  subscriptionId?: string;
  subscriptionStatus?: "active" | "trialing" | "past_due" | "canceled" | "expired" | "revoked";
  renewsAt?: string;
  cancelAtPeriodEnd?: boolean;
  deviceId?: string;
  orderRef?: string;
  issuedAt?: string;
  expiresAt?: string;
  errorCode?: string;
}

export interface CommerceQuotaWindow {
  key: string;
  windowHours: number;
  limits: {
    totalTokens: number | null;
    calls: number | null;
    totalCostUsd: number | null;
  };
  usage?: {
    calls: number;
    totalTokens: number;
    totalCostUsd: number;
    windowHours: number;
    lastEventAt?: string;
  };
  exceeded: string[];
}

export interface CommerceQuotaStatus {
  plan: CommercePlan;
  enforced: boolean;
  unlimited: boolean;
  windowHours: number;
  limits: {
    totalTokens: number | null;
    calls: number | null;
    totalCostUsd: number | null;
  };
  usage?: {
    calls: number;
    totalTokens: number;
    totalCostUsd: number;
    windowHours: number;
    lastEventAt?: string;
  };
  exceeded: string[];
  windows: CommerceQuotaWindow[];
}
