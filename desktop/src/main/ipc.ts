import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent, type OpenDialogOptions } from "electron";
import { existsSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  ApiRequest,
  ApiResponse,
  BackendStatus,
  DesktopPrivacyEraseResponse,
  MobilePairingRemoteInputGrantRequest,
  MobilePairingRevokeRemoteInputGrantRequest
} from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { openSafeExternalUrl } from "./externalUrl";
import {
  ApiRequestValidationError,
  type ApiRequestValidationOptions,
  buildValidatedRequestUrl,
  isPlainRecord,
  settingsEgressChangeRequiresConfirmation,
  settingsNativeChangeRequiresConfirmation,
  validateApiAbortGroup,
  validateApiRequest,
  validateBridgeIdentifier,
  validateBridgePathValue,
  validateBridgePositiveInteger,
  validateCommandExecuteRequest,
  validateDocumentAskRequest,
  validateDocumentCompareRequest,
  validateDocumentParseRequest,
  validateOptionalModelRequest,
  validatePermissionPolicyRelaxationRequest,
  validatePermissionRuleDeleteRequest,
  validatePermissionRuleUpsertRequest,
  validatePlainBridgeBody,
  validatePrivacyEraseRequest,
  validateRunStartRequest,
  validateSettingsPatchRequest
} from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";

export { isSafeExternalUrl } from "./externalUrl";
export { buildRequestUrl } from "./ipcValidation";
export { assertTrustedRenderer, isTrustedRendererUrl } from "./rendererTrust";

const DEFAULT_REMOTE_INPUT_GRANT_TTL_SECONDS = 300;
const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";
const NATIVE_CONFIRMATION_ID_HEADER = "X-Lengrvis-Native-Confirmation-Id";
const NATIVE_CONFIRMATION_TIMESTAMP_HEADER = "X-Lengrvis-Native-Confirmation-Timestamp";
const NATIVE_CONFIRMATION_SIGNATURE_HEADER = "X-Lengrvis-Native-Confirmation-Signature";
const BACKEND_PRIVACY_ERASE_CONFIRMATION = "erase-local-data";

const apiInflightGroups = new Map<string, AbortController>();

