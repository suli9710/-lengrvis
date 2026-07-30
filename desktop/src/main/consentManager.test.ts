import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

const electronMocks = vi.hoisted(() => ({
  handle: vi.fn()
}));

vi.mock("electron", () => ({
  app: {
    getAppPath: () => process.cwd(),
    getPath: () => process.cwd(),
    isPackaged: false
  },
  ipcMain: { handle: electronMocks.handle }
}));

import { readConsentRecord, writeConsentRecord } from "./consentManager";

const temporaryDirectories: string[] = [];
const originalDataDir = process.env.LENGRVIS_DATA_DIR;

afterEach(() => {
  if (originalDataDir === undefined) delete process.env.LENGRVIS_DATA_DIR;
  else process.env.LENGRVIS_DATA_DIR = originalDataDir;
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe("consentManager", () => {
  it("atomically persists a complete consent record", () => {
    const directory = mkdtempSync(join(tmpdir(), "lengrvis-consent-"));
    temporaryDirectories.push(directory);
    process.env.LENGRVIS_DATA_DIR = directory;

    const stored = writeConsentRecord({
      eula_version: "2026-07",
      eula_accepted_at: "2026-07-31T00:00:00.000Z"
    });

    expect(readConsentRecord()).toEqual(stored);
    expect(JSON.parse(readFileSync(join(directory, "consent.json"), "utf8"))).toEqual(stored);
    expect(readdirSync(directory)).toEqual(["consent.json"]);
  });
});
