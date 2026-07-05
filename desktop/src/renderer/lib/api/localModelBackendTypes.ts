export interface BackendLocalLlmBackend {
  kind?: string;
  base_url?: string;
  models?: string[];
  model?: string;
}

export interface BackendLocalModelReadinessCheck {
  key?: string;
  label?: string;
  ok?: boolean;
  actual?: string;
  required?: string;
}

export interface BackendLocalModelReadiness {
  can_install?: boolean;
  recommended_model?: string;
  reason?: string;
  checks?: BackendLocalModelReadinessCheck[];
  memory_total_bytes?: number;
  disk_free_bytes?: number;
  cpu_logical_cores?: number;
  gpu_summary?: string;
}

export interface BackendLocalLlmHealth {
  available?: boolean;
  selected_backend?: BackendLocalLlmBackend | null;
  probe_order?: string[];
  error?: string;
  kind?: string;
  base_url?: string;
  models?: string[];
  model?: string;
  readiness?: BackendLocalModelReadiness;
}

export interface BackendLocalModelSetupStep {
  key?: string;
  label?: string;
  state?: string;
  detail?: string;
}

export interface BackendLocalModelRepairAction {
  code?: string;
  label?: string;
  detail?: string;
}

export interface BackendLocalModelVerification {
  ready?: boolean;
  next_action?: string;
  paths_redacted?: boolean;
  privacy_fallback?: string;
}

export interface BackendLocalModelEvidenceItem {
  key?: string;
  ok?: boolean;
  detail?: string;
  value?: unknown;
  failed_checks?: unknown[];
  configured?: boolean;
}

export interface BackendLocalModelSetupPlan {
  ready?: boolean;
  can_install?: boolean;
  model?: string;
  readiness?: BackendLocalModelReadiness;
  installed?: boolean;
  running?: boolean;
  models?: string[];
  has_model?: boolean;
  runtime_source?: string;
  bundled_runtime_available?: boolean;
  bundled_runtime_path?: string;
  bundled_models_available?: boolean;
  bundled_models_path?: string;
  bundled_model_available?: boolean;
  bundled_model_configured?: boolean;
  bundle_manifest?: BackendLocalModelBundleManifest;
  steps?: BackendLocalModelSetupStep[];
  next_action?: string;
  repair_action?: BackendLocalModelRepairAction;
  verification?: BackendLocalModelVerification;
  evidence?: BackendLocalModelEvidenceItem[];
}

export interface BackendLocalModelBundleManifest {
  present?: boolean;
  valid?: boolean;
  path?: string;
  model?: string;
  accepted_licenses?: boolean;
  runtime_sha256?: string;
  models_sha256?: string;
  runtime_files?: number;
  models_files?: number;
  error?: string;
}
