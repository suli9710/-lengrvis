export type HardwareAccelerationRuntime = "auto" | "winml" | "directml" | "openvino" | "cpu";

export type HardwareAccelerationOperation = "warmup" | "test_generate" | "test_embedding" | "test_ocr" | "test_image_embedding";

export type HardwareAccelerationStatus = "ready" | "missing" | "error";

export interface HardwareAccelerationCheck {
  key: string;
  label: string;
  status: HardwareAccelerationStatus;
  details?: string;
  required?: string;
  actual?: string;
}

export interface HardwareAccelerationStatusPayload {
  available: boolean;
  kind: string;
  modelPath: string;
  executionProvider: string;
  availableProviders: string[];
  generationRuntime: string;
  runtimePackage?: string;
  configuredProvider?: string;
  selectedProvider?: string;
  runtimePackages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: {
    available?: boolean;
    provider?: string;
    providerAvailable?: boolean;
    packages?: string[];
    errors?: Record<string, string>;
  };
  errors?: string[];
  error?: string;
  llm?: {
    runtime?: string;
    available?: boolean;
    modelPath?: string;
    configuredProvider?: string;
    selectedProvider?: string;
    runtimePackages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
    winml?: {
      available?: boolean;
      provider?: string;
      providerAvailable?: boolean;
      packages?: string[];
      errors?: Record<string, string>;
    };
    errors?: string[];
  };
  textEmbedding?: HardwareAccelerationComponentStatus;
  imageEmbedding?: HardwareAccelerationComponentStatus;
  ocr?: HardwareAccelerationComponentStatus;
}

export interface HardwareAccelerationComponentStatus {
  available: boolean;
  component?: string;
  kind?: string;
  modelPath?: string;
  executionProvider?: string;
  availableProviders?: string[];
  runtimePackage?: string;
  configuredProvider?: string;
  selectedProvider?: string;
  runtimePackages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: HardwareAccelerationStatusPayload["winml"];
  selectedBackend?: string;
  runtime?: string;
  model?: string;
  errors?: string[];
  error?: string;
}

export interface HardwareAccelerationSmokePayload {
  ok: boolean;
  available: boolean;
  status: "ready" | "unavailable";
  operation: HardwareAccelerationOperation;
  error?: string;
  errors?: string[];
  message?: string;
  count?: number;
  dim?: number;
  source?: string;
  selectedBackend?: string;
  runtime?: string;
  model?: string;
  smoke?: string;
  backend?: {
    kind: string;
    model_path: string;
    execution_provider: string;
    available_providers: string[];
    generation_runtime: string;
    runtime_package?: string;
    model_family?: string;
    provider_options?: Record<string, string>;
  };
  llm?: HardwareAccelerationStatusPayload["llm"];
  textEmbedding?: HardwareAccelerationComponentStatus;
  imageEmbedding?: HardwareAccelerationComponentStatus;
  ocr?: HardwareAccelerationComponentStatus;
}
