import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { CredentialVault, credentialDomainFromUrl } from "./credentialVault";

const tempDirs: string[] = [];

afterEach(() => {
  for (const directory of tempDirs.splice(0)) rmSync(directory, { recursive: true, force: true });
});

describe("CredentialVault", () => {
  it("persists only safeStorage ciphertext and returns public refs", () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-credentials-"));
    tempDirs.push(directory);
    const filePath = join(directory, "credential-vault.json");
    const vault = new CredentialVault({
      filePath: () => filePath,
      storage: reversibleSafeStorage(),
      now: () => new Date("2026-07-11T00:00:00.000Z"),
      randomId: () => "11111111-2222-3333-4444-555555555555"
    });

    const ref = vault.store("https://example.test:8443", { username: "alice@example.test", password: "top-secret-password" });

    expect(ref).toEqual({
      schema_version: "credential-ref-v1",
      id: "cred_11111111222233334444555555555555",
      domain: "https://example.test:8443",
      kind: "password",
      created_at: "2026-07-11T00:00:00.000Z",
      updated_at: "2026-07-11T00:00:00.000Z"
    });
    const persisted = readFileSync(filePath, "utf8");
    expect(persisted).not.toContain("alice@example.test");
    expect(persisted).not.toContain("top-secret-password");
    expect(vault.list("https://example.test:8443")).toEqual([ref]);
    expect(vault.resolve(ref.id)).toEqual({
      ref,
      secret: { username: "alice@example.test", password: "top-secret-password" }
    });
    vault.clear();
    expect(vault.list()).toEqual([]);
  });

  it("fails closed when the OS storage backend is unavailable or plaintext-only", () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-credentials-"));
    tempDirs.push(directory);
    const filePath = join(directory, "credential-vault.json");
    const storage = reversibleSafeStorage();
    storage.getSelectedStorageBackend = () => "basic_text";
    const vault = new CredentialVault({ filePath: () => filePath, storage });

    expect(() => vault.store("https://example.test", { username: "alice", password: "secret" }))
      .toThrow(/Secure OS credential storage is unavailable/);
  });

  it("requires exact HTTPS origins including non-default ports", () => {
    expect(credentialDomainFromUrl("https://Example.TEST/login")).toBe("https://example.test");
    expect(credentialDomainFromUrl("https://Example.TEST:8443/login")).toBe("https://example.test:8443");
    expect(() => credentialDomainFromUrl("http://example.test/login")).toThrow(/HTTPS/);
    expect(() => credentialDomainFromUrl("https://user:password@example.test/login")).toThrow(/URL credentials/);
  });
});

function reversibleSafeStorage() {
  return {
    isEncryptionAvailable: () => true,
    getSelectedStorageBackend: () => "dpapi",
    encryptString: (value: string) => Buffer.from([...value].reverse().join(""), "utf8"),
    decryptString: (value: Buffer) => [...value.toString("utf8")].reverse().join("")
  };
}
