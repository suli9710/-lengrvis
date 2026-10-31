import { describe, expect, it } from "vitest";

import {
  allowedDirectoriesForSettings,
  mapSettings,
  mergeDesktopOnlySettings,
  settingsPatchFor
} from "./settingsMappers";

describe("settings mapper contracts", () => {
  it("maps backend settings defaults and keeps only persistable MCP servers", () => {
    const settings = mapSettings({
      base_url: "http://127.0.0.1:8000",
      mode: "hybrid",
      permission_mode: "trusted_edits",
      wire_api: "responses",
      requires_openai_auth: false,
      allowed_directories: ["C:\\Users\\Suli\\Workspace"],
      onnx_execution_provider: "directml",
      mcp_servers: [
        { name: "Remote", url: " https://example.test/mcp ", enabled: true },
        { name: "Local", command: "node", args: ["server.js"], transport: "stdio" },
        { name: "Blank", url: "  ", enabled: true }
      ]
    });

    expect(settings).toMatchObject({
      apiBaseUrl: "http://127.0.0.1:8000",
      workspaceRoot: "C:\\Users\\Suli\\Workspace",
      allowedDirectories: ["C:\\Users\\Suli\\Workspace"],
      mode: "hybrid",
      permissionMode: "trusted_edits",
      wireApi: "responses",
      requiresOpenAiAuth: false,
      onnxExecutionProvider: "DirectML"
    });
    expect(settings.mcpServers).toEqual([
      { name: "Remote", url: "https://example.test/mcp", command: undefined, args: undefined, transport: undefined, auth: undefined, enabled: true },
      { name: "Local", url: "", command: "node", args: ["server.js"], transport: "stdio", auth: undefined, enabled: true }
    ]);
  });

  it("builds sparse settings patches and preserves workspace-root ordering", () => {
    const baseline = mapSettings({
      base_url: "http://127.0.0.1:8000",
      allowed_directories: ["C:\\Old", "D:\\Shared"],
      mcp_servers: [{ name: "Remote", url: "https://old.example/mcp", enabled: true }]
    });
    const next = {
      ...baseline,
      workspaceRoot: "C:\\New",
      allowedDirectories: ["C:\\Old", "D:\\Shared"],
      temperature: baseline.temperature + 0.1,
      mcpServers: [
        { name: "Remote", url: "https://new.example/mcp", enabled: true },
        { name: "Blank", url: "", enabled: true }
      ]
    };

    expect(allowedDirectoriesForSettings(next, baseline)).toEqual(["C:\\New", "D:\\Shared"]);
    expect(settingsPatchFor(next, baseline)).toEqual({
      temperature: baseline.temperature + 0.1,
      allowed_directories: ["C:\\New", "D:\\Shared"],
      mcp_servers: [{ name: "Remote", url: "https://new.example/mcp", enabled: true }]
    });
  });

  it("keeps desktop-only settings from the previous local draft", () => {
    const backendSettings = mapSettings({ mode: "efficiency" });
    const previous = {
      ...backendSettings,
      autoStartBackend: true,
      telemetryEnabled: true,
      compactMode: true,
      theme: "dark" as const
    };

    expect(mergeDesktopOnlySettings(backendSettings, previous)).toMatchObject({
      autoStartBackend: true,
      telemetryEnabled: true,
      compactMode: true,
      theme: "dark"
    });
  });
});

// Independent wire-key expectations guard the compact scalar mapping table.
describe("scalar settings patch wire compatibility", () => {
  const fields = [
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
  ["model_auto_compact_token_limit", "modelAutoCompactTokenLimit"],
  ["allow_browser_network", "allowBrowserNetwork"],
  ["remote_desktop_enabled", "remoteDesktopEnabled"],
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
] as const;
  it.each(fields)("maps %s from %s without sending unchanged scalar fields", (wireKey, settingsKey) => {
    const baseline = mapSettings({});
    const current = baseline[settingsKey];
    const value = typeof current === "boolean" ? !current : typeof current === "number" ? current + 1 : "changed-value";
    const changed = { ...baseline, [settingsKey]: value };
    expect(settingsPatchFor(changed, baseline)).toEqual({ [wireKey]: value });
    expect(settingsPatchFor(changed, null)[wireKey]).toBe(value);
    expect(settingsPatchFor(changed, changed)).toEqual({});
  });
  it("retains strict inequality semantics for NaN scalar settings", () => {
    const settings = { ...mapSettings({}), temperature: NaN };
    expect(settingsPatchFor(settings, settings)).toEqual({ temperature: NaN });
  });
});
