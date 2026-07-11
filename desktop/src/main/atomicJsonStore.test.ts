import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { writeJsonAtomically } from "./atomicJsonStore";

const tempDirs: string[] = [];

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

describe("writeJsonAtomically", () => {
  it("replaces an existing JSON record without leaving a partial temp file", () => {
    const dir = mkdtempSync(join(tmpdir(), "lengrvis-atomic-json-"));
    tempDirs.push(dir);
    const filePath = join(dir, "update-health.json");
    writeFileSync(filePath, JSON.stringify({ version: "old" }), "utf8");

    writeJsonAtomically(filePath, { version: "new", healthy: true });

    expect(JSON.parse(readFileSync(filePath, "utf8"))).toEqual({ version: "new", healthy: true });
    expect(readdirSync(dir)).toEqual(["update-health.json"]);
  });
});
