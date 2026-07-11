import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import { BrowserScreenshotStore } from "./browserScreenshotStore";

const tempDirs: string[] = [];

afterEach(async () => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("BrowserScreenshotStore", () => {
  it("evicts old artifacts to enforce per-session and global byte budgets", async () => {
    const root = mkdtempSync(join(tmpdir(), "lengrvis-screenshot-store-test-"));
    tempDirs.push(root);
    const store = new BrowserScreenshotStore(root, {
      maxArtifactBytes: 8,
      maxArtifactsPerSession: 2,
      maxBytesPerSession: 10,
      maxBytesGlobal: 14
    });

    const first = await store.save("session-a", Buffer.alloc(6, 1));
    const second = await store.save("session-a", Buffer.alloc(6, 2));
    expect(existsSync(fileURLToPath(first))).toBe(false);
    expect(existsSync(fileURLToPath(second))).toBe(true);

    const third = await store.save("session-b", Buffer.alloc(6, 3));
    const fourth = await store.save("session-c", Buffer.alloc(6, 4));
    expect(existsSync(fileURLToPath(second))).toBe(false);
    expect(existsSync(fileURLToPath(third))).toBe(true);
    expect(existsSync(fileURLToPath(fourth))).toBe(true);

    await store.clear();
    expect(existsSync(fileURLToPath(third))).toBe(false);
    expect(existsSync(fileURLToPath(fourth))).toBe(false);
  });
});
