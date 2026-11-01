import type { AppSettings } from "../../../shared/settingsTypes";
import type { BackendSettings } from "./settingsBackendTypes";
import { normalizeExecutionProvider } from "./hardwareAccelerationMappers";

export function settingsPatchFor(settings: AppSettings, baseline: AppSettings | null): Partial<BackendSettings> {
  const body: Partial<BackendSettings> = {};
  const previous = baseline ?? settings;
  const add = <K extends keyof BackendSettings>(key: K, value: BackendSettings[K], changed: boolean) => {
    if (baseline === null || changed) {
      body[key] = value;
    }
  };

  for (const [backendKey, settingsKey] of [
    ["provider_name", "providerName"],
    ["base_url", "apiBaseUrl"],
    ["model", "model"],
    ["review_model", "reviewModel"],
    ["wire_api", "wireApi"],
    ["requires_openai_auth", "requiresOpenAiAuth"],
    ["model_reasoning_effort", "modelReasoningEffort"],
    ["disable_response_storage", "disableResponseStorage"],
    ["temperature", "temperature"],
    ["max_tokens", "maxTokens"],
    ["timeout", "timeout"],
    ["llm_api_max_retries", "llmApiMaxRetries"],
    ["llm_api_retry_backoff_seconds", "llmApiRetryBackoffSeconds"],
    ["llm_api_circuit_failure_threshold", "llmApiCircuitFailureThreshold"],
    ["llm_api_circuit_cooldown_seconds", "llmApiCircuitCooldownSeconds"],
    ["model_context_window", "modelContextWindow"],
    ["model_auto_compact_token_limit", "modelAutoCompactTokenLimit"]
  ] as const) {
    add(backendKey, settings[settingsKey], settings[settingsKey] !== previous[settingsKey]);
  }
  const allowedDirectories = allowedDirectoriesForSettings(settings, previous);
  const previousAllowedDirectories = allowedDirectoriesForSettings(previous);
  add("allowed_directories", allowedDirectories, !sameStringArray(allowedDirectories, previousAllowedDirectories));
  for (const [backendKey, settingsKey] of [
    ["allow_browser_network", "allowBrowserNetwork"],
    ["remote_desktop_enabled", "remoteDesktopEnabled"]
  ] as const) {
    add(backendKey, settings[settingsKey], settings[settingsKey] !== previous[settingsKey]);
  }
  add("app_allowlist", settings.appAllowlist, !sameStringArray(settings.appAllowlist, previous.appAllowlist));
  for (const [backendKey, settingsKey] of [
    ["browser_max_page_bytes", "browserMaxPageBytes"],
    ["browser_screenshot_dir", "browserScreenshotDir"],
    ["onnx_model_path", "onnxModelPath"],
    ["onnx_execution_provider", "onnxExecutionProvider"],
    ["onnx_provider_preference", "onnxProviderPreference"],
    ["onnx_directml_device_id", "onnxDirectmlDeviceId"],
    ["onnx_openvino_device", "onnxOpenvinoDevice"],
    ["onnx_openvino_cache_dir", "onnxOpenvinoCacheDir"],
    ["onnx_warm_on_startup", "onnxWarmOnStartup"],
    ["onnx_model_family", "onnxModelFamily"],
    ["embedding_backend", "onnxEmbeddingBackend"],
    ["onnx_embedding_model_path", "onnxEmbeddingModelPath"],
    ["onnx_embedding_execution_provider", "onnxEmbeddingExecutionProvider"],
    ["onnx_embedding_model_id", "onnxEmbeddingModelId"],
    ["onnx_embedding_max_batch_size", "onnxEmbeddingMaxBatchSize"],
    ["image_embedding_backend", "imageEmbeddingBackend"],
    ["onnx_image_embedding_model_path", "onnxImageEmbeddingModelPath"],
    ["onnx_image_embedding_execution_provider", "onnxImageEmbeddingExecutionProvider"],
    ["onnx_image_embedding_model_id", "onnxImageEmbeddingModelId"],
    ["onnx_image_embedding_max_batch_size", "onnxImageEmbeddingMaxBatchSize"],
    ["ocr_backend", "ocrBackend"],
    ["ocr_execution_provider", "ocrExecutionProvider"],
    ["ocr_openvino_model_dir", "ocrOpenvinoModelDir"],
    ["ocr_openvino_device", "ocrOpenvinoDevice"],
    ["ocr_lang", "ocrLang"],
    ["ocr_min_confidence", "ocrMinConfidence"],
    ["ocr_batch_size", "ocrBatchSize"],
    ["mode", "mode"],
    ["permission_mode", "permissionMode"],
    ["allow_cloud_context", "allowCloudContext"],
    ["allow_file_content_upload", "allowFileContentUpload"]
  ] as const) {
    add(backendKey, settings[settingsKey], settings[settingsKey] !== previous[settingsKey]);
  }
  const mcpServers = settings.mcpServers.map(mapMcpServerForBackend).filter(hasPersistableMcpServerTarget);
  const previousMcpServers = previous.mcpServers.map(mapMcpServerForBackend).filter(hasPersistableMcpServerTarget);
  add("mcp_servers", mcpServers, JSON.stringify(mcpServers) !== JSON.stringify(previousMcpServers));

  return body;
}

