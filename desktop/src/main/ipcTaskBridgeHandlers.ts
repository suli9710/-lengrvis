import { ipcMain } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type { BackendProcessManager } from "./backendProcess";
import {
  ensureBackendReadyForRendererSubmission,
  proxyExplicitDesktopBridgeRequest
} from "./ipcBackendProxy";
import {
  approvalConfirmationDialogOptions,
  confirmNativeDesktopAction,
  nativeApprovalConfirmationHeaders,
  truncateForDialog
} from "./ipcNativeConfirmation";
import {
  ApiRequestValidationError,
  validateBridgeIdentifier,
  validateCommandExecuteRequest,
  validatePerceptionSuggestionLaunchRequest,
  validatePlainBridgeBody,
  validateRunStartRequest
} from "./ipcValidation";
import { assertTrustedRenderer } from "./rendererTrust";

export function registerTaskBridgeIpcHandlers(backend: BackendProcessManager): void {
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
      (request) => proxyExplicitDesktopBridgeRequest(backend, request),
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
      (request) => proxyExplicitDesktopBridgeRequest(backend, request),
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

  ipcMain.handle(IPC_CHANNELS.taskPause, async (event, taskId: unknown) => {
    assertTrustedRenderer(event);
    const safeTaskId = validateBridgeIdentifier(taskId, "task id");
    await confirmNativeDesktopAction(event, {
      title: "Confirm task pause",
      message: "Pause this task?",
      detail: `Task id: ${safeTaskId}\n\nThis changes the lifecycle state of an agent task.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/tasks/${encodeURIComponent(safeTaskId)}/pause`,
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.taskResume, async (event, taskId: unknown) => {
    assertTrustedRenderer(event);
    const safeTaskId = validateBridgeIdentifier(taskId, "task id");
    await confirmNativeDesktopAction(event, {
      title: "Confirm task resume",
      message: "Resume this task?",
      detail: `Task id: ${safeTaskId}\n\nResuming can continue previously planned agent work.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/tasks/${encodeURIComponent(safeTaskId)}/resume`,
      method: "POST"
    });
  });

  ipcMain.handle(IPC_CHANNELS.taskCancel, async (event, taskId: unknown) => {
    assertTrustedRenderer(event);
    const safeTaskId = validateBridgeIdentifier(taskId, "task id");
    await confirmNativeDesktopAction(event, {
      type: "warning",
      confirmLabel: "Cancel task",
      title: "Confirm task cancellation",
      message: "Cancel this task?",
      detail: `Task id: ${safeTaskId}\n\nThis may interrupt agent work in progress.`
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/tasks/${encodeURIComponent(safeTaskId)}/cancel`,
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

  ipcMain.handle(IPC_CHANNELS.perceptionSuggestionLaunch, async (event, request: unknown) => {
    assertTrustedRenderer(event);
    const body = validatePerceptionSuggestionLaunchRequest(request);
    const backendNotReady = await ensureBackendReadyForRendererSubmission(backend);
    if (backendNotReady) {
      return backendNotReady;
    }
    await confirmNativeDesktopAction(event, {
      title: "Confirm suggested agent run",
      message: "Start this suggested task?",
      detail: [
        `Suggestion id: ${body.suggestionId}`,
        `Mode: ${body.mode ?? "efficiency"}`,
        "",
        "Suggested runs can use tools, access authorized files, and request further approvals."
      ].join("\n")
    });
    return proxyExplicitDesktopBridgeRequest(backend, {
      endpoint: `/api/perception/suggestions/${encodeURIComponent(body.suggestionId)}/launch`,
      method: "POST",
      body: {
        suggestion_id: body.suggestionId,
        mode: body.mode ?? "efficiency"
      }
    });
  });
}
