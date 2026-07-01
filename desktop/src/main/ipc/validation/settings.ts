import { API_REQUEST_SECURITY_LIMITS } from "../../../shared/ipc";
import { PRIVACY_ERASE_CONFIRMATION_TEXT } from "../../../shared/privacy";
import type { DesktopOpenSettingsRequest, DesktopPrivacyEraseRequest, DesktopSettingsPatch } from "../../../shared/types";
import {
  ApiRequestValidationError,
  assertJsonSafeValue,
  assertSafeFieldName,
  isPlainRecord,
  rejectUnexpectedBridgeKeys,
  validateBridgeBoolean,
  validateBridgeFiniteNumber,
  validateBridgeStringArray,
  validateBridgeStringValue,
  validatePlainBridgeBody
} from "./primitives";

const PRIVACY_ERASE_ALLOWED_KEYS = new Set(["confirmationText", "includeSettings"]);
const SETTINGS_EGRESS_CONFIRMATION_FIELDS = new Set(["base_url", "wire_api"]);
const SETTINGS_NATIVE_CONFIRMATION_FIELDS = new Set([
  "allowed_directories",
  "allow_browser_network",
  "allow_cloud_context",
  "allow_file_content_upload",
  "app_allowlist",
  "mcp_servers",
  "remote_desktop_enabled"
]);
const SETTINGS_URI_ALLOWLIST = new Set([
  "ms-settings:",
  "ms-settings:appsfeatures",
  "ms-settings:network",
  "ms-settings:privacy",
  "ms-settings:privacy-backgroundapps",
  "ms-settings:windowsupdate"
]);
const SETTINGS_PATCH_VALUE_KINDS: Record<
  string,
  "string" | "number" | "boolean" | "stringArray" | "mcpServers"
> = {
  provider_name: "string",
  base_url: "string",
  model: "string",
  review_model: "string",
  wire_api: "string",
  requires_openai_auth: "boolean",
  model_reasoning_effort: "string",
  disable_response_storage: "boolean",
  temperature: "number",
  max_tokens: "number",
  timeout: "number",
  llm_api_max_retries: "number",
  llm_api_retry_backoff_seconds: "number",
  llm_api_circuit_failure_threshold: "number",
  llm_api_circuit_cooldown_seconds: "number",
  model_context_window: "number",
  model_auto_compact_token_limit: "number",
  allowed_directories: "stringArray",
  allow_browser_network: "boolean",
  remote_desktop_enabled: "boolean",
  app_allowlist: "stringArray",
  browser_max_page_bytes: "number",
  browser_screenshot_dir: "string",
  onnx_model_path: "string",
  onnx_execution_provider: "string",
  onnx_provider_preference: "string",
  onnx_directml_device_id: "string",
  onnx_openvino_device: "string",
  onnx_openvino_cache_dir: "string",
  onnx_warm_on_startup: "boolean",
  onnx_model_family: "string",
  embedding_backend: "string",
  onnx_embedding_model_path: "string",
  onnx_embedding_execution_provider: "string",
  onnx_embedding_model_id: "string",
  onnx_embedding_max_batch_size: "number",
  image_embedding_backend: "string",
  onnx_image_embedding_model_path: "string",
  onnx_image_embedding_execution_provider: "string",
  onnx_image_embedding_model_id: "string",
  onnx_image_embedding_max_batch_size: "number",
  ocr_backend: "string",
  ocr_execution_provider: "string",
  ocr_openvino_model_dir: "string",
  ocr_openvino_device: "string",
  ocr_lang: "string",
  ocr_min_confidence: "number",
  ocr_batch_size: "number",
  mode: "string",
  permission_mode: "string",
  allow_cloud_context: "boolean",
  allow_file_content_upload: "boolean",
  confirmation_nonce: "string",
  mcp_servers: "mcpServers"
};
const MCP_SERVER_ALLOWED_KEYS = new Set(["id", "name", "url", "command", "args", "enabled", "transport", "auth"]);

export function validateOpenSettingsRequest(value: unknown): DesktopOpenSettingsRequest {
  const request = validatePlainBridgeBody(value, "open settings request");
  rejectUnexpectedBridgeKeys(request, new Set(["uri"]), "open settings request");
  const uri = validateBridgeStringValue(request.uri, "settings uri", 128, { allowEmpty: false, trim: true });
  if (!SETTINGS_URI_ALLOWLIST.has(uri)) {
    throw new ApiRequestValidationError("settings uri is not allowed");
  }
  return { uri };
}

