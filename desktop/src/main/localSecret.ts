import { spawnSync } from "node:child_process";

import { safeStorage } from "electron";

export const LOCAL_SECRET_DPAPI_PREFIX = "dpapi:";
export const LOCAL_SECRET_SAFE_STORAGE_PREFIX = "safe:";
export const ALLOW_INSECURE_LOCAL_SECRETS_ENV = "LENGRVIS_ALLOW_INSECURE_LOCAL_SECRETS";

export function dpapiAvailable(): boolean {
  return process.platform === "win32";
}

export function safeStorageAvailable(): boolean {
  try {
    if (!safeStorage?.isEncryptionAvailable()) {
      return false;
    }
    const backend = typeof safeStorage.getSelectedStorageBackend === "function"
      ? safeStorage.getSelectedStorageBackend()
      : "";
    return backend !== "basic_text";
  } catch {
    return false;
  }
}

function insecurePlaintextAllowed(): boolean {
  return ["1", "true", "yes", "on"].includes(
    String(process.env[ALLOW_INSECURE_LOCAL_SECRETS_ENV] ?? "").trim().toLowerCase()
  );
}

export function protectLocalSecret(value: string): string {
  if (dpapiAvailable()) {
    return protectWithDpapi(value);
  }
  if (safeStorageAvailable()) {
    return `${LOCAL_SECRET_SAFE_STORAGE_PREFIX}${safeStorage.encryptString(value).toString("base64")}`;
  }
  if (insecurePlaintextAllowed()) {
    return value;
  }
  throw new Error(
    `Secure local secret storage is unavailable. Configure the OS keyring or set ${ALLOW_INSECURE_LOCAL_SECRETS_ENV}=1 for local development/tests only.`
  );
}

function protectWithDpapi(value: string): string {
  const script = [
    "$ErrorActionPreference = 'Stop'",
    `[Reflection.Assembly]::LoadWithPartialName('System.Security') | Out-Null`,
    "$plain = [Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())",
    `$protected = [Security.Cryptography.ProtectedData]::Protect($plain, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)`,
    "[Convert]::ToBase64String($protected)"
  ].join("; ");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    encoding: "utf-8",
    input: Buffer.from(value, "utf-8").toString("base64")
  });
  if (result.status !== 0) {
    throw new Error(result.stderr?.trim() || "Failed to encrypt local secret with Windows DPAPI.");
  }
  const encryptedB64 = result.stdout?.trim();
  if (!encryptedB64) {
    throw new Error("Failed to encrypt local secret with Windows DPAPI.");
  }
  return `${LOCAL_SECRET_DPAPI_PREFIX}${encryptedB64}`;
}

export function unprotectLocalSecret(stored: string): string {
  if (stored.startsWith(LOCAL_SECRET_SAFE_STORAGE_PREFIX)) {
    if (!safeStorageAvailable()) {
      throw new Error("Encrypted local secret requires OS safe storage on this platform.");
    }
    return safeStorage.decryptString(Buffer.from(stored.slice(LOCAL_SECRET_SAFE_STORAGE_PREFIX.length), "base64"));
  }
  if (stored.startsWith(LOCAL_SECRET_DPAPI_PREFIX)) {
    return unprotectWithDpapi(stored);
  }
  if (insecurePlaintextAllowed()) {
    return stored;
  }
  throw new Error("Refusing to read plaintext local secret without explicit insecure development/test opt-in.");
}

function unprotectWithDpapi(stored: string): string {
  if (!dpapiAvailable()) {
    throw new Error("Encrypted local secret requires Windows DPAPI on this platform.");
  }
  const encryptedB64 = stored.slice(LOCAL_SECRET_DPAPI_PREFIX.length);
  const script = [
    "$ErrorActionPreference = 'Stop'",
    `[Reflection.Assembly]::LoadWithPartialName('System.Security') | Out-Null`,
    "$protected = [Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())",
    `$plain = [Security.Cryptography.ProtectedData]::Unprotect($protected, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)`,
    "[Text.Encoding]::UTF8.GetString($plain)"
  ].join("; ");
  const result = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], {
    encoding: "utf-8",
    input: encryptedB64
  });
  if (result.status !== 0) {
    throw new Error(result.stderr?.trim() || "Failed to decrypt local secret with Windows DPAPI.");
  }
  const value = result.stdout?.trim();
  if (!value) {
    throw new Error("Failed to decrypt local secret with Windows DPAPI.");
  }
  return value;
}
