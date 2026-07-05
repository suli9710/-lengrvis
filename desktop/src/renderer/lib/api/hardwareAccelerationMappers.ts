import type { AppSettings } from "../../../shared/settingsTypes";
import type {
  HardwareAccelerationOperation,
  HardwareAccelerationSmokePayload,
  HardwareAccelerationStatusPayload
} from "../../../shared/hardwareAccelerationTypes";
import type {
  BackendHardwareAccelerationComponentStatus,
  BackendHardwareAccelerationSmoke,
  BackendHardwareAccelerationStatus
} from "./hardwareAccelerationBackendTypes";

export function mapHardwareAccelerationStatus(status: BackendHardwareAccelerationStatus): HardwareAccelerationStatusPayload {
  return {
    available: Boolean(status.available),
    kind: String(status.kind ?? "onnx"),
    modelPath: String(status.model_path ?? ""),
    executionProvider: String(status.execution_provider ?? ""),
    availableProviders: (status.available_providers ?? []).map(String),
    generationRuntime: String(status.generation_runtime ?? ""),
    runtimePackage: status.runtime_package ? String(status.runtime_package) : undefined,
    configuredProvider: status.configured_provider ? String(status.configured_provider) : undefined,
    selectedProvider: status.selected_provider ? String(status.selected_provider) : undefined,
    runtimePackages: mapRuntimePackages(status.runtime_packages),
    winml: status.winml ? mapWinmlStatus(status.winml) : undefined,
    errors: Array.isArray(status.errors) ? status.errors.map(String) : status.error ? [String(status.error)] : [],
    error: status.error ? String(status.error) : undefined,
    llm: status.llm ? mapHardwareAccelerationLlm(status.llm) : undefined,
    textEmbedding: status.text_embedding ? mapHardwareAccelerationComponent(status.text_embedding) : undefined,
    imageEmbedding: status.image_embedding ? mapHardwareAccelerationComponent(status.image_embedding) : undefined,
    ocr: status.ocr ? mapHardwareAccelerationComponent(status.ocr) : undefined
  };
}

export function mapHardwareAccelerationSmoke(
  data: BackendHardwareAccelerationSmoke,
  fallbackOperation: HardwareAccelerationOperation = "warmup"
): HardwareAccelerationSmokePayload {
  return {
    ok: Boolean(data.ok),
    available: Boolean(data.available ?? data.ok),
    status: data.status === "ready" || (data.status === undefined && data.ok) ? "ready" : "unavailable",
    operation: mapHardwareAccelerationOperation(data.operation, fallbackOperation),
    error: data.error ? String(data.error) : undefined,
    errors: Array.isArray(data.errors) ? data.errors.map(String) : data.error ? [String(data.error)] : [],
    message: data.message ? String(data.message) : undefined,
    count: data.count !== undefined ? Number(data.count) : undefined,
    dim: data.dim !== undefined ? Number(data.dim) : undefined,
    source: data.source ? String(data.source) : undefined,
    selectedBackend: data.selected_backend ? String(data.selected_backend) : undefined,
    runtime: data.runtime ? String(data.runtime) : undefined,
    model: data.model ? String(data.model) : undefined,
    smoke: data.smoke ? String(data.smoke) : undefined,
    backend: data.backend
      ? {
          kind: String(data.backend.kind ?? ""),
          model_path: String(data.backend.model_path ?? ""),
          execution_provider: String(data.backend.execution_provider ?? ""),
          available_providers: (data.backend.available_providers ?? []).map(String),
          generation_runtime: String(data.backend.generation_runtime ?? ""),
          runtime_package: data.backend.runtime_package ? String(data.backend.runtime_package) : undefined,
          model_family: data.backend.model_family ? String(data.backend.model_family) : undefined,
          provider_options: data.backend.provider_options ? objectStringRecord(data.backend.provider_options) : {}
        }
      : undefined,
    llm: data.llm ? mapHardwareAccelerationLlm(data.llm) : undefined,
    textEmbedding: data.text_embedding ? mapHardwareAccelerationComponent(data.text_embedding) : undefined,
    imageEmbedding: data.image_embedding ? mapHardwareAccelerationComponent(data.image_embedding) : undefined,
    ocr: data.ocr ? mapHardwareAccelerationComponent(data.ocr) : undefined
  };
}

