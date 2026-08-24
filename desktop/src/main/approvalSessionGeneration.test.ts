import { EventEmitter } from "node:events";
import {
  existsSync,
  lstatSync,
  mkdtempSync,
  readFileSync,
  statSync,
  symlinkSync,
  unlinkSync,
  writeFileSync
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import {
  APPROVAL_SESSION_GENERATION_FILE,
  APPROVAL_SESSION_POWER_EVENTS,
  ApprovalSessionGenerationManager,
  ApprovalSessionVisibilityCoordinator,
  registerApprovalSessionPowerRotation,
  type ApprovalSessionPowerEvent,
  type ApprovalSessionPowerMonitor
} from "./approvalSessionGeneration";

const GENERATION_A = "A".repeat(43);
const GENERATION_B = "B".repeat(43);

describe("ApprovalSessionGenerationManager", () => {
  it("delays filesystem and signing side effects until primary-instance initialization", () => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-session-"));
    const generationPath = join(dataDir, APPROVAL_SESSION_GENERATION_FILE);
    const generate = vi.fn(() => GENERATION_A);
    const manager = new ApprovalSessionGenerationManager({ dataDir, generate });

    expect(existsSync(generationPath)).toBe(false);
    expect(() => manager.bindSigningPayload("approval-v4\nchallenge")).toThrow(/unavailable/);
    expect(() => manager.rotate()).toThrow(/primary app instance/);

    manager.initialize();
    manager.initialize();

    expect(readGeneration(dataDir)).toBe(GENERATION_A);
    expect(generate).toHaveBeenCalledTimes(1);
    expect(manager.bindSigningPayload("approval-v4\nchallenge")).toBe(
      `approval-v4\nchallenge\ndesktop-approval-session-v1\n${GENERATION_A}`
    );
    if (process.platform !== "win32") {
      expect(statSync(generationPath).mode & 0o077).toBe(0);
    }
  });

  it("does not let a denied second instance overwrite the primary generation", () => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-second-instance-"));
    const primary = new ApprovalSessionGenerationManager({ dataDir, generate: () => GENERATION_A });
    primary.initialize();

    const deniedSecondInstance = new ApprovalSessionGenerationManager({
      dataDir,
      generate: () => GENERATION_B
    });

    expect(readGeneration(dataDir)).toBe(GENERATION_A);
    expect(() => deniedSecondInstance.bindSigningPayload("approval-v4\nchallenge")).toThrow(/unavailable/);
    expect(() => deniedSecondInstance.rotate()).toThrow(/primary app instance/);
    expect(primary.bindSigningPayload("approval-v4\nchallenge")).toContain(GENERATION_A);
  });

  it("invalidates the old canonical generation before rejecting a malformed replacement", () => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-malformed-"));
    const primary = new ApprovalSessionGenerationManager({ dataDir, generate: () => GENERATION_A });
    primary.initialize();
    const malformedNextLaunch = new ApprovalSessionGenerationManager({
      dataDir,
      generate: () => `${GENERATION_B} `
    });

    expect(() => malformedNextLaunch.initialize()).toThrow(/malformed/);
    expect(existsSync(join(dataDir, APPROVAL_SESSION_GENERATION_FILE))).toBe(false);
    expect(() => malformedNextLaunch.bindSigningPayload("approval-v4\nchallenge")).toThrow(/unavailable/);
  });

  it("keeps the old generation invalid after replacement persistence fails", () => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-persist-failure-"));
    let failPersistence = false;
    const persistGeneration = vi.fn((filePath: string, generation: string) => {
      if (failPersistence) throw new Error("simulated persistence failure");
      writeFileSync(filePath, `${generation}\n`, {
        encoding: "utf-8",
        flag: "wx",
        mode: 0o600
      });
    });
    const generations = [GENERATION_A, GENERATION_B];
    const manager = new ApprovalSessionGenerationManager({
      dataDir,
      generate: () => generations.shift() ?? GENERATION_B,
      persistGeneration
    });
    manager.initialize();
    failPersistence = true;

    expect(() => manager.rotate()).toThrow(/persistence failure/);
    expect(existsSync(join(dataDir, APPROVAL_SESSION_GENERATION_FILE))).toBe(false);
    expect(() => manager.bindSigningPayload("approval-v4\nchallenge")).toThrow(/unavailable/);
  });

  it("deactivates a dangling canonical symlink without following it", (context) => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-symlink-"));
    const generationPath = join(dataDir, APPROVAL_SESSION_GENERATION_FILE);
    const missingTarget = join(dataDir, "missing-generation.secret");
    const manager = new ApprovalSessionGenerationManager({
      dataDir,
      generate: () => GENERATION_A
    });
    manager.initialize();
    unlinkSync(generationPath);
    try {
      symlinkSync(missingTarget, generationPath, "file");
    } catch (error) { // broad-exception-boundary: skip only known host symlink restrictions; rethrow every other failure.
      const code = String((error as NodeJS.ErrnoException).code ?? "");
      if (["EACCES", "ENOTSUP", "EPERM", "UNKNOWN"].includes(code)) {
        context.skip(`symlink creation is unavailable on this host (${code})`);
      }
      throw error;
    }

    manager.deactivate();

    expect(() => lstatSync(generationPath)).toThrow();
    expect(existsSync(missingTarget)).toBe(false);
    expect(() => manager.bindSigningPayload("approval-v4\nchallenge")).toThrow(/unavailable/);
  });

  it("invalidates the old canonical generation before invoking a failing generator", () => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-generate-failure-"));
    const generationPath = join(dataDir, APPROVAL_SESSION_GENERATION_FILE);
    let calls = 0;
    const manager = new ApprovalSessionGenerationManager({
      dataDir,
      generate: () => {
        calls += 1;
        if (calls === 1) return GENERATION_A;
        expect(existsSync(generationPath)).toBe(false);
        throw new Error("simulated generation failure");
      }
    });
    manager.initialize();

    expect(() => manager.rotate()).toThrow(/generation failure/);
    expect(existsSync(generationPath)).toBe(false);
    expect(() => manager.bindSigningPayload("approval-v4\nchallenge")).toThrow(/unavailable/);
  });

  it("rotates for every lock and power lifecycle boundary", () => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-power-"));
    const generations = ["A", "B", "C", "D", "E"].map((value) => value.repeat(43));
    let nextGeneration = 0;
    const manager = new ApprovalSessionGenerationManager({
      dataDir,
      generate: () => generations[nextGeneration++] ?? "Z".repeat(43)
    });
    manager.initialize();
    const monitor = new EventEmitter() as ApprovalSessionPowerMonitor & EventEmitter;
    const rotate = vi.fn((_event: ApprovalSessionPowerEvent) => manager.rotate());
    const onError = vi.fn();
    const dispose = registerApprovalSessionPowerRotation(monitor, rotate, onError);

    APPROVAL_SESSION_POWER_EVENTS.forEach((event, index) => {
      monitor.emit(event);
      expect(readGeneration(dataDir)).toBe(generations[index + 1]);
    });

    expect(rotate.mock.calls.map(([event]) => event)).toEqual(APPROVAL_SESSION_POWER_EVENTS);
    expect(onError).not.toHaveBeenCalled();

    dispose();
    for (const event of APPROVAL_SESSION_POWER_EVENTS) monitor.emit(event);
    expect(rotate).toHaveBeenCalledTimes(APPROVAL_SESSION_POWER_EVENTS.length);
  });

  it("routes rotation failures to the fail-closed handler", () => {
    const monitor = new EventEmitter() as ApprovalSessionPowerMonitor & EventEmitter;
    const failure = new Error("disk unavailable");
    const onError = vi.fn();
    registerApprovalSessionPowerRotation(monitor, () => {
      throw failure;
    }, onError);

    monitor.emit("lock-screen");

    expect(onError).toHaveBeenCalledWith("lock-screen", failure);
  });

  it("disables signing for the entire tray-background interval", () => {
    const dataDir = mkdtempSync(join(tmpdir(), "lengrvis-approval-background-"));
    const generations = [GENERATION_A, GENERATION_B];
    const manager = new ApprovalSessionGenerationManager({
      dataDir,
      generate: () => generations.shift() ?? GENERATION_B
    });
    manager.initialize();

    manager.deactivate();

    expect(existsSync(join(dataDir, APPROVAL_SESSION_GENERATION_FILE))).toBe(false);
    expect(() => manager.bindSigningPayload("approval-v4\nchallenge")).toThrow(/unavailable/);
    manager.rotate();
    expect(existsSync(join(dataDir, APPROVAL_SESSION_GENERATION_FILE))).toBe(false);

    manager.activate();
    expect(readGeneration(dataDir)).toBe(GENERATION_B);
    expect(manager.bindSigningPayload("approval-v4\nchallenge")).toContain(GENERATION_B);
  });
});

