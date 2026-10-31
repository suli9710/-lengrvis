import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IpcMainInvokeEvent } from "electron";

const mocks = vi.hoisted(() => ({
  showMessageBox: vi.fn(),
  showItemInFolder: vi.fn()
}));
vi.mock("electron", () => ({
  app: { getPath: () => "" },
  BrowserWindow: { fromWebContents: () => null },
  dialog: { showMessageBox: mocks.showMessageBox },
  shell: { showItemInFolder: mocks.showItemInFolder }
}));
vi.mock("node:fs", () => ({ existsSync: () => true }));
import { ensureDocumentReadGrant, rememberDocumentPathGrant, showItemInFolder } from "./ipcPathGrants";

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubEnv("LENGRVIS_CONFIG_DIR", "");
  vi.stubEnv("LENGRVIS_DATA_DIR", "");
});
afterEach(() => { vi.unstubAllEnvs(); vi.restoreAllMocks(); });

describe("path grants", () => {
  it("does not grant a different case-sensitive document without confirmation", async () => {
    vi.spyOn(process, "platform", "get").mockReturnValue("linux");
    const grants = new Set<string>();
    rememberDocumentPathGrant(grants, "/documents/Report.txt");
    mocks.showMessageBox.mockResolvedValue({ response: 1 });
    await expect(ensureDocumentReadGrant({ sender: {} } as IpcMainInvokeEvent, grants, ["/documents/report.txt"]))
      .rejects.toThrow("not confirmed");
    expect(mocks.showMessageBox).toHaveBeenCalledOnce();
  });

  it("keeps Windows path grants case insensitive", async () => {
    vi.spyOn(process, "platform", "get").mockReturnValue("win32");
    const grants = new Set<string>();
    rememberDocumentPathGrant(grants, "/documents/Report.txt");
    await ensureDocumentReadGrant({ sender: {} } as IpcMainInvokeEvent, grants, ["/documents/report.txt"]);
    expect(mocks.showMessageBox).not.toHaveBeenCalled();
  });

  it("uses native case and separator rules while rejecting reveal-root siblings", () => {
    // Changing process.platform does not change node:path native semantics.
    const windows = process.platform === "win32";
    vi.stubEnv("LENGRVIS_DATA_DIR", "/data/Private");
    const grants = { documentPathGrants: new Set<string>(), revealPathGrants: new Set<string>() };
    expect(showItemInFolder("/data/private/secret.txt", grants).ok).toBe(windows);
    expect(showItemInFolder("/data/Private\\secret.txt", grants).ok).toBe(windows);
    expect(showItemInFolder("/data/PrivateSibling/secret.txt", grants).ok).toBe(false);
    expect(showItemInFolder("/data/Private/allowed.txt", grants).ok).toBe(true);
    expect(mocks.showItemInFolder).toHaveBeenCalledTimes(windows ? 3 : 1);
  });
});
