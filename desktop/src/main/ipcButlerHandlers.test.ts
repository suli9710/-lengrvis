import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BackendProcessManager } from "./backendProcess";
import { IPC_CHANNELS } from "../shared/ipc";

const mocks = vi.hoisted(() => ({
  handlers: new Map<string, (...args: unknown[]) => Promise<unknown>>(),
  proxy: vi.fn(), confirmation: vi.fn(), nativeHeaders: vi.fn(), ready: vi.fn()
}));
vi.mock("electron", () => ({ ipcMain: { handle: (name: string, handler: (...args: unknown[]) => Promise<unknown>) => mocks.handlers.set(name, handler) } }));
vi.mock("./rendererTrust", () => ({ assertTrustedRenderer: (event: unknown) => { if (event !== "trusted") throw new Error("unknown renderer"); } }));
vi.mock("./ipcBackendProxy", () => ({ proxyExplicitDesktopBridgeRequest: mocks.proxy, ensureBackendReadyForRendererSubmission: mocks.ready }));
vi.mock("./ipcNativeConfirmation", () => ({ confirmNativeDesktopAction: mocks.confirmation, nativeActionConfirmationHeaders: mocks.nativeHeaders }));
import { registerButlerIpcHandlers } from "./ipcButlerHandlers";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.handlers.clear();
  mocks.nativeHeaders.mockResolvedValue({ proof: "native" });
  registerButlerIpcHandlers({} as BackendProcessManager);
});

describe("Butler IPC boundaries", () => {
  it("rejects every untrusted caller and malformed request before proxying", async () => {
    for (const [name, channel] of Object.entries(IPC_CHANNELS).filter(([name]) => name.startsWith("butler"))) {
      const handler = mocks.handlers.get(channel)!;
      expect(handler, name).toBeTypeOf("function");
      await expect(handler("untrusted", {})).rejects.toThrow("unknown renderer");
      await expect(handler("trusted", "invalid")).rejects.toThrow();
    }
    expect(mocks.proxy).not.toHaveBeenCalled();
    expect(mocks.nativeHeaders).not.toHaveBeenCalled();
  });

  it("does not create a scope when native confirmation is cancelled", async () => {
    mocks.confirmation.mockRejectedValueOnce(new Error("cancelled"));
    await expect(mocks.handlers.get(IPC_CHANNELS.butlerScopeCreate)!("trusted", {
      label: "watch", directories: ["C:/docs"], applications: [], origins: [], account_id: "", allowed_actions: ["observe"]
    })).rejects.toThrow("cancelled");
    expect(mocks.proxy).not.toHaveBeenCalled();
  });
});
