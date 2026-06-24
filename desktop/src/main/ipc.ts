import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent, type OpenDialogOptions } from "electron";
import { existsSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import {
  API_REQUEST_ALLOWED_KEYS,
  API_REQUEST_DENIED_EXACT_PATHS,
  API_REQUEST_DENIED_METHOD_PATHS,
  API_REQUEST_DENIED_PATH_PREFIXES,
  API_REQUEST_SECURITY_LIMITS,
  IPC_CHANNELS
} from "../shared/ipc";
import type {
  ApiMethod,
  ApiQueryValue,
  ApiRequest,
  ApiResponse,
  BackendStatus,
  DocumentAskRequest,
  DocumentCompareRequest,
  DocumentParseRequest,
  DesktopPermissionPolicyRelaxationRequest,
  DesktopPermissionRule,
  DesktopPermissionRuleDeleteRequest,
  DesktopPermissionRuleUpsertRequest,
  DesktopRunStartRequest,
  DesktopSettingsPatch,
  MobilePairingRemoteInputGrantRequest,
  MobilePairingRevokeRemoteInputGrantRequest
} from "../shared/types";
import { assertLoopbackBackendUrl } from "./backendUrl";
import type { BackendProcessManager } from "./backendProcess";
import { pathToFileURL } from "node:url";

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_REMOTE_INPUT_GRANT_TTL_SECONDS = 300;
const ALLOWED_API_METHODS = new Set<ApiMethod>(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const ALLOWED_EXTERNAL_PROTOCOLS = new Set(["https:", "mailto:"]);
const EXTERNAL_URL_MAX_CHARS = 2048;
const MAILTO_DENIED_QUERY_KEYS = new Set(["bcc", "body", "cc"]);
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001F\u007F]/;
const PERCENT_ENCODED_CONTROL_CHARACTER_PATTERN = /%(?:0[0-9a-f]|1[0-9a-f]|7f)/i;
const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";
const DOCUMENT_QUESTION_MAX_CHARS = 8_000;
const DOCUMENT_FOCUS_MAX_CHARS = 4_000;
const API_REQUEST_ALLOWED_KEY_SET = new Set<string>(API_REQUEST_ALLOWED_KEYS);
const API_REQUEST_DENIED_EXACT_PATH_SET = new Set<string>(API_REQUEST_DENIED_EXACT_PATHS);
const API_REQUEST_DENIED_METHOD_PATH_RULES = API_REQUEST_DENIED_METHOD_PATHS;
const API_REQUEST_RESERVED_KEYS = new Set(["__proto__", "constructor", "prototype"]);
const DOCUMENT_PARSE_ALLOWED_KEYS = new Set(["path", "includeText", "include_text"]);
const DOCUMENT_ASK_ALLOWED_KEYS = new Set(["path", "question", "topK", "top_k"]);
const DOCUMENT_COMPARE_ALLOWED_KEYS = new Set(["paths", "focus"]);
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

class ApiRequestValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiRequestValidationError";
  }
}

interface ValidatedApiRequest {
  endpoint: string;
  method: ApiMethod;
  query?: Record<string, Exclude<ApiQueryValue, null | undefined>>;
  serializedBody?: string;
  timeoutMs: number;
  abortGroup?: string;
}

const apiInflightGroups = new Map<string, AbortController>();

function abortInflightApiGroup(abortGroup: string): void {
  apiInflightGroups.get(abortGroup)?.abort();
  apiInflightGroups.delete(abortGroup);
}

function resolveInflightGroupSignal(abortGroup: string | undefined): AbortSignal | undefined {
  if (!abortGroup) {
    return undefined;
  }
  let controller = apiInflightGroups.get(abortGroup);
  if (!controller || controller.signal.aborted) {
    controller = new AbortController();
    apiInflightGroups.set(abortGroup, controller);
  }
  return controller.signal;
}

function mergeAbortSignals(signals: AbortSignal[]): AbortSignal {
  const controller = new AbortController();
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort();
      return controller.signal;
    }
    signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  return controller.signal;
}

interface ApiRequestValidationOptions {
  allowDeniedDesktopBridgePath?: boolean;
}

