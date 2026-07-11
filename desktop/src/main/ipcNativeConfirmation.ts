import { BrowserWindow, dialog, type IpcMainInvokeEvent } from "electron";

import type { ApiResponse } from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { ApiRequestValidationError, isPlainRecord } from "./ipcValidation";

const NATIVE_CONFIRMATION_ID_HEADER = "X-Lengrvis-Native-Confirmation-Id";
const NATIVE_CONFIRMATION_TIMESTAMP_HEADER = "X-Lengrvis-Native-Confirmation-Timestamp";
const NATIVE_CONFIRMATION_SIGNATURE_HEADER = "X-Lengrvis-Native-Confirmation-Signature";

export interface NativeConfirmationDialogOptions {
  title: string;
  message: string;
  detail: string;
  type?: "question" | "warning";
  confirmLabel?: string;
}

interface NativeConfirmationChallengePayload extends Record<string, unknown> {
  confirmation_id?: unknown;
  expires_at_epoch?: unknown;
  signing_payload?: unknown;
}

interface NativeConfirmationBridgeRequest {
  endpoint: string;
  method: "POST";
  body?: unknown;
}

type NativeConfirmationBridgeRequester = <TData>(
  request: NativeConfirmationBridgeRequest
) => Promise<ApiResponse<TData>>;

export async function confirmNativeDesktopAction(
  event: IpcMainInvokeEvent,
  options: NativeConfirmationDialogOptions
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

export async function nativeApprovalConfirmationHeaders(
  requestBridge: NativeConfirmationBridgeRequester,
  backend: Pick<BackendProcessManager, "signNativeConfirmationPayload">,
  action: "approve" | "reject",
  approvalId: string,
  approvalPayload: unknown
): Promise<Record<string, string>> {
  const challenge = await requestBridge<NativeConfirmationChallengePayload>({
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

export async function nativeActionConfirmationHeaders(
  requestBridge: NativeConfirmationBridgeRequester,
  backend: Pick<BackendProcessManager, "signNativeConfirmationPayload">,
  challengeEndpoint: string,
  body?: unknown
): Promise<Record<string, string>> {
  const challenge = await requestBridge<NativeConfirmationChallengePayload>({
    endpoint: challengeEndpoint,
    method: "POST",
    body
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

export function approvalConfirmationDialogOptions(
  approvalId: string,
  payload: unknown,
  action: "approve" | "reject"
): NativeConfirmationDialogOptions {
  const detail = isPlainRecord(payload) ? payload : {};
  const approval = isPlainRecord(detail.approval) ? detail.approval : detail;
  const task = isPlainRecord(detail.task) ? detail.task : {};
  const taskSummary = stringField(task, "user_goal") || stringField(task, "userGoal") || stringField(task, "title");
  const toolName = stringField(approval, "tool_name") || stringField(approval, "toolName") || "unknown";
  const riskLevel = stringField(approval, "risk_level") || stringField(approval, "riskLevel") || "unknown";
  const dryRunSummary = stringField(approval, "dry_run_summary") || stringField(approval, "dryRunSummary");
  const message = stringField(approval, "message") || stringField(approval, "reason") || "No approval summary was provided.";
  const expiresAt = stringField(approval, "expires_at") || stringField(approval, "expiresAt");
  const lines = [
    `Approval id: ${approvalId}`,
    `Task: ${taskSummary || "unknown"}`,
    `Tool: ${toolName}`,
    `Risk: ${riskLevel}`,
    `Authorization expires: ${expiresAt || "missing"}`,
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

export function approvalAuthorizationError(
  payload: unknown,
  action: "approve" | "reject",
  now = Date.now()
): string {
  const detail = isPlainRecord(payload) ? payload : {};
  const approval = isPlainRecord(detail.approval) ? detail.approval : detail;
  const status = stringField(approval, "status");
  const allowedStatuses = action === "approve" ? new Set(["pending", "approved"]) : new Set(["pending"]);
  if (!allowedStatuses.has(status)) {
    return `Approval is not ${action === "approve" ? "executable" : "rejectable"} in status '${status || "unknown"}'.`;
  }
  const expiresAt = Date.parse(stringField(approval, "expires_at") || stringField(approval, "expiresAt"));
  if (!Number.isFinite(expiresAt)) {
    return "Approval authorization expiry is missing or invalid.";
  }
  if (expiresAt <= now) {
    return "Approval authorization has expired. Refresh and generate a new preview.";
  }
  return "";
}

export function truncateForDialog(value: string): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > 1200 ? `${normalized.slice(0, 1200)}...` : normalized;
}

function approvalPreviewHmac(payload: unknown): string {
  const detail = isPlainRecord(payload) ? payload : {};
  const approval = isPlainRecord(detail.approval) ? detail.approval : detail;
  return stringField(approval, "preview_hmac") || stringField(approval, "previewHmac");
}

function stringField(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value.trim() : "";
}
