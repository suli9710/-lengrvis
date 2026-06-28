import {
  API_REQUEST_ALLOWED_KEYS,
  API_REQUEST_DENIED_EXACT_PATHS,
  API_REQUEST_DENIED_METHOD_PATHS,
  API_REQUEST_DENIED_PATH_PREFIXES,
  API_REQUEST_SECURITY_LIMITS
} from "../shared/ipc";
import { PRIVACY_ERASE_CONFIRMATION_TEXT } from "../shared/privacy";
import type {
  ApiMethod,
  ApiQueryValue,
  ApiRequest,
  DocumentAskRequest,
  DocumentCompareRequest,
  DocumentParseRequest,
  DesktopPermissionPolicyRelaxationRequest,
  DesktopPermissionRule,
  DesktopPermissionRuleDeleteRequest,
  DesktopPermissionRuleUpsertRequest,
  DesktopPrivacyEraseRequest,
  DesktopOpenSettingsRequest,
  DesktopRunStartRequest,
  DesktopScheduleCreateRequest,
  DesktopScheduleEnableRequest,
  DesktopSettingsPatch
} from "../shared/types";
import { assertLoopbackBackendUrl } from "./backendUrl";

const DEFAULT_TIMEOUT_MS = 30_000;
const ALLOWED_API_METHODS = new Set<ApiMethod>(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const DOCUMENT_QUESTION_MAX_CHARS = 8_000;
const DOCUMENT_FOCUS_MAX_CHARS = 4_000;
const API_REQUEST_ALLOWED_KEY_SET = new Set<string>(API_REQUEST_ALLOWED_KEYS);
const API_REQUEST_DENIED_EXACT_PATH_SET = new Set<string>(API_REQUEST_DENIED_EXACT_PATHS);
const API_REQUEST_DENIED_METHOD_PATH_RULES = API_REQUEST_DENIED_METHOD_PATHS;
const API_REQUEST_RESERVED_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const DOCUMENT_PARSE_ALLOWED_KEYS = new Set(["path", "includeText", "include_text"]);
const DOCUMENT_ASK_ALLOWED_KEYS = new Set(["path", "question", "topK", "top_k"]);
const DOCUMENT_COMPARE_ALLOWED_KEYS = new Set(["paths", "focus"]);
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
const RUN_MODES = new Set(["privacy", "efficiency", "hybrid"]);
const RUN_ENGINES = new Set(["auto", "os", "developer"]);
const SETTINGS_URI_ALLOWLIST = new Set([
  "ms-settings:",
  "ms-settings:appsfeatures",
  "ms-settings:network",
  "ms-settings:privacy",
  "ms-settings:privacy-backgroundapps",
  "ms-settings:windowsupdate"
]);
const PERMISSION_EFFECTS = new Set(["allow", "deny"]);
const PERMISSION_RELAXATION_ACTIONS = new Set(["upsert_rule", "delete_rule", "replace_policy"]);
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
const PERMISSION_RULE_ALLOWED_KEYS = new Set([
  "id",
  "name",
  "effect",
  "tool",
  "tools",
  "path_pattern",
  "path_patterns",
  "time_window",
  "time_windows",
  "enabled",
  "reason"
]);
const PERMISSION_TIME_WINDOW_ALLOWED_KEYS = new Set(["days", "start", "end", "timezone"]);

export class ApiRequestValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiRequestValidationError";
  }
}

export interface ValidatedApiRequest {
  endpoint: string;
  method: ApiMethod;
  query?: Record<string, Exclude<ApiQueryValue, null | undefined>>;
  serializedBody?: string;
  timeoutMs: number;
  abortGroup?: string;
}

export interface ApiRequestValidationOptions {
  allowDeniedDesktopBridgePath?: boolean;
}

export function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  return buildValidatedRequestUrl(baseUrl, validateApiRequest(request));
}

