import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BrowserHost } from "./browserHost";
import { CredentialUseTicketBroker } from "./credentialUseTicketBroker";
import { CredentialVault } from "./credentialVault";

const tempDirs: string[] = [];

afterEach(() => {
  for (const directory of tempDirs.splice(0)) rmSync(directory, { recursive: true, force: true });
});

describe("BrowserHost credential broker", () => {
  it("captures in main, fills once on the exact task/origin/page, and stops duplicate effects", async () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-browser-credential-"));
    tempDirs.push(directory);
    const vault = new CredentialVault({
      filePath: () => join(directory, "credential-vault.json"),
      storage: {
        isEncryptionAvailable: () => true,
        getSelectedStorageBackend: () => "dpapi",
        encryptString: (value) => Buffer.from([...value].reverse().join("")),
        decryptString: (value) => [...value.toString()].reverse().join("")
      },
      randomId: () => "11111111-2222-3333-4444-555555555555"
    });
    const tickets = new CredentialUseTicketBroker({
      now: () => new Date("2026-07-11T00:00:00.000Z"),
      randomId: () => "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      randomNonce: () => "nonce_123456789012345678901234"
    });
    const host = new BrowserHost(() => null, vault, tickets);
    const fingerprint = `sha256:${"a".repeat(64)}`;
    const executeJavaScript = vi.fn(async (script: string) => {
      if (script.includes("filled_password")) {
        return { ok: true, filled_username: true, filled_password: true };
      }
      if (script.includes('"fingerprint" === "fingerprint"')) {
        return { ok: true, origin: "https://example.test:8443", page_fingerprint: fingerprint };
      }
      return {
        ok: true,
        username: "alice",
        password: "top-secret",
        origin: "https://example.test:8443",
        page_fingerprint: fingerprint
      };
    });
    attachSession(host, executeJavaScript);

    const capturePreview = await host.previewCredentialCapture({ session_id: "session_1" });
    const captured = await host.captureCredential({ session_id: "session_1" }, capturePreview);
    const credentialRefId = captured.credential_ref?.id;
    expect(credentialRefId).toBeTruthy();
    const ticketRequest = {
      session_id: "session_1",
      credential_ref_id: credentialRefId!,
      run_id: "run_1",
      task_id: "task_1",
      purpose: "sign-in",
      ttl_seconds: 60
    } as const;
    const usePreview = await host.previewCredentialUse(ticketRequest);
    const issued = await host.issueCredentialUseTicket(ticketRequest, usePreview);
    expect(issued.ticket).toBeTruthy();

    const first = await host.fillCredential({ session_id: "session_1", ticket: issued.ticket! });
    const duplicate = await host.fillCredential({ session_id: "session_1", ticket: issued.ticket! });

    expect(first).toMatchObject({ ok: true, filled_username: true, filled_password: true });
    expect(duplicate).toMatchObject({ ok: false, error: expect.stringMatching(/already consumed/) });
    expect(executeJavaScript).toHaveBeenCalledTimes(7);
    expect(JSON.stringify(host.getSnapshot())).not.toContain("top-secret");
  });

  it("rejects capture when the page fingerprint changes after native confirmation", async () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-browser-credential-toctou-"));
    tempDirs.push(directory);
    const vault = new CredentialVault({
      filePath: () => join(directory, "credential-vault.json"),
      storage: {
        isEncryptionAvailable: () => true,
        getSelectedStorageBackend: () => "dpapi",
        encryptString: (value) => Buffer.from(value),
        decryptString: (value) => value.toString()
      }
    });
    const host = new BrowserHost(() => null, vault, new CredentialUseTicketBroker());
    const executeJavaScript = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        origin: "https://example.test:8443",
        page_fingerprint: `sha256:${"a".repeat(64)}`
      })
      .mockResolvedValueOnce({ ok: false, error_code: "page-fingerprint-mismatch" });
    attachSession(host, executeJavaScript);

    const preview = await host.previewCredentialCapture({ session_id: "session_1" });
    const result = await host.captureCredential({ session_id: "session_1" }, preview);

    expect(result).toMatchObject({ ok: false, error: expect.stringMatching(/changed|savable password/) });
    expect(vault.list()).toEqual([]);
  });

  it("erases the credential vault and outstanding tickets with local private data", async () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-browser-private-erase-"));
    tempDirs.push(directory);
    const vault = new CredentialVault({
      filePath: () => join(directory, "credential-vault.json"),
      storage: {
        isEncryptionAvailable: () => true,
        getSelectedStorageBackend: () => "dpapi",
        encryptString: (value) => Buffer.from(value),
        decryptString: (value) => value.toString()
      }
    });
    const tickets = new CredentialUseTicketBroker();
    const host = new BrowserHost(() => null, vault, tickets);
    const ref = vault.store("https://example.test:8443", { username: "alice", password: "secret" });
    const binding = {
      credential_ref_id: ref.id,
      domain: ref.domain,
      session_id: "session_1",
      page_fingerprint: `sha256:${"a".repeat(64)}`,
      run_id: "run_1",
      task_id: "task_1",
      purpose: "sign-in" as const
    };
    const ticket = tickets.issue(binding);

    await host.eraseLocalPrivateData();

    expect(vault.list()).toEqual([]);
    expect(() => tickets.consume(ticket, binding)).toThrow(/already consumed/);
  });
});

function attachSession(host: BrowserHost, executeJavaScript: ReturnType<typeof vi.fn>): void {
  const webContents = {
    getURL: () => "https://example.test:8443/login",
    executeJavaScript
  };
  const entry = {
    container: { kind: "browserView", view: { webContents } },
    proxyReady: Promise.resolve(),
    session: {
      id: "session_1",
      task_id: "task_1",
      current_url: "https://example.test:8443/login",
      title: "Login",
      status: "idle",
      mode: "watch",
      created_at: "2026-07-11T00:00:00.000Z",
      updated_at: "2026-07-11T00:00:00.000Z",
      paused: false,
      takeover: false,
      last_observation: null
    },
    events: []
  };
  const internals = host as unknown as { sessions: Map<string, unknown> };
  internals.sessions.set("session_1", entry);
}