type InternalDesktopBridgeRequest = Omit<ApiRequest, "headers"> & {
  headers?: Record<string, string>;
};

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
    return proxyRendererApiRequest(backend, request);
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

  ipcMain.handle(IPC_CHANNELS.approvalApprove, async (event, approvalId: unknown) => {
    assertTrustedRenderer(event);
    const safeApprovalId = validateBridgeIdentifier(approvalId, "approval id");
    const approvalResponse = await proxyExplicitDesktopBridgeRequest<Record<string, unknown>>(backend, {
      endpoint: `/api/approvals/${encodeURIComponent(safeApprovalId)}`,
      method: "GET"
    });
    if (!approvalResponse.ok) {
      throw new ApiRequestValidationError("Approval details are unavailable for native confirmation");
    }
    const confirmationHeaders = await nativeApprovalConfirmationHeaders(
      backend,
      "approve",
      safeApprovalId,
      approvalResponse.data
    );
    await confirmNativeDesktopAction(event, approvalConfirmationDialogOptions(safeApprovalId, approvalResponse.data, "approve"));
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/approvals/${encodeURIComponent(safeApprovalId)}/approve`,
      method: "POST",
      headers: confirmationHeaders
    });
  });

  ipcMain.handle(IPC_CHANNELS.approvalReject, async (event, approvalId: unknown) => {
    assertTrustedRenderer(event);
    const safeApprovalId = validateBridgeIdentifier(approvalId, "approval id");
    const approvalResponse = await proxyExplicitDesktopBridgeRequest<Record<string, unknown>>(backend, {
      endpoint: `/api/approvals/${encodeURIComponent(safeApprovalId)}`,
      method: "GET"
    });
    if (!approvalResponse.ok) {
      throw new ApiRequestValidationError("Approval details are unavailable for native confirmation");
    }
    const confirmationHeaders = await nativeApprovalConfirmationHeaders(
      backend,
      "reject",
      safeApprovalId,
      approvalResponse.data
    );
    await confirmNativeDesktopAction(event, approvalConfirmationDialogOptions(safeApprovalId, approvalResponse.data, "reject"));
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/approvals/${encodeURIComponent(safeApprovalId)}/reject`,
      method: "POST",
      headers: confirmationHeaders
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
    const body = validateRunStartRequest(request);
    const backendNotReady = await ensureBackendReadyForRendererSubmission(backend);
    if (backendNotReady) {
      return backendNotReady;
    }
    await confirmNativeDesktopAction(event, {
      title: "Confirm agent run",
      message: "Start this agent run?",
      detail: [
        `Mode: ${body.mode ?? "efficiency"}`,
        `Engine: ${body.engine ?? "auto"}`,
        "",
        "Prompt:",
        truncateForDialog(body.message),
        "",
        "Runs can use tools, access authorized files, and request further approvals."
      ].join("\n")
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/runs",
      method: "POST",
      body
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

  ipcMain.handle(IPC_CHANNELS.privacyEraseLocalData, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validatePrivacyEraseRequest(request);
    await confirmNativeDesktopAction(event, {
      type: "warning",
      confirmLabel: "删除本机数据",
      title: "确认删除本机数据",
      message: "永久删除本机保存的个人数据？",
      detail:
        "任务、对话、运行记录、录屏、配对、记忆、文件索引和已导出的诊断包将被删除。安全审计链会保留；日志仍需在系统信息中手动清理。此操作无法撤销。"
    });
    return proxyExplicitDesktopBridgeRequest<DesktopPrivacyEraseResponse>(backend, {
      endpoint: "/api/system/privacy/erase-local-data",
      method: "POST",
      body: {
        confirm: BACKEND_PRIVACY_ERASE_CONFIRMATION,
        include_settings: body.includeSettings
      }
    });
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
    if (
      (settingsEgressChangeRequiresConfirmation(safePatch) || settingsNativeChangeRequiresConfirmation(safePatch))
      && !safePatch.confirmation_nonce
    ) {
      throw new ApiRequestValidationError("Sensitive settings require a prior confirmation");
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
    await confirmNativeDesktopAction(event, {
      title: "Confirm device disconnect",
      message: "Disconnect this paired mobile device?",
      detail: `Device id: ${safeDeviceId}\n\nThe device will lose access until paired again.`
    });
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
    await confirmNativeDesktopAction(event, {
      title: "Confirm remote input revoke",
      message: "Revoke remote input access for this device?",
      detail: `Device id: ${safeDeviceId}\nGrant id: ${safeGrantId}\n\nThe mobile device will return to read-only remote view.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/pair/devices/${encodeURIComponent(safeDeviceId)}/remote-input-grants/${encodeURIComponent(safeGrantId)}`,
      method: "DELETE"
    });
  });

}

function proxyExplicitDesktopBridgeRequest<TData>(
  backend: BackendProcessManager,
  request: InternalDesktopBridgeRequest
): Promise<ApiResponse<TData>> {
  return proxyApiRequest(backend.getBaseUrl(), request, backend.getDesktopApiToken(), {
    allowDeniedDesktopBridgePath: true,
    allowInternalHeaders: true
  });
}

function proxyRendererApiRequest<TData>(
  backend: BackendProcessManager,
  request: ApiRequest
): Promise<ApiResponse<TData>> {
  return proxyApiRequest(backend.getBaseUrl(), request, backend.getDesktopApiToken());
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
  options: {
    title: string;
    message: string;
    detail: string;
    type?: "question" | "warning";
    confirmLabel?: string;
  }
): Promise<void> {
  if (typeof dialog.showMessageBox !== "function") {
    throw new ApiRequestValidationError("Sensitive desktop action requires a native confirmation dialog");
  }
  const window = BrowserWindow.fromWebContents(event.sender);
  const messageBoxOptions = {
    type: options.type ?? ("question" as const),
    buttons: [options.confirmLabel ?? "Allow once", "Cancel"],
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

interface NativeConfirmationChallengePayload extends Record<string, unknown> {
  confirmation_id?: unknown;
  expires_at_epoch?: unknown;
  signing_payload?: unknown;
}

async function nativeApprovalConfirmationHeaders(
  backend: BackendProcessManager,
  action: "approve" | "reject",
  approvalId: string,
  approvalPayload: unknown
): Promise<Record<string, string>> {
  const challenge = await proxyExplicitDesktopBridgeRequest<NativeConfirmationChallengePayload>(backend, {
    endpoint: `/api/approvals/${encodeURIComponent(approvalId)}/native-confirmation-challenge`,
    method: "POST",
    body: {
      action,
      expected_preview_hmac: approvalPreviewHmac(approvalPayload)
    }
  });
  if (!challenge.ok || !challenge.data) {
    throw new ApiRequestValidationError("Native confirmation challenge is unavailable");
  }
  const confirmationId = stringField(challenge.data, "confirmation_id");
  const signingPayload = stringField(challenge.data, "signing_payload");
  const expiresAt = String(challenge.data.expires_at_epoch ?? "").trim();
  if (!confirmationId || !signingPayload || !expiresAt) {
    throw new ApiRequestValidationError("Native confirmation challenge is malformed");
  }
  const signature = backend.signNativeConfirmationPayload(signingPayload);
  return {
    [NATIVE_CONFIRMATION_ID_HEADER]: confirmationId,
    [NATIVE_CONFIRMATION_TIMESTAMP_HEADER]: expiresAt,
    [NATIVE_CONFIRMATION_SIGNATURE_HEADER]: signature
  };
}

function approvalPreviewHmac(payload: unknown): string {
  const detail = isPlainRecord(payload) ? payload : {};
  const approval = isPlainRecord(detail.approval) ? detail.approval : detail;
  return stringField(approval, "preview_hmac") || stringField(approval, "previewHmac");
}

function approvalConfirmationDialogOptions(
  approvalId: string,
  payload: unknown,
  action: "approve" | "reject"
): Parameters<typeof confirmNativeDesktopAction>[1] {
  const detail = isPlainRecord(payload) ? payload : {};
  const approval = isPlainRecord(detail.approval) ? detail.approval : detail;
  const task = isPlainRecord(detail.task) ? detail.task : {};
  const taskSummary = stringField(task, "user_goal") || stringField(task, "userGoal") || stringField(task, "title");
  const toolName = stringField(approval, "tool_name") || stringField(approval, "toolName") || "unknown";
  const riskLevel = stringField(approval, "risk_level") || stringField(approval, "riskLevel") || "unknown";
  const dryRunSummary = stringField(approval, "dry_run_summary") || stringField(approval, "dryRunSummary");
  const message = stringField(approval, "message") || stringField(approval, "reason") || "No approval summary was provided.";
  const lines = [
    `Approval id: ${approvalId}`,
    `Task: ${taskSummary || "unknown"}`,
    `Tool: ${toolName}`,
    `Risk: ${riskLevel}`,
    "",
    "Request:",
    truncateForDialog(message),
    "",
    "Dry-run preview:",
    truncateForDialog(dryRunSummary || "No dry-run summary was provided.")
  ];
  return {
    type: action === "approve" ? "warning" : "question",
    confirmLabel: action === "approve" ? "Approve once" : "Reject",
    title: action === "approve" ? "Confirm approval" : "Confirm rejection",
    message: action === "approve" ? "Approve this pending agent action?" : "Reject this pending agent action?",
    detail: lines.join("\n")
  };
}

function stringField(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value.trim() : "";
}

function truncateForDialog(value: string): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > 1200 ? `${normalized.slice(0, 1200)}...` : normalized;
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
  request: InternalDesktopBridgeRequest,
  desktopApiToken: string,
  options: ApiRequestValidationOptions & { allowInternalHeaders?: boolean } = {}
): Promise<ApiResponse<TData>> {
  const receivedAt = new Date().toISOString();
  let timeout: ReturnType<typeof setTimeout> | undefined;

  try {
    const { allowInternalHeaders, ...validationOptions } = options;
    const { headers: extraHeaders, ...requestWithoutHeaders } = request;
    const requestForValidation = allowInternalHeaders ? requestWithoutHeaders : request;
    const validatedRequest = validateApiRequest(requestForValidation, validationOptions);
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
        ...(allowInternalHeaders ? (extraHeaders ?? {}) : {}),
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
