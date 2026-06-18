import { spawnSync } from "node:child_process";

export const LOCAL_SECRET_DPAPI_PREFIX = "dpapi:";

export function dpapiAvailable(): boolean {
  return process.platform === "win32";
}

export function protectLocalSecret(value: string): string {
  if (!dpapiAvailable()) {
    return value;
  }
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
  if (!stored.startsWith(LOCAL_SECRET_DPAPI_PREFIX)) {
    return stored;
  }
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