export function mapHardwareAccelerationLlm(llm: NonNullable<BackendHardwareAccelerationStatus["llm"]>): HardwareAccelerationStatusPayload["llm"] {
  return {
    runtime: String(llm.runtime ?? ""),
    available: Boolean(llm.available),
    modelPath: String(llm.model_path ?? ""),
    configuredProvider: llm.configured_provider ? String(llm.configured_provider) : undefined,
    selectedProvider: llm.selected_provider ? String(llm.selected_provider) : undefined,
    runtimePackages: mapRuntimePackages(llm.runtime_packages),
    winml: llm.winml ? mapWinmlStatus(llm.winml) : undefined,
    errors: Array.isArray(llm.errors) ? llm.errors.map(String) : []
  };
}

export function mapHardwareAccelerationOperation(
  operation?: BackendHardwareAccelerationSmoke["operation"],
  fallbackOperation: HardwareAccelerationOperation = "warmup"
): HardwareAccelerationSmokePayload["operation"] {
  if (
    operation === "warmup" ||
    operation === "test_generate" ||
    operation === "test_embedding" ||
    operation === "test_ocr" ||
    operation === "test_image_embedding"
  ) {
    return operation;
  }
  return fallbackOperation;
}

export function mapHardwareAccelerationComponent(
  component: BackendHardwareAccelerationComponentStatus
): NonNullable<HardwareAccelerationStatusPayload["textEmbedding"]> {
  return {
    available: Boolean(component.available),
    component: component.component ? String(component.component) : undefined,
    kind: component.kind ? String(component.kind) : undefined,
    modelPath: String(component.model_path ?? ""),
    executionProvider: String(component.execution_provider ?? ""),
    availableProviders: (component.available_providers ?? []).map(String),
    runtimePackage: component.runtime_package ? String(component.runtime_package) : undefined,
    configuredProvider: component.configured_provider ? String(component.configured_provider) : undefined,
    selectedProvider: component.selected_provider ? String(component.selected_provider) : undefined,
    runtimePackages: mapRuntimePackages(component.runtime_packages),
    winml: component.winml ? mapWinmlStatus(component.winml) : undefined,
    selectedBackend: component.selected_backend ? String(component.selected_backend) : undefined,
    runtime: component.runtime ? String(component.runtime) : undefined,
    model: component.model ? String(component.model) : undefined,
    errors: Array.isArray(component.errors) ? component.errors.map(String) : component.error ? [String(component.error)] : [],
    error: component.error ? String(component.error) : undefined
  };
}

export function mapWinmlStatus(winml: NonNullable<BackendHardwareAccelerationStatus["winml"]>): NonNullable<HardwareAccelerationStatusPayload["winml"]> {
  return {
    available: Boolean(winml.available),
    provider: String(winml.provider ?? "WindowsMLExecutionProvider"),
    providerAvailable: Boolean(winml.provider_available),
    packages: (winml.packages ?? []).map(String),
    errors: Object.fromEntries(Object.entries(winml.errors ?? {}).map(([key, value]) => [key, String(value)]))
  };
}

export function mapRuntimePackages(
  packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>
): Record<string, { available?: boolean; module?: string; version?: string; error?: string }> | undefined {
  if (!packages) return undefined;
  return Object.fromEntries(
    Object.entries(packages).map(([key, value]) => [
      key,
      {
        available: Boolean(value.available),
        module: value.module ? String(value.module) : undefined,
        version: value.version ? String(value.version) : undefined,
        error: value.error ? String(value.error) : undefined
      }
    ])
  );
}

export function objectStringRecord(value: Record<string, unknown> | undefined): Record<string, string> | undefined {
  if (!value) return undefined;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item)]));
}

export function normalizeExecutionProvider(value: string): AppSettings["onnxExecutionProvider"] {
  const lowered = value.trim().toLowerCase();
  if (!lowered || lowered === "auto") return "";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "directml" || lowered === "dml") return "DirectML";
  if (lowered === "openvino") return "OpenVINO";
  if (lowered === "cpu") return "CPU";
  return value;
}

export function normalizeHardwareRuntime(value: string): string {
  const lowered = String(value ?? "").trim().toLowerCase();
  if (!lowered || lowered === "auto") return "";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "directml" || lowered === "dml") return "DirectML";
  if (lowered === "openvino") return "OpenVINO";
  if (lowered === "cpu") return "CPU";
  return value;
}
