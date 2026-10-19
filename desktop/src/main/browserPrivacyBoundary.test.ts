import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { BrowserPrivacyBoundary } from "./browserPrivacyBoundary";

const directories: string[] = [];
afterEach(() => { for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true }); });

describe("BrowserPrivacyBoundary", () => {
  it("excludes in-flight operations and keeps a pending erase across process recreation", () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-privacy-boundary-"));
    directories.push(directory);
    const marker = join(directory, "pending.json");
    const boundary = new BrowserPrivacyBoundary(() => marker);
    const finishOperation = boundary.enter();
    expect(() => boundary.reserve()).toThrow(/浏览器操作正在运行/);
    expect(existsSync(marker)).toBe(false);
    finishOperation();
    expect(boundary.reserve()).toBe(false);
    expect(() => boundary.enter()).toThrow(/清除尚未完成/);
    const restarted = new BrowserPrivacyBoundary(() => marker);
    expect(() => restarted.enter()).toThrow(/清除尚未完成/);
    expect(restarted.reserve()).toBe(true);
    restarted.release();
    expect(() => restarted.enter()()).not.toThrow();
    expect(existsSync(marker)).toBe(false);
  });
});