describe("ApprovalSessionVisibilityCoordinator", () => {
  it("revokes synchronously and serializes a background-to-foreground transition", async () => {
    const calls: string[] = [];
    const generation = {
      activate: vi.fn(() => calls.push("activate")),
      deactivate: vi.fn(() => calls.push("deactivate"))
    };
    const coordinator = new ApprovalSessionVisibilityCoordinator(generation);
    expect(coordinator.isForegroundRequested()).toBe(true);
    let releaseBackground!: () => void;
    const backgroundGate = new Promise<void>((resolve) => {
      releaseBackground = resolve;
    });

    const background = coordinator.enterBackground(async () => {
      calls.push("background:start");
      await backgroundGate;
      calls.push("background:end");
    });
    expect(coordinator.isForegroundRequested()).toBe(false);
    const foreground = coordinator.enterForeground(async () => {
      calls.push("foreground");
    });
    expect(coordinator.isForegroundRequested()).toBe(true);

    expect(calls).toEqual(["deactivate"]);
    expect(generation.activate).not.toHaveBeenCalled();
    releaseBackground();

    await expect(background).resolves.toBe(false);
    await expect(foreground).resolves.toBe(true);
    expect(calls).toEqual([
      "deactivate",
      "background:start",
      "background:end",
      "foreground",
      "activate"
    ]);
  });

  it("does not reactivate after a later background request wins", async () => {
    const calls: string[] = [];
    const generation = {
      activate: vi.fn(() => calls.push("activate")),
      deactivate: vi.fn(() => calls.push("deactivate"))
    };
    const coordinator = new ApprovalSessionVisibilityCoordinator(generation);
    let releaseForeground!: () => void;
    const foregroundGate = new Promise<void>((resolve) => {
      releaseForeground = resolve;
    });

    const foreground = coordinator.enterForeground(async () => {
      calls.push("foreground:start");
      await foregroundGate;
      calls.push("foreground:end");
    });
    await Promise.resolve();
    const background = coordinator.enterBackground(async () => {
      calls.push("background");
    });

    expect(calls).toEqual(["foreground:start", "deactivate"]);
    releaseForeground();

    await expect(foreground).resolves.toBe(false);
    await expect(background).resolves.toBe(true);
    expect(generation.activate).not.toHaveBeenCalled();
    expect(calls).toEqual(["foreground:start", "deactivate", "foreground:end", "background"]);
  });
});

function readGeneration(dataDir: string): string {
  return readFileSync(join(dataDir, APPROVAL_SESSION_GENERATION_FILE), "utf-8").trim();
}
