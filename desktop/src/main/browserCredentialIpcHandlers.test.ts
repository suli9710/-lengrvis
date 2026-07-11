import type { IpcMainInvokeEvent } from "electron";
import { describe, expect, it, vi } from "vitest";

import { IPC_CHANNELS } from "../shared/ipc";
import {
  registerBrowserCredentialIpcHandlers,
  type BrowserCredentialIpcListener,
  type BrowserCredentialIpcTarget
} from "./browserCredentialIpcHandlers";
import type { NativeConfirmationDialogOptions } from "./ipcNativeConfirmation";

describe("browserCredentialIpcHandlers", () => {
  it("requires native confirmation before capture and ticket issuance", async () => {
    const { confirm, host, invoke } = registerForTest();

    await invoke(IPC_CHANNELS.credentialsCaptureFromPage, { session_id: "session_1" });
    await invoke(IPC_CHANNELS.credentialsIssueUseTicket, {
      session_id: "session_1",
      credential_ref_id: "cred_12345678",
      run_id: "run_1",
      task_id: "task_1",
      purpose: "sign-in" as const,
      ttl_seconds: 60
    });

    expect(confirm).toHaveBeenCalledTimes(2);
    const confirmationCalls = confirm.mock.calls as unknown as [IpcMainInvokeEvent, NativeConfirmationDialogOptions][];
    expect(confirmationCalls[1]?.[1].detail).toContain("example.test");
    expect(confirmationCalls[1]?.[1].detail).not.toContain("password123");
    expect(host.captureCredential).toHaveBeenCalledTimes(1);
    expect(host.issueCredentialUseTicket).toHaveBeenCalledTimes(1);
  });

  it("uses the typed ticket channel for fill without another dialog", async () => {
    const { confirm, host, invoke } = registerForTest();
    const ticket = ticketFixture();

    await invoke(IPC_CHANNELS.credentialsFill, { session_id: "session_1", ticket });

    expect(confirm).not.toHaveBeenCalled();
    expect(host.fillCredential).toHaveBeenCalledWith({ session_id: "session_1", ticket });
  });

  it("rejects plaintext and unsupported fields before reaching the host", async () => {
    const { host, invoke } = registerForTest();

    await expect(invoke(IPC_CHANNELS.credentialsIssueUseTicket, {
      session_id: "session_1",
      credential_ref_id: "cred_12345678",
      run_id: "run_1",
      task_id: "task_1",
      purpose: "sign-in" as const,
      password: "must-not-cross-ipc"
    })).rejects.toThrow(/unsupported field/);
    expect(host.previewCredentialUse).not.toHaveBeenCalled();
  });
});

function registerForTest() {
  const handlers = new Map<string, BrowserCredentialIpcListener>();
  const confirm = vi.fn(async () => undefined);
  const host: BrowserCredentialIpcTarget = {
    listCredentialRefs: vi.fn(() => []),
    previewCredentialCapture: vi.fn(async () => ({
      domain: "https://example.test:8443",
      session_id: "session_1",
      page_fingerprint: `sha256:${"a".repeat(64)}`,
      task_id: "task_1"
    })),
    captureCredential: vi.fn(async () => ({ ok: true })),
    previewCredentialUse: vi.fn(async () => ({
      domain: "https://example.test:8443",
      session_id: "session_1",
      page_fingerprint: `sha256:${"a".repeat(64)}`,
      task_id: "task_1",
      run_id: "run_1",
      credential_ref_id: "cred_12345678",
      purpose: "sign-in" as const,
      ttl_seconds: 60
    })),
    issueCredentialUseTicket: vi.fn(async () => ({ ok: true, ticket: ticketFixture() })),
    fillCredential: vi.fn(async () => ({ ok: true, filled_password: true })),
    previewCredentialDelete: vi.fn(() => ({
      domain: "https://example.test:8443",
      task_id: "task_1",
      credential_ref_id: "cred_12345678"
    })),
    deleteCredential: vi.fn(() => ({ ok: true }))
  };
  registerBrowserCredentialIpcHandlers({
    handle: (channel, listener) => handlers.set(channel, listener),
    host,
    assertTrustedRenderer: vi.fn(),
    confirmNativeDesktopAction: confirm
  });
  return {
    confirm,
    host,
    invoke: async (channel: string, ...args: unknown[]) => {
      const handler = handlers.get(channel);
      if (!handler) throw new Error(`Missing handler: ${channel}`);
      return handler({} as IpcMainInvokeEvent, ...args);
    }
  };
}

function ticketFixture() {
  return {
    schema_version: "credential-use-ticket-v1" as const,
    ticket_id: "ctkt_12345678",
    credential_ref_id: "cred_12345678",
    domain: "https://example.test:8443",
    session_id: "session_1",
    page_fingerprint: `sha256:${"a".repeat(64)}`,
    run_id: "run_1",
    task_id: "task_1",
    purpose: "sign-in" as const,
    issued_at: "2026-07-11T00:00:00.000Z",
    expires_at: "2026-07-11T00:01:00.000Z",
    nonce: "nonce_123456789012345678901234"
  };
}
