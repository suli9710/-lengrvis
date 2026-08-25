import { describe, expect, it, vi } from "vitest";

vi.mock("electron", () => ({
  app: {
    getAppPath: () => process.cwd(),
    getPath: () => process.cwd(),
    isPackaged: false
  },
  safeStorage: {
    decryptString: () => "",
    encryptString: () => Buffer.alloc(0),
    getSelectedStorageBackend: () => "mock_keychain",
    isEncryptionAvailable: () => true
  }
}));

import { createBackendProcessOutputLogHandler } from "./backendProcess";

describe("backend process persistent logging boundary", () => {
  it.each(["stdout", "stderr"] as const)("omits untrusted %s content", (channel) => {
    const secret = "Authorization: Bearer network-secret-value";
    const writer = vi.fn();
    const handler = createBackendProcessOutputLogHandler(channel, writer);

    (handler as (untrustedOutput: unknown) => void)(secret);
    (handler as (untrustedOutput: unknown) => void)("second untrusted chunk");

    expect(writer).toHaveBeenCalledOnce();
    expect(writer).toHaveBeenCalledWith(`[${channel}] output received; content omitted from persistent logs`);
    expect(writer.mock.calls.flat().join(" ")).not.toContain(secret);
    expect(writer.mock.calls.flat().join(" ")).not.toContain("network-secret-value");
  });
});
