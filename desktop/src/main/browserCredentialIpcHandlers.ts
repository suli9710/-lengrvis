import type { IpcMainInvokeEvent } from "electron";

import type {
  CredentialBrokerResult,
  CredentialFillRequest,
  CredentialRef,
  CredentialRefRequest,
  CredentialSessionRequest,
  CredentialUseTicketRequest
} from "../shared/credentialTypes";
import { IPC_CHANNELS } from "../shared/ipc";
import type { NativeConfirmationDialogOptions } from "./ipcNativeConfirmation";

export type BrowserCredentialIpcListener = (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown;

export interface BrowserCredentialPreview {
  domain: string;
  session_id?: string;
  page_fingerprint?: string;
  task_id: string;
  credential_ref_id?: string;
  run_id?: string;
  purpose?: "sign-in";
  ttl_seconds?: number;
}

export interface BrowserCredentialIpcTarget {
  listCredentialRefs: (request: CredentialSessionRequest) => CredentialRef[];
  previewCredentialCapture: (request: CredentialSessionRequest) => Promise<BrowserCredentialPreview>;
  captureCredential: (
    request: CredentialSessionRequest,
    preview: BrowserCredentialPreview
  ) => Promise<CredentialBrokerResult>;
  previewCredentialUse: (request: CredentialUseTicketRequest) => Promise<BrowserCredentialPreview>;
  issueCredentialUseTicket: (
    request: CredentialUseTicketRequest,
    preview: BrowserCredentialPreview
  ) => Promise<CredentialBrokerResult>;
  fillCredential: (request: CredentialFillRequest) => Promise<CredentialBrokerResult>;
  previewCredentialDelete: (request: CredentialRefRequest) => BrowserCredentialPreview;
  deleteCredential: (request: CredentialRefRequest) => CredentialBrokerResult;
}

export interface BrowserCredentialIpcDependencies {
  handle: (channel: string, listener: BrowserCredentialIpcListener) => void;
  host: BrowserCredentialIpcTarget;
  assertTrustedRenderer?: (event: IpcMainInvokeEvent) => void;
  confirmNativeDesktopAction?: (
    event: IpcMainInvokeEvent,
    options: NativeConfirmationDialogOptions
  ) => Promise<void>;
}

export function registerBrowserCredentialIpcHandlers({
  handle,
  host,
  assertTrustedRenderer = defaultAssertTrustedRenderer,
  confirmNativeDesktopAction = defaultConfirmNativeDesktopAction
}: BrowserCredentialIpcDependencies): void {
  handle(IPC_CHANNELS.credentialsListForSession, (event, rawRequest) => {
    assertTrustedRenderer(event);
    return host.listCredentialRefs(parseCredentialSessionRequest(rawRequest));
  });

  handle(IPC_CHANNELS.credentialsCaptureFromPage, async (event, rawRequest) => {
    assertTrustedRenderer(event);
    const request = parseCredentialSessionRequest(rawRequest);
    const preview = await host.previewCredentialCapture(request);
    await confirmNativeDesktopAction(event, captureConfirmation(preview));
    return host.captureCredential(request, preview);
  });

  handle(IPC_CHANNELS.credentialsIssueUseTicket, async (event, rawRequest) => {
    assertTrustedRenderer(event);
    const request = parseCredentialUseTicketRequest(rawRequest);
    const preview = await host.previewCredentialUse(request);
    await confirmNativeDesktopAction(event, useConfirmation(preview));
    return host.issueCredentialUseTicket(request, preview);
  });

  handle(IPC_CHANNELS.credentialsFill, (event, rawRequest) => {
    assertTrustedRenderer(event);
    return host.fillCredential(parseCredentialFillRequest(rawRequest));
  });

  handle(IPC_CHANNELS.credentialsDelete, async (event, rawRequest) => {
    assertTrustedRenderer(event);
    const request = parseCredentialRefRequest(rawRequest);
    const preview = host.previewCredentialDelete(request);
    await confirmNativeDesktopAction(event, deleteConfirmation(preview));
    return host.deleteCredential(request);
  });
}

export function parseCredentialSessionRequest(value: unknown): CredentialSessionRequest {
  const record = exactRecord(value, ["session_id"]);
  return { session_id: identifier(record.session_id, "session id") };
}

export function parseCredentialRefRequest(value: unknown): CredentialRefRequest {
  const record = exactRecord(value, ["credential_ref_id", "session_id"]);
  return {
    session_id: identifier(record.session_id, "session id"),
    credential_ref_id: identifier(record.credential_ref_id, "credential ref id")
  };
}

export function parseCredentialUseTicketRequest(value: unknown): CredentialUseTicketRequest {
  const record = exactRecord(value, [
    "credential_ref_id",
    "purpose",
    "run_id",
    "session_id",
    "task_id",
    "ttl_seconds"
  ], ["ttl_seconds"]);
  if (record.purpose !== "sign-in") throw new Error("Credential purpose is not allowed");
  const ttl = record.ttl_seconds;
  if (ttl !== undefined && (!Number.isInteger(ttl) || Number(ttl) < 1 || Number(ttl) > 120)) {
    throw new Error("Credential use ticket TTL must be between 1 and 120 seconds");
  }
  return {
    session_id: identifier(record.session_id, "session id"),
    credential_ref_id: identifier(record.credential_ref_id, "credential ref id"),
    run_id: identifier(record.run_id, "run id"),
    task_id: identifier(record.task_id, "task id"),
    purpose: "sign-in",
    ttl_seconds: ttl === undefined ? undefined : Number(ttl)
  };
}

export function parseCredentialFillRequest(value: unknown): CredentialFillRequest {
  const record = exactRecord(value, ["session_id", "ticket"]);
  if (!record.ticket || typeof record.ticket !== "object" || Array.isArray(record.ticket)) {
    throw new Error("Credential use ticket is invalid");
  }
  return {
    session_id: identifier(record.session_id, "session id"),
    ticket: record.ticket as CredentialFillRequest["ticket"]
  };
}

function captureConfirmation(preview: BrowserCredentialPreview): NativeConfirmationDialogOptions {
  return {
    title: "Save site password",
    message: "Save the filled password for this site?",
    detail: [
      `Origin: ${preview.domain}`,
      `Task: ${bounded(preview.task_id)}`,
      "Only an ordinary username/password is saved. MFA, passcodes, and verification fields are excluded."
    ].join("\n")
  };
}

function useConfirmation(preview: BrowserCredentialPreview): NativeConfirmationDialogOptions {
  return {
    type: "warning",
    confirmLabel: "Fill once",
    title: "Use saved password",
    message: "Fill this saved password once?",
    detail: [
      `Origin: ${preview.domain}`,
      `Credential ref: ${bounded(preview.credential_ref_id ?? "unknown")}`,
      `Run: ${bounded(preview.run_id ?? "unknown")}`,
      `Task: ${bounded(preview.task_id)}`,
      `Purpose: ${preview.purpose ?? "sign-in"}`,
      `Expires in: ${preview.ttl_seconds ?? 60} seconds`
    ].join("\n")
  };
}

function deleteConfirmation(preview: BrowserCredentialPreview): NativeConfirmationDialogOptions {
  return {
    type: "warning",
    confirmLabel: "Delete",
    title: "Delete saved password",
    message: "Delete this saved password?",
    detail: [
      `Origin: ${preview.domain}`,
      `Credential ref: ${bounded(preview.credential_ref_id ?? "unknown")}`
    ].join("\n")
  };
}

function exactRecord(value: unknown, allowedKeys: string[], optionalKeys: string[] = []): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Credential request must be an object");
  if (Object.getOwnPropertySymbols(value).length) throw new Error("Credential request contains an unsupported field");
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (Object.values(descriptors).some((descriptor) => descriptor.get || descriptor.set)) {
    throw new Error("Credential request contains an unsupported field");
  }
  const record = Object.fromEntries(
    Object.entries(descriptors).map(([key, descriptor]) => [key, descriptor.value])
  ) as Record<string, unknown>;
  const allowed = new Set(allowedKeys);
  if (Object.keys(record).some((key) => !allowed.has(key))) {
    throw new Error("Credential request contains an unsupported field");
  }
  const optional = new Set(optionalKeys);
  if (allowedKeys.some((key) => !optional.has(key) && !Object.hasOwn(record, key))) {
    throw new Error("Credential request is missing a required field");
  }
  return record;
}

function identifier(value: unknown, label: string): string {
  const normalized = typeof value === "string" ? value.trim() : "";
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(normalized)) throw new Error(`Invalid ${label}`);
  return normalized;
}

function bounded(value: string): string {
  return value.length > 160 ? `${value.slice(0, 160)}...` : value;
}

function defaultAssertTrustedRenderer(event: IpcMainInvokeEvent): void {
  const { assertTrustedRenderer } = require("./ipc") as typeof import("./ipc");
  assertTrustedRenderer(event);
}

async function defaultConfirmNativeDesktopAction(
  event: IpcMainInvokeEvent,
  options: NativeConfirmationDialogOptions
): Promise<void> {
  const { confirmNativeDesktopAction } = require("./ipc") as typeof import("./ipc");
  await confirmNativeDesktopAction(event, options);
}
