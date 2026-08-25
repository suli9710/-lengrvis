import type { LocalLLMHealth, LocalModelReadiness, LocalModelSetupPlan } from "../../../shared/localModelTypes";
import type {
  BackendLocalLlmHealth,
  BackendLocalModelBundleManifest,
  BackendLocalModelEvidenceItem,
  BackendLocalModelReadiness,
  BackendLocalModelRepairAction,
  BackendLocalModelSetupPlan,
  BackendLocalModelVerification
} from "./localModelBackendTypes";
import { numberOrUndefined, optionalString } from "./mapperPrimitives";

export function mapLocalLlmHealth(health: BackendLocalLlmHealth): LocalLLMHealth {
  const fallbackBackend =
    health.available && health.kind
      ? {
          kind: health.kind,
          base_url: health.base_url,
          models: health.models,
          model: health.model
        }
      : null;
  const selected = health.selected_backend ?? fallbackBackend;
  const models = Array.isArray(selected?.models)
    ? selected.models.map(String)
    : Array.isArray(health.models)
      ? health.models.map(String)
      : [];
  const model = selected?.model ? String(selected.model) : models[0];

  return {
    available: Boolean(health.available),
    selectedBackend: selected
      ? {
          kind: String(selected.kind ?? health.kind ?? "local"),
          baseUrl: String(selected.base_url ?? health.base_url ?? ""),
          models,
          ...(model ? { model } : {})
        }
      : null,
    probeOrder: (health.probe_order ?? []).map(String),
    error: typeof health.error === "string" ? health.error : "",
    readiness: mapLocalModelReadiness(health.readiness)
  };
}

export function mapLocalModelReadiness(readiness?: BackendLocalModelReadiness): LocalModelReadiness | undefined {
  if (!readiness || typeof readiness !== "object") return undefined;
  return {
    canInstall: Boolean(readiness.can_install),
    recommendedModel: String(readiness.recommended_model ?? ""),
    reason: String(readiness.reason ?? ""),
    checks: (readiness.checks ?? []).map((check) => ({
      key: String(check.key ?? ""),
      label: String(check.label ?? ""),
      ok: Boolean(check.ok),
      actual: String(check.actual ?? ""),
      required: String(check.required ?? "")
    })),
    memoryTotalBytes: Number(readiness.memory_total_bytes ?? 0),
    diskFreeBytes: Number(readiness.disk_free_bytes ?? 0),
    cpuLogicalCores: Number(readiness.cpu_logical_cores ?? 0),
    gpuSummary: readiness.gpu_summary ? String(readiness.gpu_summary) : ""
  };
}

export function mapLocalModelSetupPlan(plan: BackendLocalModelSetupPlan): LocalModelSetupPlan {
  return {
    ready: Boolean(plan.ready),
    canInstall: Boolean(plan.can_install),
    model: String(plan.model ?? ""),
    readiness: mapLocalModelReadiness(plan.readiness),
    installed: Boolean(plan.installed),
    running: Boolean(plan.running),
    models: (plan.models ?? []).map(String),
    hasModel: Boolean(plan.has_model),
    runtimeSource: String(plan.runtime_source ?? ""),
    bundledRuntimeAvailable: Boolean(plan.bundled_runtime_available),
    bundledRuntimePath: String(plan.bundled_runtime_path ?? ""),
    bundledModelsAvailable: Boolean(plan.bundled_models_available),
    bundledModelsPath: String(plan.bundled_models_path ?? ""),
    bundledModelAvailable: Boolean(plan.bundled_model_available),
    bundledModelConfigured: Boolean(plan.bundled_model_configured),
    bundleManifest: mapLocalModelBundleManifest(plan.bundle_manifest),
    steps: (plan.steps ?? []).map((step) => ({
      key: String(step.key ?? ""),
      label: String(step.label ?? ""),
      state: mapLocalModelSetupStepState(step.state),
      detail: String(step.detail ?? "")
    })),
    nextAction: String(plan.next_action ?? ""),
    repairAction: mapLocalModelRepairAction(plan.repair_action),
    verification: mapLocalModelVerification(plan.verification),
    evidence: (plan.evidence ?? []).map(mapLocalModelEvidenceItem)
  };
}

export function mapLocalModelRepairAction(action?: BackendLocalModelRepairAction): LocalModelSetupPlan["repairAction"] {
  if (!action || typeof action !== "object") return undefined;
  return {
    code: String(action.code ?? ""),
    label: String(action.label ?? ""),
    detail: String(action.detail ?? "")
  };
}

export function mapLocalModelVerification(verification?: BackendLocalModelVerification): LocalModelSetupPlan["verification"] {
  if (!verification || typeof verification !== "object") return undefined;
  return {
    ready: Boolean(verification.ready),
    nextAction: String(verification.next_action ?? ""),
    pathsRedacted: verification.paths_redacted !== false,
    privacyFallback: String(verification.privacy_fallback ?? "")
  };
}

export function mapLocalModelEvidenceItem(item: BackendLocalModelEvidenceItem): LocalModelSetupPlan["evidence"][number] {
  return {
    key: String(item.key ?? ""),
    ok: Boolean(item.ok),
    detail: String(item.detail ?? ""),
    valueLabel: localModelEvidenceValueLabel(item)
  };
}

export function localModelEvidenceValueLabel(item: BackendLocalModelEvidenceItem): string {
  if (item.value !== undefined && item.value !== null && typeof item.value !== "object") {
    return String(item.value);
  }
  if (Array.isArray(item.failed_checks) && item.failed_checks.length) {
    return `${item.failed_checks.length} checks need attention`;
  }
  if (item.configured !== undefined) {
    return item.configured ? "configured" : "not configured";
  }
  return item.ok ? "ok" : "needs attention";
}

export function mapLocalModelBundleManifest(manifest?: BackendLocalModelBundleManifest): LocalModelSetupPlan["bundleManifest"] {
  if (!manifest || typeof manifest !== "object") return { present: false };
  return {
    present: Boolean(manifest.present),
    valid: manifest.valid === undefined ? undefined : Boolean(manifest.valid),
    path: optionalString(manifest.path),
    model: optionalString(manifest.model),
    acceptedLicenses: manifest.accepted_licenses === undefined ? undefined : Boolean(manifest.accepted_licenses),
    runtimeSha256: optionalString(manifest.runtime_sha256),
    modelsSha256: optionalString(manifest.models_sha256),
    runtimeFiles: numberOrUndefined(manifest.runtime_files),
    modelsFiles: numberOrUndefined(manifest.models_files),
    error: optionalString(manifest.error)
  };
}

export function mapLocalModelSetupStepState(value: unknown): LocalModelSetupPlan["steps"][number]["state"] {
  if (value === "pending" || value === "current" || value === "done" || value === "blocked") {
    return value;
  }
  return "pending";
}
