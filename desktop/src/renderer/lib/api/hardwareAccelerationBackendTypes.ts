import type { HardwareAccelerationOperation } from "../../../shared/hardwareAccelerationTypes";

export interface BackendHardwareAccelerationStatus {
  available?: boolean;
  kind?: string;
  model_path?: string;
  execution_provider?: string;
  available_providers?: string[];
  generation_runtime?: string;
  runtime_package?: string;
  configured_provider?: string;
  selected_provider?: string;
  runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: {
    available?: boolean;
    provider?: string;
    provider_available?: boolean;
    packages?: string[];
    errors?: Record<string, string>;
  };
  errors?: string[];
  error?: string;
  llm?: {
    runtime?: string;
    available?: boolean;
    model_path?: string;
    configured_provider?: string;
    selected_provider?: string;
    runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
    winml?: {
      available?: boolean;
      provider?: string;
      provider_available?: boolean;
      packages?: string[];
      errors?: Record<string, string>;
    };
    errors?: string[];
  };
  text_embedding?: BackendHardwareAccelerationComponentStatus;
  image_embedding?: BackendHardwareAccelerationComponentStatus;
  ocr?: BackendHardwareAccelerationComponentStatus;
}

export interface BackendHardwareAccelerationComponentStatus {
  available?: boolean;
  component?: string;
  kind?: string;
  model_path?: string;
  execution_provider?: string;
  available_providers?: string[];
  runtime_package?: string;
  configured_provider?: string;
  selected_provider?: string;
  runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: BackendHardwareAccelerationStatus["winml"];
  selected_backend?: string;
  runtime?: string;
  model?: string;
  errors?: string[];
  error?: string;
}

export interface BackendHardwareAccelerationSmoke {
  ok?: boolean;
  available?: boolean;
  status?: "ready" | "unavailable";
  operation?: HardwareAccelerationOperation;
  error?: string;
  errors?: string[];
  message?: string;
  count?: number;
  dim?: number;
  source?: string;
  selected_backend?: string;
  runtime?: string;
  model?: string;
  smoke?: string;
  backend?: {
    kind?: string;
    model_path?: string;
    execution_provider?: string;
    available_providers?: string[];
    generation_runtime?: string;
    runtime_package?: string;
    model_family?: string;
    provider_options?: Record<string, unknown>;
  };
  llm?: BackendHardwareAccelerationStatus["llm"];
  text_embedding?: BackendHardwareAccelerationComponentStatus;
  image_embedding?: BackendHardwareAccelerationComponentStatus;
  ocr?: BackendHardwareAccelerationComponentStatus;
}

export interface HardwareAccelerationSmokeRequest {
  operation?: HardwareAccelerationOperation;
  prompt?: string;
  maxTokens?: number;
  modelPath?: string;
  texts?: string[];
  imagePath?: string;
}

export type HardwareAccelerationSmokeRequestBody = {
  model_path?: string;
  prompt?: string;
  max_tokens?: number;
  texts?: string[];
  image_path?: string;
};