export function buildValidatedRequestUrl(baseUrl: string, request: ValidatedApiRequest): URL {
  const backendUrl = loopbackBackendUrlForApiRequest(baseUrl);

  const backendOrigin = backendUrl.origin;
  const url = new URL(request.endpoint, backendUrl);
  if (url.origin !== backendOrigin) {
    throw new ApiRequestValidationError("Renderer API request escaped the configured backend origin");
  }

  for (const [key, value] of Object.entries(request.query ?? {})) {
    url.searchParams.set(key, String(value));
  }
  if (url.search.length > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new ApiRequestValidationError("Renderer API query is too large");
  }

  return url;
}

function loopbackBackendUrlForApiRequest(baseUrl: string): URL {
  try {
    return assertLoopbackBackendUrl(baseUrl, "Desktop API token request");
  } catch (error) {
    throw new ApiRequestValidationError(error instanceof Error ? error.message : "Desktop API token requests require a loopback backend base URL");
  }
}

export function validateApiRequest(request: unknown, options: ApiRequestValidationOptions = {}): ValidatedApiRequest {
  if (!isPlainRecord(request)) {
    throw new ApiRequestValidationError("Renderer API request is malformed");
  }

  rejectUnexpectedApiRequestKeys(request);
  const method = validateApiMethod(request.method);
  const endpoint = validateApiEndpoint(request.endpoint, method, options);
  const query = validateApiQuery(request.query);
  const timeoutMs = validateApiTimeout(request.timeoutMs);
  const serializedBody = serializeApiRequestBody(request, method);
  const abortGroup = validateOptionalApiAbortGroup(request.abortGroup);
  return { endpoint, method, query, serializedBody, timeoutMs, abortGroup };
}

export function validateApiAbortGroup(value: unknown): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError("Renderer API abort group is invalid");
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 64 || !/^[A-Za-z0-9._-]+$/.test(trimmed)) {
    throw new ApiRequestValidationError("Renderer API abort group is invalid");
  }
  return trimmed;
}

function validateOptionalApiAbortGroup(value: unknown): string | undefined {
  if (value === undefined) {
    return undefined;
  }
  return validateApiAbortGroup(value);
}

function rejectUnexpectedApiRequestKeys(request: Record<string, unknown>): void {
  for (const key of Object.keys(request)) {
    if (!API_REQUEST_ALLOWED_KEY_SET.has(key)) {
      const detail = key === "headers" ? "custom headers are not allowed" : `field is not allowed: ${key}`;
      throw new ApiRequestValidationError(`Renderer API request ${detail}`);
    }
  }
}

function validateApiEndpoint(value: unknown, method: ApiMethod, options: ApiRequestValidationOptions = {}): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError("Renderer API endpoint is required");
  }
  if (!value || value.length > API_REQUEST_SECURITY_LIMITS.maxEndpointChars) {
    throw new ApiRequestValidationError("Renderer API endpoint length is invalid");
  }
  if (value.trim() !== value || /\s|[\u0000-\u001F\u007F]/.test(value)) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe characters");
  }
  if (value.includes("?") || value.includes("#")) {
    throw new ApiRequestValidationError("Renderer API endpoint must not include query strings or fragments");
  }
  if (
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("//") ||
    value.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(value)
  ) {
    throw new ApiRequestValidationError("Renderer API requests must use backend-relative endpoints");
  }
  if (/%2f|%5c/i.test(value)) {
    throw new ApiRequestValidationError("Renderer API endpoint must not contain encoded path separators");
  }

  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(value);
  } catch {
    throw new ApiRequestValidationError("Renderer API endpoint encoding is invalid");
  }

  if (decodedPath.includes("\\") || decodedPath.includes("//")) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe path separators");
  }
  if (decodedPath !== "/api" && !decodedPath.startsWith("/api/")) {
    throw new ApiRequestValidationError("Renderer API requests must target backend API paths");
  }

  const segments = decodedPath.split("/");
  if (segments.some((segment) => segment === "." || segment === "..")) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe path segments");
  }

  const normalizedPath = `/${segments.filter(Boolean).join("/")}`;
  if (!options.allowDeniedDesktopBridgePath) {
    rejectDeniedApiPath(normalizedPath, method);
  }
  return value;
}