export function validatePrivacyEraseRequest(value: unknown): Pick<DesktopPrivacyEraseRequest, "includeSettings"> {
  const request = validatePlainBridgeBody(value, "privacy erase request") as DesktopPrivacyEraseRequest &
    Record<string, unknown>;
  rejectUnexpectedBridgeKeys(request, PRIVACY_ERASE_ALLOWED_KEYS, "privacy erase request");
  if (request.confirmationText !== PRIVACY_ERASE_CONFIRMATION_TEXT) {
    throw new ApiRequestValidationError("privacy erase confirmation text does not match");
  }
  return {
    includeSettings: validateBridgeBoolean(request.includeSettings, "privacy erase includeSettings")
  };
}

export function validateSettingsPatchRequest(value: unknown): DesktopSettingsPatch {
  const request = validatePlainBridgeBody(value, "settings patch");
  const entries = Object.entries(request);
  if (entries.length > API_REQUEST_SECURITY_LIMITS.maxBodyObjectKeys) {
    throw new ApiRequestValidationError("settings patch has too many fields");
  }

  const patch: DesktopSettingsPatch = {};
  for (const [key, item] of entries) {
    assertSafeFieldName(key, "settings patch key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    const kind = SETTINGS_PATCH_VALUE_KINDS[key];
    if (!kind) {
      throw new ApiRequestValidationError(`settings patch field is not allowed: ${key}`);
    }
    if (item === undefined) {
      continue;
    }
    if (item === null) {
      throw new ApiRequestValidationError(`settings patch field is invalid: ${key}`);
    }

    if (kind === "string") {
      patch[key] = validateBridgeStringValue(item, `settings patch ${key}`, key === "confirmation_nonce" ? 256 : 4096, {
        allowEmpty: true,
        trim: true
      });
    } else if (kind === "number") {
      patch[key] = validateBridgeFiniteNumber(item, `settings patch ${key}`);
    } else if (kind === "boolean") {
      patch[key] = validateBridgeBoolean(item, `settings patch ${key}`);
    } else if (kind === "stringArray") {
      patch[key] = validateBridgeStringArray(item, `settings patch ${key}`, 256, 4096);
    } else {
      patch[key] = validateMcpServers(item);
    }
  }

  return patch;
}

export function settingsEgressChangeRequiresConfirmation(patch: DesktopSettingsPatch): boolean {
  return Object.keys(patch).some((key) => SETTINGS_EGRESS_CONFIRMATION_FIELDS.has(key));
}

export function settingsNativeChangeRequiresConfirmation(patch: DesktopSettingsPatch): boolean {
  return Object.keys(patch).some((key) => SETTINGS_NATIVE_CONFIRMATION_FIELDS.has(key));
}

function validateMcpServers(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value) || value.length > 50) {
    throw new ApiRequestValidationError("settings patch mcp_servers is invalid");
  }
  return value.map((server, index) => validateMcpServer(server, index));
}

function validateMcpServer(value: unknown, index: number): Record<string, unknown> {
  const request = validatePlainBridgeBody(value, `settings patch mcp_servers[${index}]`);
  for (const key of Object.keys(request)) {
    assertSafeFieldName(key, "MCP server key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (!MCP_SERVER_ALLOWED_KEYS.has(key)) {
      throw new ApiRequestValidationError(`MCP server field is not allowed: ${key}`);
    }
  }

  const server: Record<string, unknown> = {};
  for (const key of ["id", "name", "url", "command", "transport"]) {
    if (request[key] !== undefined) {
      server[key] = validateBridgeStringValue(request[key], `MCP server ${key}`, key === "name" ? 256 : 4096, {
        allowEmpty: true,
        trim: true
      });
    }
  }
  if (request.args !== undefined) {
    server.args = validateBridgeStringArray(request.args, "MCP server args", 100, 4096);
  }
  if (request.enabled !== undefined) {
    server.enabled = validateBridgeBoolean(request.enabled, "MCP server enabled");
  }
  if (request.auth !== undefined) {
    if (!isPlainRecord(request.auth)) {
      throw new ApiRequestValidationError("MCP server auth must be an object");
    }
    assertJsonSafeValue(request.auth, 0, new WeakSet<object>());
    server.auth = JSON.parse(JSON.stringify(request.auth)) as Record<string, unknown>;
  }
  return server;
}