export function registerIpcHandlers(backend: BackendProcessManager): void {
  const documentPathGrants = new Set<string>();
  const revealPathGrants = new Set<string>();

  ipcMain.handle(IPC_CHANNELS.backendStatus, (event) => {
    assertTrustedRenderer(event);
    return backend.getStatus();
  });
  ipcMain.handle(IPC_CHANNELS.backendStart, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm backend start",
      message: "Start the Lengrvis backend service?",
      detail: "This starts the local agent service and makes configured tools available to the desktop app."
    });
    return backend.start();
  });
  ipcMain.handle(IPC_CHANNELS.backendStop, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm backend stop",
      message: "Stop the Lengrvis backend service?",
      detail: "Active tasks and desktop integrations may be interrupted."
    });
    return backend.stop();
  });
  ipcMain.handle(IPC_CHANNELS.backendForeground, (event) => {
    assertTrustedRenderer(event);
    return backend.enterForeground();
  });
  ipcMain.handle(IPC_CHANNELS.backendBackground, (event) => {
    assertTrustedRenderer(event);
    return backend.enterBackground();
  });

  ipcMain.handle(IPC_CHANNELS.openExternal, async (event, url: string) => {
    assertTrustedRenderer(event);
    await openSafeExternalUrl(url);
  });

  ipcMain.handle(IPC_CHANNELS.getFileIcon, async (event, filePath: string) => {
    assertTrustedRenderer(event);
    return getFileIconDataUrl(filePath, { documentPathGrants, revealPathGrants });
  });

  ipcMain.handle(IPC_CHANNELS.showItemInFolder, async (event, filePath: unknown) => {
    assertTrustedRenderer(event);
    return showItemInFolder(validateBridgePathValue(filePath, "file path to reveal"), {
      documentPathGrants,
      revealPathGrants
    });
  });

  ipcMain.handle(IPC_CHANNELS.chooseDirectory, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "选择文件夹",
      properties: ["openDirectory", "createDirectory"]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.chooseDocument, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "选择文档",
      properties: ["openFile"],
      filters: [
        {
          name: "可读取文档",
          extensions: [
            "pdf",
            "docx",
            "txt",
            "md",
            "markdown",
            "log",
            "rst",
            "json",
            "yaml",
            "yml",
            "py",
            "ts",
            "tsx",
            "js",
            "csv",
            "xlsx",
            "pptx",
            "html",
            "htm",
            "png",
            "jpg",
            "jpeg",
            "webp",
            "bmp",
            "tif",
            "tiff"
          ]
        },
        { name: "所有文件", extensions: ["*"] }
      ]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    const picked = result.canceled ? null : result.filePaths[0] ?? null;
    if (picked) {
      rememberDocumentPathGrant(documentPathGrants, picked);
    }
    return picked;
  });

  ipcMain.handle(IPC_CHANNELS.knownFolders, (event) => {
    assertTrustedRenderer(event);
    return {
      desktop: app.getPath("desktop"),
      downloads: app.getPath("downloads"),
      documents: app.getPath("documents"),
      pictures: app.getPath("pictures")
    };
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillDirectory, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill package directory",
      properties: ["openDirectory"]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillZip, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill zip package",
      properties: ["openFile"],
      filters: [{ name: "Skill packages", extensions: ["zip"] }]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.apiRequest, async (event, request: ApiRequest) => {
    assertTrustedRenderer(event);
    if (isRendererTaskSubmissionRequest(request)) {
      const backendNotReady = await ensureBackendReadyForRendererSubmission(backend);
      if (backendNotReady) {
        return backendNotReady;
      }
    }
    return proxyApiRequest(backend.getBaseUrl(), request, backend.getDesktopApiToken());
  });

  ipcMain.handle(IPC_CHANNELS.apiAbortInflight, async (event, abortGroup: unknown) => {
    assertTrustedRenderer(event);
    abortInflightApiGroup(validateApiAbortGroup(abortGroup));
  });

  ipcMain.handle(IPC_CHANNELS.commandsExecute, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateCommandExecuteRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm command",
      message: "Run this desktop command?",
      detail: `Command: ${body.name}\n\nCommands may change settings or invoke local agent capabilities.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/commands/execute",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.taskRollback, async (event, taskId: unknown) => {
    assertTrustedRenderer(event);
    const safeTaskId = validateBridgeIdentifier(taskId, "task id");
    await confirmNativeDesktopAction(event, {
      title: "Confirm task rollback",
      message: "Roll back this task?",
      detail: `Task id: ${safeTaskId}\n\nReview the rollback preview before confirming. Rollback replays recorded file recovery steps and may move or delete files inside authorized directories.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/tasks/${encodeURIComponent(safeTaskId)}/rollback`,
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.cleanupExecute, async (event, body: unknown) => {
    assertTrustedRenderer(event);
    const safeBody = validatePlainBridgeBody(body, "cleanup execute request");
    await confirmNativeDesktopAction(event, {
      title: "Confirm cleanup execution",
      message: "Run the cleanup plan?",
      detail: "This may move files to the recycle bin or apply other approved cleanup actions. Review the cleanup preview before continuing."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/files/cleanup/execute",
      method: "POST",
      body: safeBody
    });
  });

  ipcMain.handle(IPC_CHANNELS.cleanupRollback, async (event, body: unknown) => {
    assertTrustedRenderer(event);
    const safeBody = validatePlainBridgeBody(body, "cleanup rollback request");
    await confirmNativeDesktopAction(event, {
      title: "Confirm cleanup rollback",
      message: "Roll back this cleanup execution?",
      detail: "Rollback may move recovered files and replace current filesystem entries inside authorized directories."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/files/cleanup/rollback",
      method: "POST",
      body: safeBody
    });
  });

  ipcMain.handle(IPC_CHANNELS.skillsImport, async (event, packagePath: unknown) => {
    assertTrustedRenderer(event);
    const safePackagePath = validateBridgePathValue(packagePath, "skill package path");
    // Skill import reads and registers code/tools from an arbitrary filesystem
    // path. Require an explicit native confirmation that shows the path so a
    // compromised renderer cannot silently import from an attacker-chosen location.
    await confirmNativeDesktopAction(event, {
      title: "Confirm skill import",
      message: "Import this skill package?",
      detail: `Path: ${safePackagePath}\n\nOnly import skill packages you trust. Skills can register tools and run packaged code under the app's permissions.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/skills/import",
      method: "POST",
      body: { path: safePackagePath }
    });
  });

  ipcMain.handle(IPC_CHANNELS.skillsRefresh, async (event) => {
    assertTrustedRenderer(event);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/skills/refresh",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.localModelInstall, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateOptionalModelRequest(request, "local model install request");
    await confirmNativeDesktopAction(event, {
      title: "Confirm local model installation",
      message: "Install the local AI model?",
      detail: `Model: ${body.model ?? "recommended default"}\n\nThis may download a large package and use significant disk space.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/install-local-model",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.ollamaInstall, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm Ollama installation",
      message: "Install the local AI runtime?",
      detail: "This may download and install Ollama on this computer."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/ollama/install",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.ollamaPull, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateOptionalModelRequest(request ?? {}, "Ollama pull request");
    await confirmNativeDesktopAction(event, {
      title: "Confirm model download",
      message: "Download this Ollama model?",
      detail: `Model: ${body.model ?? "recommended default"}\n\nModel downloads can use substantial network bandwidth and disk space.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/ollama/pull",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.ollamaStart, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm local AI start",
      message: "Start the Ollama service?",
      detail: "This starts a local background process that can load AI models and use system resources."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/ollama/start",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.runsStart, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const backendNotReady = await ensureBackendReadyForRendererSubmission(backend);
    if (backendNotReady) {
      return backendNotReady;
    }
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/runs",
      method: "POST",
      body: validateRunStartRequest(request)
    });
  });

  ipcMain.handle(IPC_CHANNELS.systemDiagnosticsExport, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm diagnostics export",
      message: "Create a local diagnostics package?",
      detail: "The package is saved locally and is not sent automatically. Review it before sharing because it may contain device and environment metadata."
    });
    const response = await proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/system/diagnostics/export",
      method: "POST"
    });
    rememberRevealPathFromApiResponse(revealPathGrants, response);
    return response;
  });

  ipcMain.handle(IPC_CHANNELS.documentsParse, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateDocumentParseRequest(request);
    await ensureDocumentReadGrant(event, documentPathGrants, [body.path]);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/documents/parse",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.documentsAsk, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateDocumentAskRequest(request);
    await ensureDocumentReadGrant(event, documentPathGrants, body.path ? [body.path] : []);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/documents/ask",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.documentsCompare, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateDocumentCompareRequest(request);
    await ensureDocumentReadGrant(event, documentPathGrants, body.paths);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/documents/compare",
      method: "POST",
      body
    });
  });

  ipcMain.handle(IPC_CHANNELS.settingsConfirmSensitiveChange, async (event, patch: unknown) => {
    assertTrustedRenderer(event);
    const safePatch = validateSettingsPatchRequest(patch);
    if (settingsNativeChangeRequiresConfirmation(safePatch)) {
      await confirmNativeDesktopAction(event, {
        title: "Confirm settings change",
        message: "Allow Lengrvis to prepare a sensitive settings change?",
        detail: "This may enable capabilities such as remote desktop, file content upload, expanded folders, cloud context, or MCP tools."
      });
    }
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/confirm-sensitive-change",
      method: "POST",
      body: safePatch
    });
  });

  ipcMain.handle(IPC_CHANNELS.settingsSave, async (event, patch: unknown) => {
    assertTrustedRenderer(event);
    const safePatch = validateSettingsPatchRequest(patch);
    if (settingsEgressChangeRequiresConfirmation(safePatch) && !safePatch.confirmation_nonce) {
      throw new ApiRequestValidationError("Sensitive LLM endpoint settings require a prior confirmation");
    }
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings",
      method: "POST",
      body: safePatch
    });
  });

  ipcMain.handle(IPC_CHANNELS.permissionPolicyConfirmRelaxation, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const safeRequest = validatePermissionPolicyRelaxationRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm permission policy change",
      message: "Allow Lengrvis to prepare a permission policy relaxation?",
      detail: "This can change what tools the agent may use. Continue only if you intended to edit the policy."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/permission-policy/confirm-relaxation",
      method: "POST",
      body: safeRequest
    });
  });

  ipcMain.handle(IPC_CHANNELS.permissionPolicyUpsertRule, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const safeRequest = validatePermissionRuleUpsertRequest(request);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/permission-policy/rules",
      method: "POST",
      query: safeRequest.confirmationNonce ? { confirmation_nonce: safeRequest.confirmationNonce } : undefined,
      body: safeRequest.rule
    });
  });

  ipcMain.handle(IPC_CHANNELS.permissionPolicyDeleteRule, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const safeRequest = validatePermissionRuleDeleteRequest(request);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/settings/permission-policy/rules/${encodeURIComponent(safeRequest.ruleId)}`,
      method: "DELETE",
      query: safeRequest.confirmationNonce ? { confirmation_nonce: safeRequest.confirmationNonce } : undefined
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingCreateCode, async (event) => {
    assertTrustedRenderer(event);
    await confirmNativeDesktopAction(event, {
      title: "Confirm mobile pairing",
      message: "Create a new mobile pairing code?",
      detail: "Anyone who can see the temporary code may attempt to pair a device until it expires."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/pair/request",
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingListDevices, async (event) => {
    assertTrustedRenderer(event);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/pair/devices"
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingRevokeDevice, async (event, deviceId: string) => {
    assertTrustedRenderer(event);
    const safeDeviceId = validateBridgeIdentifier(deviceId, "mobile device id");
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/pair/devices/${encodeURIComponent(safeDeviceId)}`,
      method: "DELETE"
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingCreateRemoteInputGrant, async (event, request: MobilePairingRemoteInputGrantRequest) => {
    assertTrustedRenderer(event);
    const safeDeviceId = validateBridgeIdentifier(request?.deviceId, "mobile device id");
    const expiresInSeconds = validateBridgePositiveInteger(
      request?.expiresInSeconds,
      "remote input grant expiry",
      DEFAULT_REMOTE_INPUT_GRANT_TTL_SECONDS,
      1,
      86_400
    );
    await confirmNativeDesktopAction(event, {
      title: "Confirm remote input",
      message: "Allow this paired mobile device to send remote input?",
      detail: `Device id: ${safeDeviceId}\nExpires in: ${expiresInSeconds} seconds\n\nThe grant can be revoked from the desktop or mobile app.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/pair/devices/${encodeURIComponent(safeDeviceId)}/remote-input-grants`,
      method: "POST",
      body: { expires_in: expiresInSeconds }
    });
  });

  ipcMain.handle(IPC_CHANNELS.mobilePairingRevokeRemoteInputGrant, async (event, request: MobilePairingRevokeRemoteInputGrantRequest) => {
    assertTrustedRenderer(event);
    const safeDeviceId = validateBridgeIdentifier(request?.deviceId, "mobile device id");
    const safeGrantId = validateBridgeIdentifier(request?.grantId, "remote input grant id");
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/pair/devices/${encodeURIComponent(safeDeviceId)}/remote-input-grants/${encodeURIComponent(safeGrantId)}`,
      method: "DELETE"
    });
  });

}

function proxyExplicitDesktopBridgeRequest<TData>(
  backend: BackendProcessManager,
  request: ApiRequest
): Promise<ApiResponse<TData>> {
  return proxyApiRequest(backend.getBaseUrl(), request, backend.getDesktopApiToken(), {
    allowDeniedDesktopBridgePath: true
  });
}

async function ensureBackendReadyForRendererSubmission(
  backend: BackendProcessManager
): Promise<ApiResponse<never> | null> {
  const receivedAt = new Date().toISOString();
  try {
    const status = await backend.enterForeground("renderer_task_submit");
    if (status.health?.ok || (status.state === "running" && !status.health)) {
      return null;
    }
    return backendNotReadyResponse(status, receivedAt);
  } catch (error) {
    return {
      ok: false,
      status: 503,
      error: {
        code: "BACKEND_NOT_READY",
        message: error instanceof Error ? error.message : "Backend is not ready for task submission"
      },
      receivedAt
    };
  }
}

function backendNotReadyResponse(status: BackendStatus, receivedAt: string): ApiResponse<never> {
  return {
    ok: false,
    status: 503,
    error: {
      code: "BACKEND_NOT_READY",
      message: status.message
        ? `Backend is not ready for task submission: ${status.message}`
        : "Backend is not ready for task submission",
      details: { backendStatus: status }
    },
    receivedAt
  };
}

function isRendererTaskSubmissionRequest(request: unknown): boolean {
  if (!isPlainRecord(request) || typeof request.endpoint !== "string") {
    return false;
  }
  const method = typeof request.method === "string" ? request.method.toUpperCase() : "GET";
  if (method !== "POST") {
    return false;
  }
  return (
    request.endpoint === "/api/chat" ||
    (request.endpoint.startsWith("/api/perception/suggestions/") && request.endpoint.endsWith("/launch"))
  );
}

function rememberDocumentPathGrant(grants: Set<string>, filePath: string): void {
  grants.add(normalizeGrantPath(filePath));
}

function rememberRevealPathGrant(grants: Set<string>, filePath: string): void {
  grants.add(normalizeGrantPath(filePath));
}

function rememberRevealPathFromApiResponse(grants: Set<string>, response: ApiResponse<unknown>): void {
  if (!response.ok || !isPlainRecord(response.data) || typeof response.data.path !== "string") {
    return;
  }
  rememberRevealPathGrant(grants, response.data.path);
}

async function ensureDocumentReadGrant(
  event: IpcMainInvokeEvent,
  grants: Set<string>,
  paths: string[]
): Promise<void> {
  const ungranted = [...new Set(paths)].filter((filePath) => !grants.has(normalizeGrantPath(filePath)));
  if (!ungranted.length) {
    return;
  }

  if (typeof dialog.showMessageBox !== "function") {
    throw new ApiRequestValidationError("Document access requires a desktop confirmation dialog");
  }

  const window = BrowserWindow.fromWebContents(event.sender);
  const detail = ungranted.map((filePath) => `- ${filePath}`).join("\n");
  const options = {
    type: "question" as const,
    buttons: ["Allow for this app session", "Cancel"],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
    title: "Confirm document access",
    message: ungranted.length === 1 ? "Allow Lengrvis to read this document?" : "Allow Lengrvis to read these documents?",
    detail: `This may include document text. The selected path stays available until the desktop app exits.\n\n${detail}`
  };
  const result = window
    ? await dialog.showMessageBox(window, options)
    : await dialog.showMessageBox(options);

  if (result.response !== 0) {
    throw new ApiRequestValidationError("Document access was not confirmed");
  }
  for (const filePath of ungranted) {
    rememberDocumentPathGrant(grants, filePath);
  }
}

export async function confirmNativeDesktopAction(
  event: IpcMainInvokeEvent,
  options: { title: string; message: string; detail: string }
): Promise<void> {
  if (typeof dialog.showMessageBox !== "function") {
    throw new ApiRequestValidationError("Sensitive desktop action requires a native confirmation dialog");
  }
  const window = BrowserWindow.fromWebContents(event.sender);
  const messageBoxOptions = {
    type: "question" as const,
    buttons: ["Allow once", "Cancel"],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
    title: options.title,
    message: options.message,
    detail: options.detail
  };
  const result = window
    ? await dialog.showMessageBox(window, messageBoxOptions)
    : await dialog.showMessageBox(messageBoxOptions);
  if (result.response !== 0) {
    throw new ApiRequestValidationError("Sensitive desktop action was not confirmed");
  }
}

function normalizeGrantPath(filePath: string): string {
  return resolvePath(filePath).toLowerCase();
}

async function getFileIconDataUrl(
  filePath: string,
  grants: { documentPathGrants: Set<string>; revealPathGrants: Set<string> }
): Promise<string | null> {
  let resolved: string;
  try {
    resolved = resolvePath(validateBridgePathValue(filePath, "file icon path"));
  } catch {
    return null;
  }
  if (!isRevealPathAuthorized(resolved, grants)) {
    return null;
  }
  if (!existsSync(resolved)) {
    return null;
  }
  try {
    const icon = await app.getFileIcon(resolved, { size: "normal" });
    if (icon.isEmpty()) {
      return null;
    }
    return icon.toDataURL();
  } catch {
    return null;
  }
}

function showItemInFolder(
  filePath: string,
  grants: { documentPathGrants: Set<string>; revealPathGrants: Set<string> }
): { ok: boolean; path: string; revealed: boolean; shown: boolean; error?: string } {
  const resolved = resolvePath(filePath);
  if (!isRevealPathAuthorized(resolved, grants)) {
    return {
      ok: false,
      path: "",
      revealed: false,
      shown: false,
      error: "Path is not authorized for reveal"
    };
  }
  if (!existsSync(resolved)) {
    return { ok: false, path: resolved, revealed: false, shown: false, error: "Path does not exist" };
  }
  try {
    shell.showItemInFolder(resolved);
    return { ok: true, path: resolved, revealed: true, shown: true };
  } catch (error) {
    return {
      ok: false,
      path: resolved,
      revealed: false,
      shown: false,
      error: error instanceof Error ? error.message : "Could not reveal path"
    };
  }
}

function isRevealPathAuthorized(
  resolvedPath: string,
  grants: { documentPathGrants: Set<string>; revealPathGrants: Set<string> }
): boolean {
  const normalized = normalizeGrantPath(resolvedPath);
  if (grants.documentPathGrants.has(normalized) || grants.revealPathGrants.has(normalized)) {
    return true;
  }
  return defaultRevealRoots().some((root) => isSameOrNestedPath(root, resolvedPath));
}

function defaultRevealRoots(): string[] {
  const roots = [
    process.env.LENGRVIS_CONFIG_DIR,
    process.env.LENGRVIS_DATA_DIR,
    safeElectronAppPath("userData")
  ].filter((value): value is string => typeof value === "string" && value.trim().length > 0);
  return [...new Set(roots.map((root) => resolvePath(root)))];
}

function safeElectronAppPath(name: "userData"): string {
  try {
    return app.getPath(name);
  } catch {
    return "";
  }
}

function isSameOrNestedPath(rootPath: string, candidatePath: string): boolean {
  const root = resolvePath(rootPath).toLowerCase().replace(/[\\/]+$/, "");
  const candidate = resolvePath(candidatePath).toLowerCase();
  return candidate === root || candidate.startsWith(`${root}\\`) || candidate.startsWith(`${root}/`);
}

async function proxyApiRequest<TData>(
  baseUrl: string,
  request: ApiRequest,
  desktopApiToken: string,
  options: ApiRequestValidationOptions = {}
): Promise<ApiResponse<TData>> {
  const receivedAt = new Date().toISOString();
  let timeout: ReturnType<typeof setTimeout> | undefined;

  try {
    const validatedRequest = validateApiRequest(request, options);
    const url = buildValidatedRequestUrl(baseUrl, validatedRequest);
    const timeoutController = new AbortController();
    timeout = setTimeout(
      () => timeoutController.abort(),
      validatedRequest.timeoutMs
    );
    const groupSignal = resolveInflightGroupSignal(validatedRequest.abortGroup);
    const signal = groupSignal
      ? mergeAbortSignals([groupSignal, timeoutController.signal])
      : timeoutController.signal;

    const response = await fetch(url, {
      method: validatedRequest.method,
      headers: {
        Accept: "application/json",
        [DESKTOP_API_TOKEN_HEADER]: desktopApiToken,
        ...(validatedRequest.serializedBody !== undefined ? { "Content-Type": "application/json" } : {})
      },
      body: validatedRequest.serializedBody,
      signal
    });

    const data = await parseResponseBody(response);

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: {
          code: `HTTP_${response.status}`,
          message: getErrorMessage(data, response.statusText),
          details: data
        },
        receivedAt
      };
    }

    return {
      ok: true,
      status: response.status,
      data: data as TData,
      receivedAt
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Request failed";
    if (error instanceof ApiRequestValidationError) {
      return {
        ok: false,
        status: 0,
        error: {
          code: "INVALID_RENDERER_API_REQUEST",
          message
        },
        receivedAt
      };
    }

    return {
      ok: false,
      status: 0,
      error: {
        code: "NETWORK_ERROR",
        message
      },
      receivedAt
    };
  } finally {
    if (timeout) {
      clearTimeout(timeout);
    }
  }
}

export function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  return buildValidatedRequestUrl(baseUrl, validateApiRequest(request));
}

function buildValidatedRequestUrl(baseUrl: string, request: ValidatedApiRequest): URL {
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

function validateApiRequest(request: unknown, options: ApiRequestValidationOptions = {}): ValidatedApiRequest {
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

function validateApiAbortGroup(value: unknown): string {
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

function validateBridgeIdentifier(value: unknown, label: string): string {
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

function validateCommandExecuteRequest(value: unknown): { name: string; args: Record<string, unknown> } {
  const request = validatePlainBridgeBody(value, "command execute request");
  const name = validateBridgeIdentifier(request.name, "command name");
  const args = request.args === undefined ? {} : validatePlainBridgeBody(request.args, "command args");
  return { name, args };
}

function validatePlainBridgeBody(value: unknown, label: string): Record<string, unknown> {
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

function validateBridgePathValue(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError(`${label} is required`);
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 4096 || trimmed.includes("\0") || /[\u0000-\u001F\u007F]/.test(trimmed)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
  return trimmed;
}

function validateOptionalModelRequest(value: unknown, label: string): { model?: string } {
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

function validateRunStartRequest(value: unknown): DesktopRunStartRequest {
  const request = validatePlainBridgeBody(value, "run start request");
  const message = validateBridgeStringValue(request.message, "run message", 20_000, {
    allowEmpty: false,
    trim: true
  });
  const mode = validateBridgeEnum<DesktopRunStartRequest["mode"] & string>(request.mode, "run mode", RUN_MODES, "efficiency");
  const engine = validateBridgeEnum<DesktopRunStartRequest["engine"] & string>(request.engine, "run engine", RUN_ENGINES, "auto");
  return { message, mode, engine };
}

function validateDocumentParseRequest(value: unknown): { path: string; include_text?: boolean } {
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

function validateDocumentAskRequest(value: unknown): { path: string; question: string; top_k?: number } {
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

function validateDocumentCompareRequest(value: unknown): { paths: string[]; focus?: string } {
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

function validateSettingsPatchRequest(value: unknown): DesktopSettingsPatch {
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

function settingsEgressChangeRequiresConfirmation(patch: DesktopSettingsPatch): boolean {
  return Object.keys(patch).some((key) => SETTINGS_EGRESS_CONFIRMATION_FIELDS.has(key));
}

function settingsNativeChangeRequiresConfirmation(patch: DesktopSettingsPatch): boolean {
  return Object.keys(patch).some((key) => SETTINGS_NATIVE_CONFIRMATION_FIELDS.has(key));
}

function validatePermissionPolicyRelaxationRequest(value: unknown): Record<string, unknown> {
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

function validatePermissionRuleUpsertRequest(value: unknown): DesktopPermissionRuleUpsertRequest {
  const request = validatePlainBridgeBody(value, "permission rule upsert request");
  return {
    rule: validatePermissionRule(request.rule),
    confirmationNonce: validateOptionalConfirmationNonce(request.confirmationNonce ?? request.confirmation_nonce)
  };
}

function validatePermissionRuleDeleteRequest(value: unknown): DesktopPermissionRuleDeleteRequest {
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

function validateBridgePositiveInteger(
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

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function utf8ByteLength(value: string): number {
  return Buffer.byteLength(value, "utf8");
}

async function openSafeExternalUrl(rawUrl: string): Promise<void> {
  const parsed = validateSafeExternalUrl(rawUrl);
  await shell.openExternal(parsed.toString());
}

export function assertTrustedRenderer(event: IpcMainInvokeEvent): void {
  const url = event.senderFrame?.url ?? "";
  if (!BrowserWindow.fromWebContents(event.sender) || !isTrustedRendererUrl(url)) {
    throw new Error("IPC request came from an untrusted renderer");
  }
}

export function isTrustedRendererUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "file:") {
      const rendererRoot = pathToFileURL(`${__dirname}/../renderer/`).toString();
      return parsed.href.startsWith(rendererRoot);
    }
    if (parsed.protocol === "app:" && parsed.hostname === "local") {
      return true;
    }
    const trustedOrigins = new Set<string>();
    if (!app.isPackaged) {
      trustedOrigins.add("http://127.0.0.1:5173");
      trustedOrigins.add("http://localhost:5173");
    }
    const devServerUrl = app.isPackaged ? "" : process.env.VITE_DEV_SERVER_URL;
    if (!app.isPackaged && devServerUrl) {
      trustedOrigins.add(new URL(devServerUrl).origin);
    }
    return trustedOrigins.has(parsed.origin);
  } catch {
    return false;
  }
}

export function isSafeExternalUrl(url: string): boolean {
  try {
    validateSafeExternalUrl(url);
    return true;
  } catch {
    return false;
  }
}

function validateSafeExternalUrl(rawUrl: string): URL {
  if (typeof rawUrl !== "string" || !rawUrl.trim() || rawUrl.length > EXTERNAL_URL_MAX_CHARS) {
    throw new Error("External URL is invalid");
  }
  if (/[\u0000-\u001F\u007F]/.test(rawUrl)) {
    throw new Error("External URL must not contain control characters");
  }
  const parsed = new URL(rawUrl);
  if (!ALLOWED_EXTERNAL_PROTOCOLS.has(parsed.protocol)) {
    throw new Error("External URL protocol is not allowed");
  }
  if (parsed.protocol === "mailto:") {
    for (const [key, value] of parsed.searchParams.entries()) {
      if (hasControlCharacters(key) || hasControlCharacters(value)) {
        throw new Error("External mailto URL contains unsafe header characters");
      }
      if (MAILTO_DENIED_QUERY_KEYS.has(key.toLowerCase())) {
        throw new Error("External mailto URL contains unsafe message fields");
      }
    }
    return parsed;
  }
  if (parsed.username || parsed.password) {
    throw new Error("External URL credentials are not allowed");
  }
  if (!parsed.hostname || isBlockedExternalHost(parsed.hostname)) {
    throw new Error("External URL host is not allowed");
  }
  return parsed;
}

function isBlockedExternalHost(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (normalized === "localhost" || normalized.endsWith(".localhost")) {
    return true;
  }
  if (normalized === "::1" || normalized === "0:0:0:0:0:0:0:1") {
    return true;
  }
  const ipv4Mapped = normalized.match(/^::ffff:(?:(\d{1,3}(?:\.\d{1,3}){3})|([0-9a-f]{1,4}):([0-9a-f]{1,4}))$/i);
  if (ipv4Mapped) {
    const mappedIpv4 = ipv4Mapped[1] ?? ipv4FromHexWords(ipv4Mapped[2] ?? "", ipv4Mapped[3] ?? "");
    return isBlockedIpv4Host(mappedIpv4);
  }
  if (normalized.startsWith("fe80:") || normalized.startsWith("fc") || normalized.startsWith("fd")) {
    return true;
  }
  return isBlockedIpv4Host(normalized);
}

function isBlockedIpv4Host(hostname: string): boolean {
  const octets = hostname.split(".").map((part) => Number(part));
  if (octets.length !== 4 || octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return false;
  }
  const [first = 0, second = 0] = octets;
  return (
    first === 0 ||
    first === 10 ||
    first === 127 ||
    first === 169 && second === 254 ||
    first === 172 && second >= 16 && second <= 31 ||
    first === 192 && second === 168
  );
}

function ipv4FromHexWords(highWord: string, lowWord: string): string {
  const high = Number.parseInt(highWord, 16);
  const low = Number.parseInt(lowWord, 16);
  if (
    !Number.isInteger(high) ||
    !Number.isInteger(low) ||
    high < 0 ||
    high > 0xffff ||
    low < 0 ||
    low > 0xffff
  ) {
    return "";
  }
  return `${(high >> 8) & 0xff}.${high & 0xff}.${(low >> 8) & 0xff}.${low & 0xff}`;
}

function hasControlCharacters(value: string): boolean {
  return /[\u0000-\u001F\u007F]/.test(value);
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";

  if (response.status === 204) {
    return undefined;
  }

  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return text ? { message: text } : undefined;
}

function getErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object" && "message" in data) {
    const message = (data as { message?: unknown }).message;
    if (typeof message === "string") {
      return userFacingBackendError(message);
    }
  }
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return userFacingBackendError(detail);
    }
  }

  return userFacingBackendError(fallback || "Backend request failed");
}

function userFacingBackendError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("missing desktop api token") || normalized.includes("unauthorized")) {
    return "Lengrvis 正在保护本机接口。请重启桌面应用后再试；未授权页面不能直接读取本机数据。";
  }
  return message;
}