function rejectDeniedApiPath(pathname: string, method: ApiMethod): void {
  if (API_REQUEST_DENIED_EXACT_PATH_SET.has(pathname)) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
  if (
    API_REQUEST_DENIED_PATH_PREFIXES.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
    )
  ) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
  if (
    API_REQUEST_DENIED_METHOD_PATH_RULES.some((rule) => {
      if (rule.method !== method) {
        return false;
      }
      if ("path" in rule) {
        return pathname === rule.path;
      }
      if (!pathname.startsWith(rule.pathPrefix)) {
        return false;
      }
      if ("pathSuffix" in rule) {
        return pathname.endsWith(rule.pathSuffix);
      }
      return true;
    })
  ) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
}

function validateApiMethod(value: unknown): ApiMethod {
  if (value === undefined) {
    return "GET";
  }
  if (typeof value !== "string" || value !== value.toUpperCase() || !ALLOWED_API_METHODS.has(value as ApiMethod)) {
    throw new ApiRequestValidationError("Renderer API request method is not allowed");
  }
  return value as ApiMethod;
}

function validateApiQuery(value: unknown): ValidatedApiRequest["query"] {
  if (value === undefined) {
    return undefined;
  }
  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError("Renderer API query must be an object");
  }

  const entries = Object.entries(value);
  if (entries.length > API_REQUEST_SECURITY_LIMITS.maxQueryParams) {
    throw new ApiRequestValidationError("Renderer API query has too many parameters");
  }

  let totalBytes = 0;
  const query: NonNullable<ValidatedApiRequest["query"]> = {};
  for (const [key, queryValue] of entries) {
    assertSafeFieldName(key, "Renderer API query key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (queryValue === null || queryValue === undefined) {
      continue;
    }
    if (!["string", "number", "boolean"].includes(typeof queryValue)) {
      throw new ApiRequestValidationError("Renderer API query values must be primitive");
    }
    if (typeof queryValue === "number" && !Number.isFinite(queryValue)) {
      throw new ApiRequestValidationError("Renderer API query number is invalid");
    }
    const stringValue = String(queryValue);
    const valueBytes = utf8ByteLength(stringValue);
    if (valueBytes > API_REQUEST_SECURITY_LIMITS.maxQueryValueChars) {
      throw new ApiRequestValidationError("Renderer API query value is too large");
    }
    totalBytes += utf8ByteLength(key) + valueBytes;
    query[key] = queryValue as Exclude<ApiQueryValue, null | undefined>;
  }

  if (totalBytes > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new ApiRequestValidationError("Renderer API query is too large");
  }

  return Object.keys(query).length ? query : undefined;
}

function validateApiTimeout(value: unknown): number {
  if (value === undefined) {
    return DEFAULT_TIMEOUT_MS;
  }
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    !Number.isInteger(value) ||
    value <= 0 ||
    value > API_REQUEST_SECURITY_LIMITS.maxTimeoutMs
  ) {
    throw new ApiRequestValidationError("Renderer API timeout is invalid");
  }
  return value;
}

export function validateBridgeIdentifier(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} is required`);
  }
  const trimmed = value.trim();
  if (
    !trimmed ||
    trimmed.length > 128 ||
    /[\s/\\?#\u0000-\u001F\u007F]/.test(trimmed) ||
    trimmed === "." ||
    trimmed === ".."
  ) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return trimmed;
}

export function validateCommandExecuteRequest(value: unknown): { name: string; args: Record<string, unknown> } {
  const request = validatePlainBridgeBody(value, "command execute request");
  const name = validateBridgeIdentifier(request.name, "command name");
  const args = request.args === undefined ? {} : validatePlainBridgeBody(request.args, "command args");
  return { name, args };
}

export function validatePlainBridgeBody(value: unknown, label: string): Record<string, unknown> {
  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError(`${label} must be an object`);
  }
  return value;
}

function rejectUnexpectedBridgeKeys(request: Record<string, unknown>, allowedKeys: ReadonlySet<string>, label: string): void {
  for (const key of Object.keys(request)) {
    assertSafeFieldName(key, `${label} key`, API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (!allowedKeys.has(key)) {
      throw new ApiRequestValidationError(`${label} field is not allowed: ${key}`);
    }
  }
}

export function validateBridgePathValue(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} is required`);
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 4096 || trimmed.includes("\0") || /[\u0000-\u001F\u007F]/.test(trimmed)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return trimmed;
}

