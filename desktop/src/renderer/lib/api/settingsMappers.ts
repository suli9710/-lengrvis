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

  add("provider_name", settings.providerName, settings.providerName !== previous.providerName);
  add("base_url", settings.apiBaseUrl, settings.apiBaseUrl !== previous.apiBaseUrl);
  add("model", settings.model, settings.model !== previous.model);
  add("review_model", settings.reviewModel, settings.reviewModel !== previous.reviewModel);
  add("wire_api", settings.wireApi, settings.wireApi !== previous.wireApi);
  add("requires_openai_auth", settings.requiresOpenAiAuth, settings.requiresOpenAiAuth !== previous.requiresOpenAiAuth);
  add("model_reasoning_effort", settings.modelReasoningEffort, settings.modelReasoningEffort !== previous.modelReasoningEffort);
  add("disable_response_storage", settings.disableResponseStorage, settings.disableResponseStorage !== previous.disableResponseStorage);
  add("temperature", settings.temperature, settings.temperature !== previous.temperature);
  add("max_tokens", settings.maxTokens, settings.maxTokens !== previous.maxTokens);
  add("timeout", settings.timeout, settings.timeout !== previous.timeout);
  add("llm_api_max_retries", settings.llmApiMaxRetries, settings.llmApiMaxRetries !== previous.llmApiMaxRetries);
  add(
    "llm_api_retry_backoff_seconds",
    settings.llmApiRetryBackoffSeconds,
    settings.llmApiRetryBackoffSeconds !== previous.llmApiRetryBackoffSeconds
  );
  add(
    "llm_api_circuit_failure_threshold",
    settings.llmApiCircuitFailureThreshold,
    settings.llmApiCircuitFailureThreshold !== previous.llmApiCircuitFailureThreshold
  );
  add(
    "llm_api_circuit_cooldown_seconds",
    settings.llmApiCircuitCooldownSeconds,
    settings.llmApiCircuitCooldownSeconds !== previous.llmApiCircuitCooldownSeconds
  );
  add("model_context_window", settings.modelContextWindow, settings.modelContextWindow !== previous.modelContextWindow);
  add(
    "model_auto_compact_token_limit",
    settings.modelAutoCompactTokenLimit,
    settings.modelAutoCompactTokenLimit !== previous.modelAutoCompactTokenLimit
  );
  const allowedDirectories = allowedDirectoriesForSettings(settings, previous);
  const previousAllowedDirectories = allowedDirectoriesForSettings(previous);
  add("allowed_directories", allowedDirectories, !sameStringArray(allowedDirectories, previousAllowedDirectories));
  add("allow_browser_network", settings.allowBrowserNetwork, settings.allowBrowserNetwork !== previous.allowBrowserNetwork);
  add("remote_desktop_enabled", settings.remoteDesktopEnabled, settings.remoteDesktopEnabled !== previous.remoteDesktopEnabled);
  add("app_allowlist", settings.appAllowlist, !sameStringArray(settings.appAllowlist, previous.appAllowlist));
  add("browser_max_page_bytes", settings.browserMaxPageBytes, settings.browserMaxPageBytes !== previous.browserMaxPageBytes);
  add("browser_screenshot_dir", settings.browserScreenshotDir, settings.browserScreenshotDir !== previous.browserScreenshotDir);
  add("onnx_model_path", settings.onnxModelPath, settings.onnxModelPath !== previous.onnxModelPath);
  add("onnx_execution_provider", settings.onnxExecutionProvider, settings.onnxExecutionProvider !== previous.onnxExecutionProvider);
  add("onnx_provider_preference", settings.onnxProviderPreference, settings.onnxProviderPreference !== previous.onnxProviderPreference);
  add("onnx_directml_device_id", settings.onnxDirectmlDeviceId, settings.onnxDirectmlDeviceId !== previous.onnxDirectmlDeviceId);
  add("onnx_openvino_device", settings.onnxOpenvinoDevice, settings.onnxOpenvinoDevice !== previous.onnxOpenvinoDevice);
  add("onnx_openvino_cache_dir", settings.onnxOpenvinoCacheDir, settings.onnxOpenvinoCacheDir !== previous.onnxOpenvinoCacheDir);
  add("onnx_warm_on_startup", settings.onnxWarmOnStartup, settings.onnxWarmOnStartup !== previous.onnxWarmOnStartup);
  add("onnx_model_family", settings.onnxModelFamily, settings.onnxModelFamily !== previous.onnxModelFamily);
  add("embedding_backend", settings.onnxEmbeddingBackend, settings.onnxEmbeddingBackend !== previous.onnxEmbeddingBackend);
  add("onnx_embedding_model_path", settings.onnxEmbeddingModelPath, settings.onnxEmbeddingModelPath !== previous.onnxEmbeddingModelPath);
  add(
    "onnx_embedding_execution_provider",
    settings.onnxEmbeddingExecutionProvider,
    settings.onnxEmbeddingExecutionProvider !== previous.onnxEmbeddingExecutionProvider
  );
  add("onnx_embedding_model_id", settings.onnxEmbeddingModelId, settings.onnxEmbeddingModelId !== previous.onnxEmbeddingModelId);
  add(
    "onnx_embedding_max_batch_size",
    settings.onnxEmbeddingMaxBatchSize,
    settings.onnxEmbeddingMaxBatchSize !== previous.onnxEmbeddingMaxBatchSize
  );
  add("image_embedding_backend", settings.imageEmbeddingBackend, settings.imageEmbeddingBackend !== previous.imageEmbeddingBackend);
  add(
    "onnx_image_embedding_model_path",
    settings.onnxImageEmbeddingModelPath,
    settings.onnxImageEmbeddingModelPath !== previous.onnxImageEmbeddingModelPath
  );
  add(
    "onnx_image_embedding_execution_provider",
    settings.onnxImageEmbeddingExecutionProvider,
    settings.onnxImageEmbeddingExecutionProvider !== previous.onnxImageEmbeddingExecutionProvider
  );
  add(
    "onnx_image_embedding_model_id",
    settings.onnxImageEmbeddingModelId,
    settings.onnxImageEmbeddingModelId !== previous.onnxImageEmbeddingModelId
  );
  add(
    "onnx_image_embedding_max_batch_size",
    settings.onnxImageEmbeddingMaxBatchSize,
    settings.onnxImageEmbeddingMaxBatchSize !== previous.onnxImageEmbeddingMaxBatchSize
  );
  add("ocr_backend", settings.ocrBackend, settings.ocrBackend !== previous.ocrBackend);
  add("ocr_execution_provider", settings.ocrExecutionProvider, settings.ocrExecutionProvider !== previous.ocrExecutionProvider);
  add("ocr_openvino_model_dir", settings.ocrOpenvinoModelDir, settings.ocrOpenvinoModelDir !== previous.ocrOpenvinoModelDir);
  add("ocr_openvino_device", settings.ocrOpenvinoDevice, settings.ocrOpenvinoDevice !== previous.ocrOpenvinoDevice);
  add("ocr_lang", settings.ocrLang, settings.ocrLang !== previous.ocrLang);
  add("ocr_min_confidence", settings.ocrMinConfidence, settings.ocrMinConfidence !== previous.ocrMinConfidence);
  add("ocr_batch_size", settings.ocrBatchSize, settings.ocrBatchSize !== previous.ocrBatchSize);
  add("mode", settings.mode, settings.mode !== previous.mode);
  add("permission_mode", settings.permissionMode, settings.permissionMode !== previous.permissionMode);
  add("allow_cloud_context", settings.allowCloudContext, settings.allowCloudContext !== previous.allowCloudContext);
  add(
    "allow_file_content_upload",
    settings.allowFileContentUpload,
    settings.allowFileContentUpload !== previous.allowFileContentUpload
  );
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
