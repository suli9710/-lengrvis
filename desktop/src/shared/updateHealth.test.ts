import { describe, expect, it } from "vitest";

import {
  MAX_FAILED_LAUNCHES,
  closeLaunchBeacon,
  createInitialHealthRecord,
  isQuarantined,
  markHealthy,
  markUpdatePending,
  normalizeHealthRecord,
  reconcileLaunch,
} from "./updateHealth";

const NOW = "2026-06-22T00:00:00.000Z";

describe("updateHealth", () => {
  it("creates an empty initial record", () => {
    const record = createInitialHealthRecord();
    expect(record.pendingVersion).toBeNull();
    expect(record.lastGoodVersion).toBeNull();
    expect(record.failedLaunches).toBe(0);
    expect(record.quarantinedVersions).toEqual([]);
    expect(record.launchInProgress).toBe(false);
  });

  it("monitors a fresh launch and promotes it to last-good when healthy", () => {
    const initial = createInitialHealthRecord();
    const launch = reconcileLaunch(initial, "1.0.0", NOW);
    expect(launch.action).toBe("monitor");
    expect(launch.record.launchInProgress).toBe(true);
    expect(launch.record.launchVersion).toBe("1.0.0");

    const healthy = markHealthy(launch.record, "1.0.0", NOW);
    expect(healthy.lastGoodVersion).toBe("1.0.0");
    expect(healthy.launchInProgress).toBe(false);
  });

  it("promotes a pending update to last-good after a healthy launch", () => {
    let record = markHealthy(createInitialHealthRecord(), "1.0.0", NOW);
    record = markUpdatePending(record, "1.1.0");
    expect(record.pendingVersion).toBe("1.1.0");

    const launch = reconcileLaunch(record, "1.1.0", NOW);
    expect(launch.action).toBe("monitor");
    expect(launch.record.pendingSince).toBe(NOW);

    const healthy = markHealthy(launch.record, "1.1.0", NOW);
    expect(healthy.pendingVersion).toBeNull();
    expect(healthy.lastGoodVersion).toBe("1.1.0");
    expect(healthy.failedLaunches).toBe(0);
  });

  it("quarantines a pending update that crash-loops on launch", () => {
    let record = markUpdatePending(createInitialHealthRecord(), "1.1.0");

    // Each launch arms the beacon; the app crashes before markHealthy runs, so
    // the beacon stays open and the next launch counts a failure.
    let action = "monitor";
    let quarantinedVersion: string | null = null;
    for (let i = 0; i <= MAX_FAILED_LAUNCHES; i += 1) {
      const launch = reconcileLaunch(record, "1.1.0", NOW);
      record = launch.record;
      action = launch.action;
      quarantinedVersion = launch.quarantinedVersion;
    }

    expect(action).toBe("quarantine");
    expect(quarantinedVersion).toBe("1.1.0");
    expect(isQuarantined(record, "1.1.0")).toBe(true);
    expect(record.pendingVersion).toBeNull();
  });

  it("never quarantines a non-pending (already good) version that crashes", () => {
    let record = markHealthy(createInitialHealthRecord(), "1.0.0", NOW);
    for (let i = 0; i < 5; i += 1) {
      const launch = reconcileLaunch(record, "1.0.0", NOW);
      record = launch.record;
      expect(launch.action).toBe("monitor");
    }
    expect(isQuarantined(record, "1.0.0")).toBe(false);
    expect(record.quarantinedVersions).toEqual([]);
  });

  it("closeLaunchBeacon clears the beacon without promoting", () => {
    const launch = reconcileLaunch(createInitialHealthRecord(), "1.0.0", NOW);
    const closed = closeLaunchBeacon(launch.record);
    expect(closed.launchInProgress).toBe(false);
    expect(closed.launchVersion).toBeNull();
    expect(closed.lastGoodVersion).toBeNull();
  });

  it("resets failure tracking when the beacon belongs to another version", () => {
    let record = markUpdatePending(createInitialHealthRecord(), "1.1.0");
    record = reconcileLaunch(record, "1.1.0", NOW).record; // beacon armed for 1.1.0
    // User manually reinstalls the older build instead of the pending one.
    const launch = reconcileLaunch(record, "1.0.0", NOW);
    expect(launch.action).toBe("monitor");
    expect(launch.record.failedLaunches).toBe(0);
    expect(launch.record.pendingVersion).toBeNull();
  });

  it("normalizes malformed persisted data", () => {
    const normalized = normalizeHealthRecord({
      pendingVersion: 123 as unknown as string,
      failedLaunches: -5,
      quarantinedVersions: ["1.0.0", 7 as unknown as string],
      launchInProgress: "yes" as unknown as boolean,
    });
    expect(normalized.pendingVersion).toBeNull();
    expect(normalized.failedLaunches).toBe(0);
    expect(normalized.quarantinedVersions).toEqual(["1.0.0"]);
    expect(normalized.launchInProgress).toBe(false);
  });
});