export function validateOptionalModelRequest(value: unknown, label: string): { model?: string } {
  const request = validatePlainBridgeBody(value, label);
  if (request.model === undefined || request.model === null || request.model === "") {
    return {};
  }
  if (typeof request.model !== "string") {
    throw new ApiRequestValidationError("model must be a string");
  }
  const model = request.model.trim();
  if (!model || model.length > 256 || /[\s\u0000-\u001F\u007F]/.test(model)) {
    throw new ApiRequestValidationError("model is invalid");
  }
  return { model };
}

export function validateRunStartRequest(value: unknown): DesktopRunStartRequest {
  const request = validatePlainBridgeBody(value, "run start request");
  const message = validateBridgeStringValue(request.message, "run message", 20_000, {
    allowEmpty: false,
    trim: true
  });
  const mode = validateBridgeEnum<DesktopRunStartRequest["mode"] & string>(request.mode, "run mode", RUN_MODES, "efficiency");
  const engine = validateBridgeEnum<DesktopRunStartRequest["engine"] & string>(request.engine, "run engine", RUN_ENGINES, "auto");
  return { message, mode, engine };
}

export function validateScheduleCreateRequest(value: unknown): DesktopScheduleCreateRequest {
  const request = validatePlainBridgeBody(value, "schedule create request");
  rejectUnexpectedBridgeKeys(request, new Set(["cron", "goal", "mode", "note"]), "schedule create request");
  const cron = validateBridgeStringValue(request.cron, "schedule cron", 256, { allowEmpty: false, trim: true });
  const goal = validateBridgeStringValue(request.goal, "schedule goal", 20_000, { allowEmpty: false, trim: true });
  const mode = validateBridgeEnum<DesktopScheduleCreateRequest["mode"] & string>(
    request.mode,
    "schedule mode",
    RUN_MODES,
    "efficiency"
  );
  const note =
    request.note === undefined || request.note === null
      ? undefined
      : validateBridgeStringValue(request.note, "schedule note", 2_000, { allowEmpty: true, trim: true });
  return note === undefined ? { cron, goal, mode } : { cron, goal, mode, note };
}

export function validateScheduleEnableRequest(scheduleId: unknown, enabled: unknown): DesktopScheduleEnableRequest {
  return {
    scheduleId: validateBridgeIdentifier(scheduleId, "schedule id"),
    enabled: validateBridgeBoolean(enabled, "schedule enabled")
  };
}

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

export function validateDocumentParseRequest(value: unknown): { path: string; include_text?: boolean } {
  const request = validatePlainBridgeBody(value, "document parse request") as DocumentParseRequest & Record<string, unknown>;
  rejectUnexpectedBridgeKeys(request, DOCUMENT_PARSE_ALLOWED_KEYS, "document parse request");
  const body: { path: string; include_text?: boolean } = {
    path: validateBridgePathValue(request.path, "document path")
  };
  const includeText = request.includeText ?? request.include_text;
  if (includeText !== undefined) {
    body.include_text = validateBridgeBoolean(includeText, "document includeText");
  }
  return body;
}

export function validateDocumentAskRequest(value: unknown): { path: string; question: string; top_k?: number } {
  const request = validatePlainBridgeBody(value, "document ask request") as DocumentAskRequest & Record<string, unknown>;
  rejectUnexpectedBridgeKeys(request, DOCUMENT_ASK_ALLOWED_KEYS, "document ask request");
  const body: { path: string; question?: string; top_k?: number } = {
    path: validateBridgePathValue(request.path, "document path")
  };
  body.question = validateBridgeStringValue(request.question, "document question", DOCUMENT_QUESTION_MAX_CHARS, {
    allowEmpty: false,
    trim: true
  });
  const topK = request.topK ?? request.top_k;
  if (topK !== undefined) {
    body.top_k = validateBridgePositiveInteger(topK, "document topK", 5, 1, 20);
  }
  return body as { path: string; question: string; top_k?: number };
}