export function mergeDesktopOnlySettings(settings: AppSettings, source: AppSettings | null): AppSettings {
  if (!source) return settings;
  return {
    ...settings,
    autoStartBackend: source.autoStartBackend,
    telemetryEnabled: source.telemetryEnabled,
    compactMode: source.compactMode,
    theme: source.theme
  };
}

export function allowedDirectoriesForSettings(settings: AppSettings, baseline?: AppSettings | null): string[] {
  const directories = settings.allowedDirectories?.length
    ? settings.allowedDirectories.filter(Boolean)
    : settings.workspaceRoot
      ? [settings.workspaceRoot]
      : [];
  if (!settings.workspaceRoot) return directories;
  if (!directories.length) return [settings.workspaceRoot];
  if (directories[0] === settings.workspaceRoot) return directories;

  const baselinePrimary = baseline?.workspaceRoot || baseline?.allowedDirectories?.[0] || directories[0];
  if (settings.workspaceRoot !== baselinePrimary) {
    return [settings.workspaceRoot, ...directories.slice(1).filter((directory) => directory !== settings.workspaceRoot)];
  }

  return [settings.workspaceRoot, ...directories.filter((directory) => directory !== settings.workspaceRoot)];
}

export function sameStringArray(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

export function mapMcpServerForBackend(server: AppSettings["mcpServers"][number]): NonNullable<BackendSettings["mcp_servers"]>[number] {
  const result: NonNullable<BackendSettings["mcp_servers"]>[number] = {
    ...server,
    name: String(server.name ?? "").trim(),
    url: String(server.url ?? "").trim(),
    enabled: server.enabled !== false
  };
  if (server.command !== undefined) result.command = String(server.command);
  if (Array.isArray(server.args)) result.args = server.args.map(String);
  if (server.transport !== undefined) result.transport = String(server.transport);
  if (server.auth && typeof server.auth === "object") result.auth = server.auth;
  return result;
}

export function hasPersistableMcpServerTarget(server: NonNullable<BackendSettings["mcp_servers"]>[number]): boolean {
  return Boolean(String(server.url ?? "").trim() || String(server.command ?? "").trim());
}

export function mapSettings(settings: BackendSettings): AppSettings {
  const rawMode = (settings.mode ?? "efficiency").toLowerCase();
  const mode: AppSettings["mode"] = rawMode === "efficiency" || rawMode === "hybrid" ? rawMode : "privacy";
  const mcpServers = (settings.mcp_servers ?? [])
    .map((server) => ({
      ...server,
      id: typeof server?.id === "string" ? server.id : undefined,
      name: String(server?.name ?? "").trim(),
      url: String(server?.url ?? "").trim(),
      command: typeof server?.command === "string" ? server.command : undefined,
      args: Array.isArray(server?.args) ? server.args.map(String) : undefined,
      transport: typeof server?.transport === "string" ? server.transport : undefined,
      auth: server?.auth && typeof server.auth === "object" ? server.auth : undefined,
      enabled: server?.enabled !== false
    }))
    .filter((server) => server.url.length > 0 || Boolean(server.command));
  const allowedDirectories = settings.allowed_directories ?? [];
  return {
    apiBaseUrl: settings.base_url ?? "http://127.0.0.1:8000",
    autoStartBackend: false,
    telemetryEnabled: false,
    compactMode: false,
    theme: "system",
    providerName: settings.provider_name ?? "openai_compatible",
    model: settings.model ?? "gpt-4o-mini",
    reviewModel: settings.review_model ?? "",
    wireApi: settings.wire_api === "responses" ? "responses" : "chat_completions",
    requiresOpenAiAuth: settings.requires_openai_auth !== false,
    modelReasoningEffort: settings.model_reasoning_effort ?? "medium",
    disableResponseStorage: Boolean(settings.disable_response_storage),
    temperature: Number(settings.temperature ?? 0.2),
    maxTokens: Number(settings.max_tokens ?? 1600),
    timeout: Number(settings.timeout ?? 30),
    llmApiMaxRetries: Number(settings.llm_api_max_retries ?? 2),
    llmApiRetryBackoffSeconds: Number(settings.llm_api_retry_backoff_seconds ?? 0.25),
    llmApiCircuitFailureThreshold: Number(settings.llm_api_circuit_failure_threshold ?? 5),
    llmApiCircuitCooldownSeconds: Number(settings.llm_api_circuit_cooldown_seconds ?? 30),
    modelContextWindow: Number(settings.model_context_window ?? 128000),
    modelAutoCompactTokenLimit: Number(settings.model_auto_compact_token_limit ?? 96000),
    workspaceRoot: allowedDirectories[0] ?? "",
    allowedDirectories,
    allowBrowserNetwork: Boolean(settings.allow_browser_network),
    remoteDesktopEnabled: Boolean(settings.remote_desktop_enabled),
    appAllowlist: settings.app_allowlist ?? [],
    browserMaxPageBytes: settings.browser_max_page_bytes ?? 250000,
    browserScreenshotDir: settings.browser_screenshot_dir ?? "",
    onnxModelPath: settings.onnx_model_path ?? "",
    onnxExecutionProvider: normalizeExecutionProvider(settings.onnx_execution_provider ?? ""),
    onnxProviderPreference: settings.onnx_provider_preference ?? "winml,directml,openvino,cpu",
    onnxDirectmlDeviceId: settings.onnx_directml_device_id ?? "",
    onnxOpenvinoDevice: settings.onnx_openvino_device ?? "AUTO",
    onnxOpenvinoCacheDir: settings.onnx_openvino_cache_dir ?? "",
    onnxWarmOnStartup: Boolean(settings.onnx_warm_on_startup),
    onnxModelFamily: settings.onnx_model_family ?? "",
    onnxEmbeddingBackend: settings.embedding_backend ?? "auto",
    onnxEmbeddingModelPath: settings.onnx_embedding_model_path ?? "",
    onnxEmbeddingExecutionProvider: settings.onnx_embedding_execution_provider ?? "",
    onnxEmbeddingModelId: settings.onnx_embedding_model_id ?? "intfloat/multilingual-e5-small",
    onnxEmbeddingMaxBatchSize: Number(settings.onnx_embedding_max_batch_size ?? 32),
    imageEmbeddingBackend: settings.image_embedding_backend ?? "auto",
    onnxImageEmbeddingModelPath: settings.onnx_image_embedding_model_path ?? "",
    onnxImageEmbeddingExecutionProvider: settings.onnx_image_embedding_execution_provider ?? "",
    onnxImageEmbeddingModelId: settings.onnx_image_embedding_model_id ?? "openai/clip-vit-base-patch32",
    onnxImageEmbeddingMaxBatchSize: Number(settings.onnx_image_embedding_max_batch_size ?? 8),
    ocrBackend: settings.ocr_backend ?? "auto",
    ocrExecutionProvider: settings.ocr_execution_provider ?? "",
    ocrOpenvinoModelDir: settings.ocr_openvino_model_dir ?? "",
    ocrOpenvinoDevice: settings.ocr_openvino_device ?? "AUTO",
    ocrLang: settings.ocr_lang ?? "multi",
    ocrMinConfidence: Number(settings.ocr_min_confidence ?? 0),
    ocrBatchSize: Number(settings.ocr_batch_size ?? 1),
    mode,
    permissionMode: normalizePermissionMode(settings.permission_mode),
    allowCloudContext: Boolean(settings.allow_cloud_context),
    allowFileContentUpload: Boolean(settings.allow_file_content_upload),
    mcpServers
  };
}

export function normalizePermissionMode(value?: string): AppSettings["permissionMode"] {
  const normalized = String(value ?? "default").toLowerCase();
  if (normalized === "plan" || normalized === "trusted_edits" || normalized === "auto_review" || normalized === "dont_ask") {
    return normalized;
  }
  return "default";
}
