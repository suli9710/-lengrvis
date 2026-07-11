import { ipcMain } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type { DesktopPrivacyEraseResponse } from "../shared/types";
import type { IpcHandlerContext } from "./ipcHandlerContext";
import { proxyExplicitDesktopBridgeRequest } from "./ipcBackendProxy";
import { confirmNativeDesktopAction, nativeActionConfirmationHeaders } from "./ipcNativeConfirmation";
import {
  ensureDocumentReadGrant,
  rememberRevealPathFromApiResponse
} from "./ipcPathGrants";
import {
  ApiRequestValidationError,
  settingsEgressChangeRequiresConfirmation,
  settingsNativeChangeRequiresConfirmation,
  validateDocumentAskRequest,
  validateDocumentCompareRequest,
  validateDocumentParseRequest,
  validateNoPayloadBridgeRequest,
  validateOpenSettingsRequest,
  validatePermissionPolicyRelaxationRequest,
  validatePermissionRuleDeleteRequest,
  validatePermissionRuleUpsertRequest,
  validatePrivacyEraseRequest,
  validateSettingsPatchRequest
} from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";

const BACKEND_PRIVACY_ERASE_CONFIRMATION = "erase-local-data";

export function registerSystemSettingsIpcHandlers(context: IpcHandlerContext): void {
  const { backend, documentPathGrants, localPrivacyEraser, revealPathGrants } = context;

  ipcMain.handle(IPC_CHANNELS.systemOpenSettings, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateOpenSettingsRequest(request);
    await confirmNativeDesktopAction(event, {
      title: "Confirm Windows Settings",
      message: "Open Windows Settings?",
      detail: `Settings page: ${body.uri}`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/system/open-settings",
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
    const backendBody = {
      confirm: BACKEND_PRIVACY_ERASE_CONFIRMATION,
      include_settings: body.includeSettings
    };
    const confirmationHeaders = await nativeActionConfirmationHeaders(
      (bridgeRequest) => proxyExplicitDesktopBridgeRequest(backend, bridgeRequest),
      backend,
      "/api/system/privacy/erase-local-data/native-confirmation-challenge",
      backendBody
    );
    await confirmNativeDesktopAction(event, {
      type: "warning",
      confirmLabel: "删除本机数据",
      title: "确认删除本机数据",
      message: "永久删除本机保存的个人数据？",
      detail:
        "任务、对话、运行记录、录屏、配对、记忆、文件索引、浏览器会话痕迹、已保存的网站密码和已导出的诊断包将被删除。安全审计链会保留；日志仍需在系统信息中手动清理。此操作无法撤销。"
    });
    if (!localPrivacyEraser) {
      throw new Error("Electron private data eraser is unavailable");
    }
    await localPrivacyEraser.eraseLocalPrivateData();
    return proxyExplicitDesktopBridgeRequest<DesktopPrivacyEraseResponse>(backend, {
      endpoint: "/api/system/privacy/erase-local-data",
      method: "POST",
      body: backendBody,
      headers: confirmationHeaders
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

  ipcMain.handle(IPC_CHANNELS.settingsTestLlmProvider, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    validateNoPayloadBridgeRequest(request, "LLM provider test request");
    await confirmNativeDesktopAction(event, {
      title: "Confirm LLM provider test",
      message: "Test the configured LLM provider?",
      detail: "This may call a local or cloud LLM provider and can consume quota or incur cost depending on your settings."
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: "/api/settings/test-llm-provider",
      method: "POST"
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
}