export function validateDocumentCompareRequest(value: unknown): { paths: string[]; focus?: string } {
  const request = validatePlainBridgeBody(value, "document compare request") as DocumentCompareRequest & Record<string, unknown>;
  rejectUnexpectedBridgeKeys(request, DOCUMENT_COMPARE_ALLOWED_KEYS, "document compare request");
  const paths = validateBridgeStringArray(request.paths, "document compare paths", 2, 4096);
  if (paths.length !== 2) {
    throw new ApiRequestValidationError("document compare requires exactly two paths");
  }
  const body: { paths: string[]; focus?: string } = { paths };
  if (request.focus !== undefined && request.focus !== null && request.focus !== "") {
    body.focus = validateBridgeStringValue(request.focus, "document compare focus", DOCUMENT_FOCUS_MAX_CHARS, {
      allowEmpty: false,
      trim: true
    });
  }
  return body;
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

export function validatePermissionPolicyRelaxationRequest(value: unknown): Record<string, unknown> {
  const request = validatePlainBridgeBody(value, "permission policy confirmation request");
  const action = validateBridgeEnum<DesktopPermissionPolicyRelaxationRequest["action"]>(
    request.action,
    "permission policy action",
    PERMISSION_RELAXATION_ACTIONS
  );
  if (action === "upsert_rule") {
    return { action, rule: validatePermissionRule(request.rule) };
  }
  if (action === "delete_rule") {
    return {
      action,
      rule_id: validateBridgeIdentifier(request.ruleId ?? request.rule_id, "permission rule id")
    };
  }
  return { action, policy: validatePermissionPolicy(request.policy) };
}

export function validatePermissionRuleUpsertRequest(value: unknown): DesktopPermissionRuleUpsertRequest {
  const request = validatePlainBridgeBody(value, "permission rule upsert request");
  return {
    rule: validatePermissionRule(request.rule),
    confirmationNonce: validateOptionalConfirmationNonce(request.confirmationNonce ?? request.confirmation_nonce)
  };
}

export function validatePermissionRuleDeleteRequest(value: unknown): DesktopPermissionRuleDeleteRequest {
  const request = validatePlainBridgeBody(value, "permission rule delete request");
  return {
    ruleId: validateBridgeIdentifier(request.ruleId ?? request.rule_id, "permission rule id"),
    confirmationNonce: validateOptionalConfirmationNonce(request.confirmationNonce ?? request.confirmation_nonce)
  };
}

function validatePermissionPolicy(value: unknown): { rules?: DesktopPermissionRule[] } {
  const request = validatePlainBridgeBody(value, "permission policy");
  for (const key of Object.keys(request)) {
    if (key !== "rules") {
      throw new ApiRequestValidationError(`permission policy field is not allowed: ${key}`);
    }
  }
  if (request.rules === undefined) {
    return {};
  }
  if (!Array.isArray(request.rules) || request.rules.length > 200) {
    throw new ApiRequestValidationError("permission policy rules are invalid");
  }
  return { rules: request.rules.map((rule) => validatePermissionRule(rule)) };
}

function validatePermissionRule(value: unknown): DesktopPermissionRule {
  const request = validatePlainBridgeBody(value, "permission rule");
  for (const key of Object.keys(request)) {
    assertSafeFieldName(key, "permission rule key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (!PERMISSION_RULE_ALLOWED_KEYS.has(key)) {
      throw new ApiRequestValidationError(`permission rule field is not allowed: ${key}`);
    }
  }

  const rule: DesktopPermissionRule = {};
  if (request.id !== undefined) {
    rule.id = validateBridgeIdentifier(request.id, "permission rule id");
  }
  if (request.name !== undefined) {
    rule.name = validateBridgeStringValue(request.name, "permission rule name", 256, { allowEmpty: true, trim: true });
  }
  if (request.effect !== undefined) {
    rule.effect = validateBridgeEnum<"allow" | "deny">(request.effect, "permission rule effect", PERMISSION_EFFECTS);
  }
  if (request.tool !== undefined) {
    rule.tool = validateBridgeStringValue(request.tool, "permission rule tool", 256, { allowEmpty: true, trim: true });
  }
  if (request.tools !== undefined) {
    rule.tools = validateBridgeStringArray(request.tools, "permission rule tools", 100, 256);
  }
  if (request.path_pattern !== undefined) {
    rule.path_pattern = validateBridgeStringValue(request.path_pattern, "permission rule path pattern", 4096, {
      allowEmpty: true,
      trim: true
    });
  }
  if (request.path_patterns !== undefined) {
    rule.path_patterns = validateBridgeStringArray(request.path_patterns, "permission rule path patterns", 200, 4096);
  }
  if (request.time_window !== undefined) {
    rule.time_window = request.time_window === null ? null : validatePermissionTimeWindow(request.time_window);
  }
  if (request.time_windows !== undefined) {
    if (!Array.isArray(request.time_windows) || request.time_windows.length > 50) {
      throw new ApiRequestValidationError("permission rule time windows are invalid");
    }
    rule.time_windows = request.time_windows.map((window) => validatePermissionTimeWindow(window));
  }
  if (request.enabled !== undefined) {
    rule.enabled = validateBridgeBoolean(request.enabled, "permission rule enabled");
  }
  if (request.reason !== undefined) {
    rule.reason = validateBridgeStringValue(request.reason, "permission rule reason", 2048, { allowEmpty: true, trim: true });
  }
  return rule;
}

function validatePermissionTimeWindow(value: unknown): NonNullable<DesktopPermissionRule["time_window"]> {
  const request = validatePlainBridgeBody(value, "permission time window");
  for (const key of Object.keys(request)) {
    assertSafeFieldName(key, "permission time window key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (!PERMISSION_TIME_WINDOW_ALLOWED_KEYS.has(key)) {
      throw new ApiRequestValidationError(`permission time window field is not allowed: ${key}`);
    }
  }

  const timeWindow: NonNullable<DesktopPermissionRule["time_window"]> = {};
  if (request.days !== undefined) {
    if (!Array.isArray(request.days) || request.days.length > 31) {
      throw new ApiRequestValidationError("permission time window days are invalid");
    }
    timeWindow.days = request.days.map((day) => {
      if (typeof day === "number" && Number.isInteger(day) && day >= 0 && day <= 6) {
        return day;
      }
      return validateBridgeStringValue(day, "permission time window day", 32, { allowEmpty: false, trim: true });
    });
  }
  if (request.start !== undefined) {
    timeWindow.start = validateBridgeStringValue(request.start, "permission time window start", 16, {
      allowEmpty: false,
      trim: true
    });
  }
  if (request.end !== undefined) {
    timeWindow.end = validateBridgeStringValue(request.end, "permission time window end", 16, {
      allowEmpty: false,
      trim: true
    });
  }
  if (request.timezone !== undefined) {
    timeWindow.timezone = validateBridgeStringValue(request.timezone, "permission time window timezone", 128, {
      allowEmpty: true,
      trim: true
    });
  }
  return timeWindow;
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

function validateOptionalConfirmationNonce(value: unknown): string | undefined {
  if (value === undefined || value === null || value === "") {
    return undefined;
  }
  return validateBridgeIdentifier(value, "confirmation nonce");
}

function validateBridgeEnum<T extends string>(
  value: unknown,
  label: string,
  allowed: ReadonlySet<string>,
  defaultValue?: T
): T {
  if (value === undefined || value === null || value === "") {
    if (defaultValue !== undefined) {
      return defaultValue;
    }
    throw new ApiRequestValidationError(`${label} is required`);
  }
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  const normalized = value.trim().toLowerCase();
  if (!allowed.has(normalized)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return normalized as T;
}

function validateBridgeBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new ApiRequestValidationError(`${label} must be a boolean`);
  }
  return value;
}

function validateBridgeFiniteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) {
    throw new ApiRequestValidationError(`${label} must be a finite number`);
  }
  return value;
}

