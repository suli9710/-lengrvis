import { existsSync, linkSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript-legacy";
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

  it("cleans snapshots left by an exited process at initialization and explicit erase", async () => {
    const sandbox = mkdtempSync(join(tmpdir(), "lengrvis-screenshot-restart-test-"));
    tempDirs.push(sandbox);
    const root = join(sandbox, "screenshots");
    for (const name of ["browserScreenshotStore", "browserScreenshotCleanup"]) {
      const source = readFileSync(resolve("src/main", `${name}.ts`), "utf8");
      writeFileSync(join(sandbox, `${name}.js`), transpileModule(source, {
        compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 }
      }).outputText);
    }
    const child = spawnSync(process.execPath, ["-e", `
      const { BrowserScreenshotStore } = require(process.argv[1]);
      new BrowserScreenshotStore(process.argv[2]).save("child-session", Buffer.from("synthetic-private-png"))
        .then(url => process.stdout.write(url));
    `, join(sandbox, "browserScreenshotStore.js"), root], { encoding: "utf8" });
    expect(child.status, child.stderr).toBe(0);
    const orphan = fileURLToPath(child.stdout);
    expect(existsSync(orphan)).toBe(true);
    const restarted = new BrowserScreenshotStore(root);
    await restarted.initialize();
    expect(existsSync(orphan)).toBe(false);
    const next = await restarted.save("next-session", Buffer.from("next-private-png"));
    // Explicit deletion also works before any new screenshot was saved by a
    // replacement store, so it covers an immediate erase after a restart.
    await new BrowserScreenshotStore(root).clear();
    expect(existsSync(fileURLToPath(next))).toBe(false);
  });

  it("cleans only dead legacy processes with the exact application artifact layout", async () => {
    const sandbox = mkdtempSync(join(tmpdir(), "lengrvis-screenshot-legacy-test-"));
    tempDirs.push(sandbox);
    const uuid = "11111111-2222-3333-4444-555555555555";
    const legacy = join(sandbox, `lengrvis-browser-screenshots-2147483646-${uuid}`);
    const live = join(sandbox, `lengrvis-browser-screenshots-${process.pid}-${uuid}`);
    const unrelated = join(sandbox, "lengrvis-browser-screenshots-unrelated");
    for (const dir of [legacy, live, unrelated]) { mkdirSync(dir); writeFileSync(join(dir, `${uuid}.png`), "private"); }
    const store = new BrowserScreenshotStore(join(sandbox, "new-root"), undefined, sandbox);
    await store.initialize();
    expect(existsSync(legacy)).toBe(false);
    expect(existsSync(live)).toBe(true);
    expect(existsSync(unrelated)).toBe(true);
  });

  it("refuses unknown files and unowned fixed roots without deleting their contents", async () => {
    const root = mkdtempSync(join(tmpdir(), "lengrvis-screenshot-unowned-test-"));
    tempDirs.push(root);
    const original = join(root, "original.txt");
    writeFileSync(original, "must be preserved");
    await expect(new BrowserScreenshotStore(root).clear()).rejects.toThrow(/unowned/);
    expect(existsSync(original)).toBe(true);
  });

  it("refuses a foreign owner marker, directory junction, and hard-linked artifacts", async () => {
    const sandbox = mkdtempSync(join(tmpdir(), "lengrvis-screenshot-links-test-"));
    tempDirs.push(sandbox);
    const root = join(sandbox, "owned");
    const store = new BrowserScreenshotStore(root);
    const artifact = fileURLToPath(await store.save("session", Buffer.from("private")));
    writeFileSync(join(root, ".lengrvis-screenshot-owner"), "foreign owner");
    await expect(store.clear()).rejects.toThrow(/ownership/);
    expect(existsSync(artifact)).toBe(true);
    const junction = join(sandbox, "junction");
    symlinkSync(root, junction, "junction");
    await expect(new BrowserScreenshotStore(junction).clear()).rejects.toThrow(/linked/);
    expect(existsSync(artifact)).toBe(true);
    const hardRoot = join(sandbox, "hard-links");
    const hardStore = new BrowserScreenshotStore(hardRoot);
    const hardArtifact = fileURLToPath(await hardStore.save("session", Buffer.from("private")));
    const outside = join(sandbox, "outside-copy.png");
    linkSync(hardArtifact, outside);
    await expect(hardStore.clear()).rejects.toThrow(/unowned/);
    expect(existsSync(outside)).toBe(true);
  });
});
