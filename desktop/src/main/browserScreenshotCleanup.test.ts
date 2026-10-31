import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  lstat: vi.fn(), readdir: vi.fn(), realpath: vi.fn(), rmdir: vi.fn(), unlink: vi.fn(),
  mkdir: vi.fn(), readFile: vi.fn(), writeFile: vi.fn()
}));
vi.mock("node:fs/promises", () => mocks);
// Reproduce Windows path identity on every CI host, not just Windows runners.
vi.mock("node:path", async (importOriginal) => {
  const path = await importOriginal<typeof import("node:path")>();
  return path.win32;
});
import { ownedScreenshotFiles } from "./browserScreenshotCleanup";
import { BrowserScreenshotStore } from "./browserScreenshotStore";

const alias = "C:\\Users\\RUNNER~1\\Temp\\screenshots";
const canonical = "C:\\Users\\runneradmin\\Temp\\screenshots";
const name = "11111111-2222-3333-4444-555555555555.png";
const artifact = `${canonical}\\${name}`;
const info = (extra = {}) => ({
  isSymbolicLink: () => false, isFile: () => true, nlink: 1,
  uid: process.getuid?.(), ...extra
});
beforeEach(() => {
  vi.resetAllMocks();
  mocks.realpath.mockResolvedValue(canonical);
  mocks.readdir.mockResolvedValue([name]);
  mocks.lstat.mockResolvedValue(info());
});

describe("screenshot cleanup canonical Windows paths", () => {
  it("enumerates, checks and returns canonical children when realpath expands an 8.3 alias", async () => {
    expect(await ownedScreenshotFiles(alias)).toEqual([artifact]);
    expect(mocks.realpath).toHaveBeenCalledWith(alias);
    expect(mocks.readdir).toHaveBeenCalledWith(canonical);
    expect(mocks.lstat).toHaveBeenCalledWith(artifact);
    expect(mocks.lstat).not.toHaveBeenCalledWith(`${alias}\\${name}`);
  });
  it("still rejects linked ancestors before canonicalizing aliases", async () => {
    mocks.lstat.mockImplementation(async (path: string) => info({ isSymbolicLink: () => path === "C:\\Users\\RUNNER~1" }));
    await expect(ownedScreenshotFiles(alias)).rejects.toThrow(/linked paths/);
    expect(mocks.realpath).not.toHaveBeenCalled();
  });
  it.each([
    { nlink: 2 },
    { isSymbolicLink: () => true },
    { isFile: () => false }
  ])("still rejects unsafe artifacts after alias expansion: %o", async (unsafe) => {
    mocks.lstat.mockImplementation(async (path: string) => info(path === artifact ? unsafe : {}));
    await expect(ownedScreenshotFiles(alias)).rejects.toThrow(/unowned artifacts or linked paths/);
  });
  it("still rejects unknown artifact names", async () => {
    mocks.readdir.mockResolvedValue(["unrelated.txt"]);
    await expect(ownedScreenshotFiles(alias)).rejects.toThrow(/unowned artifacts/);
  });
  it("allows only the explicitly designated owner marker", async () => {
    mocks.readdir.mockResolvedValue([".owner"]);
    expect(await ownedScreenshotFiles(alias, ".owner")).toEqual([`${canonical}\\.owner`]);
    await expect(ownedScreenshotFiles(alias)).rejects.toThrow(/unowned artifacts/);
  });
});


describe("screenshot store ownership with canonical children", () => {
  const markerName = ".lengrvis-screenshot-owner";
  const marker = `${canonical}\\${markerName}`;
  it("validates and removes canonical marker paths when the configured root is an 8.3 alias", async () => {
    mocks.readdir.mockReset()
      .mockResolvedValueOnce([markerName])
      .mockResolvedValueOnce([markerName, name])
      .mockResolvedValueOnce([]);
    mocks.readFile.mockResolvedValue(JSON.stringify({
      schema: 1, owner: createHash("sha256").update(homedir()).digest("hex")
    }));
    await new BrowserScreenshotStore(alias).initialize();
    expect(mocks.readFile).toHaveBeenCalledWith(marker, "utf8");
    expect(mocks.unlink.mock.calls).toEqual([[artifact], [marker]]);
    expect(mocks.rmdir).toHaveBeenCalledWith(canonical);
    expect(mocks.writeFile).toHaveBeenCalledOnce();
  });
  it("does not bypass a foreign owner marker through a short-name alias", async () => {
    mocks.readdir.mockResolvedValue([markerName]);
    mocks.readFile.mockResolvedValue("foreign owner");
    await expect(new BrowserScreenshotStore(alias).initialize()).rejects.toThrow(/ownership changed/);
    expect(mocks.unlink).not.toHaveBeenCalled();
    expect(mocks.writeFile).not.toHaveBeenCalled();
  });
});