function validateBridgeStringArray(value: unknown, label: string, maxItems: number, maxChars: number): string[] {
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new ApiRequestValidationError(`${label} must be an array`);
  }
  return value.map((item, index) =>
    validateBridgeStringValue(item, `${label}[${index}]`, maxChars, { allowEmpty: false, trim: true })
  );
}

function validateBridgeStringValue(
  value: unknown,
  label: string,
  maxChars: number,
  options: { allowEmpty?: boolean; trim?: boolean } = {}
): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} must be a string`);
  }
  const result = options.trim ? value.trim() : value;
  if ((!options.allowEmpty && !result) || result.length > maxChars || result.includes("\0") || /[\u0000-\u001F\u007F]/.test(result)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return result;
}

export function validateBridgePositiveInteger(
  value: unknown,
  label: string,
  defaultValue: number,
  minimum: number,
  maximum: number
): number {
  if (value === undefined) {
    return defaultValue;
  }
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum || value > maximum) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return value;
}

function serializeApiRequestBody(request: Record<string, unknown>, method: ApiMethod): string | undefined {
  if (!Object.prototype.hasOwnProperty.call(request, "body") || request.body === undefined) {
    return undefined;
  }
  if (method === "GET") {
    throw new ApiRequestValidationError("Renderer API GET requests cannot include a body");
  }

  assertJsonSafeValue(request.body, 0, new WeakSet<object>());
  const serialized = JSON.stringify(request.body);
  if (typeof serialized !== "string") {
    throw new ApiRequestValidationError("Renderer API body must be JSON serializable");
  }
  if (utf8ByteLength(serialized) > API_REQUEST_SECURITY_LIMITS.maxBodyBytes) {
    throw new ApiRequestValidationError("Renderer API body is too large");
  }
  return serialized;
}

function assertJsonSafeValue(value: unknown, depth: number, seen: WeakSet<object>): void {
  if (depth > API_REQUEST_SECURITY_LIMITS.maxBodyDepth) {
    throw new ApiRequestValidationError("Renderer API body is too deeply nested");
  }

  if (value === null) {
    return;
  }

  if (typeof value === "string") {
    if (utf8ByteLength(value) > API_REQUEST_SECURITY_LIMITS.maxBodyStringBytes) {
      throw new ApiRequestValidationError("Renderer API body string is too large");
    }
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new ApiRequestValidationError("Renderer API body number is invalid");
    }
    return;
  }
  if (typeof value === "boolean") {
    return;
  }
  if (typeof value !== "object") {
    throw new ApiRequestValidationError("Renderer API body must be JSON serializable");
  }

  if (seen.has(value)) {
    throw new ApiRequestValidationError("Renderer API body cannot be circular");
  }
  seen.add(value);

  if (Array.isArray(value)) {
    if (value.length > API_REQUEST_SECURITY_LIMITS.maxBodyArrayItems) {
      throw new ApiRequestValidationError("Renderer API body array is too large");
    }
    for (const item of value) {
      assertJsonSafeValue(item, depth + 1, seen);
    }
    seen.delete(value);
    return;
  }

  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError("Renderer API body must contain plain JSON objects");
  }

  const keys = Object.keys(value);
  if (keys.length > API_REQUEST_SECURITY_LIMITS.maxBodyObjectKeys) {
    throw new ApiRequestValidationError("Renderer API body object has too many keys");
  }
  for (const key of keys) {
    assertSafeFieldName(key, "Renderer API body key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    assertJsonSafeValue(value[key], depth + 1, seen);
  }
  seen.delete(value);
}

function assertSafeFieldName(name: string, label: string, maxChars: number): void {
  if (!name || name.length > maxChars || /[\u0000-\u001F\u007F]/.test(name) || API_REQUEST_RESERVED_KEYS.has(name)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
}

export function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function utf8ByteLength(value: string): number {
  return Buffer.byteLength(value, "utf8");
}
