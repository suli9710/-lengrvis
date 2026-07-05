export interface LocalLLMBackend {
  kind: string;
  baseUrl: string;
  models: string[];
  model?: string;
}

export interface LocalModelReadinessCheck {
  key: string;
  label: string;
  ok: boolean;
  actual: string;
  required: string;
}

export interface LocalModelReadiness {
  canInstall: boolean;
  recommendedModel: string;
  reason: string;
  checks: LocalModelReadinessCheck[];
  memoryTotalBytes: number;
  diskFreeBytes: number;
  cpuLogicalCores: number;
  gpuSummary?: string;
}

export interface LocalLLMHealth {
  available: boolean;
  selectedBackend: LocalLLMBackend | null;
  probeOrder: string[];
  error?: string;
  readiness?: LocalModelReadiness;
}

export type LocalModelSetupStepState = "pending" | "current" | "done" | "blocked";

export interface LocalModelSetupStep {
  key: string;
  label: string;
  state: LocalModelSetupStepState;
  detail: string;
}

export interface LocalModelRepairAction {
  code: string;
  label: string;
  detail: string;
}

export interface LocalModelEvidenceItem {
  key: string;
  ok: boolean;
  detail: string;
  valueLabel: string;
}

export interface LocalModelVerificationSummary {
  ready: boolean;
  nextAction: string;
  pathsRedacted: boolean;
  privacyFallback: string;
}

export interface LocalModelSetupPlan {
  ready: boolean;
  canInstall: boolean;
  model: string;
  readiness?: LocalModelReadiness;
  installed: boolean;
  running: boolean;
  models: string[];
  hasModel: boolean;
  runtimeSource: "bundled" | "system" | "missing" | string;
  bundledRuntimeAvailable: boolean;
  bundledRuntimePath: string;
  bundledModelsAvailable: boolean;
  bundledModelsPath: string;
  bundledModelAvailable: boolean;
  bundledModelConfigured: boolean;
  bundleManifest: {
    present: boolean;
    valid?: boolean;
    path?: string;
    model?: string;
    acceptedLicenses?: boolean;
    runtimeSha256?: string;
    modelsSha256?: string;
    runtimeFiles?: number;
    modelsFiles?: number;
    error?: string;
  };
  steps: LocalModelSetupStep[];
  nextAction: "hardware_blocked" | "install_runtime" | "start_runtime" | "use_bundled_model" | "download_model" | "ready" | string;
  repairAction?: LocalModelRepairAction;
  verification?: LocalModelVerificationSummary;
  evidence: LocalModelEvidenceItem[];
}
