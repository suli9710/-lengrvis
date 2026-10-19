import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserHost } from "./browserHost";
import { BrowserProfileRegistry } from "./browserProfileRegistry";
import { BrowserScreenshotStore } from "./browserScreenshotStore";

const directories: string[] = [];
afterEach(() => { for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true }); });

describe("BrowserHost privacy cleanup", () => {
  it("closes a page before a failed screenshot removal and reports partial cleanup", async () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-browser-partial-erase-"));
    directories.push(directory);
    const screenshots = new BrowserScreenshotStore(join(directory, "screenshots"));
    const closed = vi.fn();
    const clearStorageData = vi.fn(async () => { expect(closed).toHaveBeenCalled(); });
    const clearCache = vi.fn(async () => {});
    vi.spyOn(screenshots, "removeSession").mockImplementation(async () => {
      expect(closed).toHaveBeenCalled();
      throw new Error("synthetic locked screenshot");
    });
    vi.spyOn(screenshots, "clear").mockResolvedValue();
    const host = new BrowserHost(() => null,
      { clear: vi.fn() } as never, { clear: vi.fn() } as never,
      new BrowserProfileRegistry(() => join(directory, "profiles.json"), async () => {}), screenshots);
    const entry = {
      container: { kind: "browserView", view: { webContents: {
        session: { clearStorageData, clearCache }, isDestroyed: () => false, close: closed
      } } },
      session: { id: "session", task_id: "task", current_url: "https://example.test/", title: "Page", status: "idle", mode: "watch" },
      events: [], submissionGuard: { close: vi.fn() }
    };
    (host as unknown as { sessions: Map<string, unknown> }).sessions.set("session", entry);
    await expect(host.eraseLocalPrivateData()).rejects.toThrow(/部分数据未能删除/);
    expect(closed).toHaveBeenCalledExactlyOnceWith({ waitForBeforeUnload: false });
    expect(clearStorageData).toHaveBeenCalledOnce();
    expect(clearCache).toHaveBeenCalledOnce();
    expect(host.getSnapshot().sessions).toEqual([]);
  });
});
