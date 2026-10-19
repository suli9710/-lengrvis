import { ipcMain } from "electron";
import { IPC_CHANNELS } from "../shared/ipc";
import type { ButlerOverview } from "../shared/butlerTypes";
import type { BackendProcessManager } from "./backendProcess";
import { ensureBackendReadyForRendererSubmission, proxyExplicitDesktopBridgeRequest } from "./ipcBackendProxy";
import { confirmNativeDesktopAction, nativeActionConfirmationHeaders } from "./ipcNativeConfirmation";
import { ApiRequestValidationError, validateNoPayloadBridgeRequest } from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";
import {
  validateButlerArrangement, validateButlerControl, validateButlerMatterControl, validateButlerMessage, validateButlerResponse,
  validateButlerScope, validateButlerVersionedId, validateButlerOpenArtifact
} from "./ipc/validation/butler";

const actionLabels = { observe: "读取与观察", file_modify: "可撤销的文件修改", ui_edit: "应用内容编辑", browser_prepare: "网页准备操作" };

export function registerButlerIpcHandlers(backend: BackendProcessManager): void {
  ipcMain.handle(IPC_CHANNELS.butlerOpenArtifact, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { matter_id, artifact_id, version } = validateButlerOpenArtifact(request);
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/butler/matters/${encodeURIComponent(matter_id)}/artifacts/${encodeURIComponent(artifact_id)}/open`,
      method: "POST", body: { version }
    });
  });
  ipcMain.handle(IPC_CHANNELS.butlerArrangement, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { matter_id, ...body } = validateButlerArrangement(request);
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: `/api/butler/matters/${encodeURIComponent(matter_id)}/arrangement`, method: "POST", body });
  });
  ipcMain.handle(IPC_CHANNELS.butlerOverview, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    validateNoPayloadBridgeRequest(request, "butler overview");
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: "/api/butler/overview" });
  });
  ipcMain.handle(IPC_CHANNELS.butlerMessage, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateButlerMessage(request);
    const unavailable = await ensureBackendReadyForRendererSubmission(backend);
    if (unavailable) return unavailable;
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: "/api/butler/messages", method: "POST", body });
  });
  ipcMain.handle(IPC_CHANNELS.butlerRespond, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { matter_id, ...body } = validateButlerResponse(request);
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: `/api/butler/matters/${encodeURIComponent(matter_id)}/respond`, method: "POST", body });
  });
  ipcMain.handle(IPC_CHANNELS.butlerMatterControl, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { matter_id, action } = validateButlerMatterControl(request);
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: `/api/butler/matters/${encodeURIComponent(matter_id)}/control`, method: "POST", body: { action } });
  });
  ipcMain.handle(IPC_CHANNELS.butlerControl, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateButlerControl(request);
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: "/api/butler/control", method: "POST", body });
  });
  ipcMain.handle(IPC_CHANNELS.butlerScopeCreate, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validateButlerScope(request);
    const headers = await nativeActionConfirmationHeaders(
      (challenge) => proxyExplicitDesktopBridgeRequest(backend, challenge), backend,
      "/api/butler/scopes/native-confirmation-challenge", body
    );
    await confirmNativeDesktopAction(event, {
      title: "确认管家授权范围",
      message: "哥哥，允许管家在以下范围持续处理事务？",
      confirmLabel: "授权此范围",
      detail: [
        `名称：${body.label}`,
        `目录：\n${body.directories.join("\n") || "无"}`,
        `应用：\n${body.applications.join("\n") || "无"}`,
        `网站：\n${body.origins.join("\n") || "无"}`,
        `账号：${body.account_id || "未指定"}`,
        `操作：${body.allowed_actions.map((action) => actionLabels[action]).join("、")}`,
        "付款、对外发送、公开发布和不可恢复的改动仍需单独确认。"
      ].join("\n\n")
    });
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: "/api/butler/scopes", method: "POST", body, headers });
  });
  ipcMain.handle(IPC_CHANNELS.butlerScopeRevoke, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { id, version } = validateButlerVersionedId(request, "scope_id");
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: `/api/butler/scopes/${encodeURIComponent(id)}/revoke`, method: "POST", body: { version } });
  });
  ipcMain.handle(IPC_CHANNELS.butlerUndo, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const { id, version } = validateButlerVersionedId(request, "matter_id");
    // Resolve the actual object in main; renderer-supplied labels are not confirmation evidence.
    const overview = await proxyExplicitDesktopBridgeRequest<ButlerOverview>(backend, { endpoint: "/api/butler/overview" });
    const matter = overview.ok ? overview.data?.matters.find((item) => item.id === id) : undefined;
    if (!matter || matter.version !== version || !matter.undo_available) {
      throw new ApiRequestValidationError("事项已改变或没有可撤销的改动，请刷新后重试。");
    }
    const headers = await nativeActionConfirmationHeaders(
      (challenge) => proxyExplicitDesktopBridgeRequest(backend, challenge), backend,
      `/api/butler/matters/${encodeURIComponent(id)}/undo/native-confirmation-challenge`, { version }
    );
    await confirmNativeDesktopAction(event, {
      title: "确认撤销事项改动",
      message: "哥哥，撤销这件事项已记录的改动？",
      type: "warning", confirmLabel: "撤销改动",
      detail: [`事项：${matter.goal}`, `进度：${matter.progress}`, `关联任务：${matter.task_ids.join("、")}`, "撤销前会核对当前文件状态，用户后续修改不会被直接覆盖。"].join("\n\n")
    });
    return proxyExplicitDesktopBridgeRequest(backend, { endpoint: `/api/butler/matters/${encodeURIComponent(id)}/undo`, method: "POST", body: { version }, headers });
  });
}
