/**
 * Pure (Electron-free) state machine for post-update health verification and
 * rollback safety. The main process persists an UpdateHealthRecord and feeds
 * it through these functions; keeping the logic here lets it be unit-tested
 * under vitest (src/shared/**) without an Electron runtime.
 *
 * Model: after electron-updater installs a new version, the next launch is
 * "pending verification". A launch is proven healthy only after the app stays
 * up for a stable interval (the main process calls markHealthy). If a pending
 * version starts but never stabilizes (crash, hang, or the main process dies
 * before a clean quit), a beacon left in the record is detected on the
 * following launch and counted as a failed launch. Once failures reach
 * MAX_FAILED_LAUNCHES the version is quarantined so the main process can stop
 * auto-installing it and guide the user back to the last known-good build.
 */

export interface UpdateHealthRecord {
  /** Version installed by an update and awaiting health confirmation. */
  pendingVersion: string | null;
  /** ISO timestamp of when pendingVersion was first observed running. */
  pendingSince: string | null;
  /** Consecutive unstable launches observed for pendingVersion. */
  failedLaunches: number;
  /** Most recent version confirmed healthy (rollback reference). */
  lastGoodVersion: string | null;
  /** Versions proven unstable; auto-install must be suppressed for these. */
  quarantinedVersions: string[];
  /** Beacon: a launch is in progress and has not yet been confirmed/closed. */
  launchInProgress: boolean;
  /** Version associated with the in-progress launch beacon. */
  launchVersion: string | null;
}

/** Consecutive failed launches of a pending version before it is quarantined. */
export const MAX_FAILED_LAUNCHES = 2;

export type LaunchAction = "none" | "monitor" | "quarantine";

export interface LaunchReconciliation {
  record: UpdateHealthRecord;
  action: LaunchAction;
  /** Set when action === "quarantine": the version that was quarantined. */
  quarantinedVersion: string | null;
}

export function createInitialHealthRecord(): UpdateHealthRecord {
  return {
    pendingVersion: null,
    pendingSince: null,
    failedLaunches: 0,
    lastGoodVersion: null,
    quarantinedVersions: [],
    launchInProgress: false,
    launchVersion: null,
  };
}

/** Defensively normalize an unknown parsed value into a valid record. */
export function normalizeHealthRecord(
  value: Partial<UpdateHealthRecord> | null | undefined
): UpdateHealthRecord {
  const base = createInitialHealthRecord();
  if (!value || typeof value !== "object") {
    return base;
  }
  return {
    pendingVersion: typeof value.pendingVersion === "string" ? value.pendingVersion : null,
    pendingSince: typeof value.pendingSince === "string" ? value.pendingSince : null,
    failedLaunches:
      typeof value.failedLaunches === "number" && Number.isFinite(value.failedLaunches)
        ? Math.max(0, Math.floor(value.failedLaunches))
        : 0,
    lastGoodVersion: typeof value.lastGoodVersion === "string" ? value.lastGoodVersion : null,
    quarantinedVersions: Array.isArray(value.quarantinedVersions)
      ? value.quarantinedVersions.filter((v): v is string => typeof v === "string")
      : [],
    launchInProgress: value.launchInProgress === true,
    launchVersion: typeof value.launchVersion === "string" ? value.launchVersion : null,
  };
}

export function isQuarantined(record: UpdateHealthRecord, version: string | null): boolean {
  if (!version) {
    return false;
  }
  return record.quarantinedVersions.includes(version);
}

/** Record that electron-updater downloaded an update that will install on restart. */
export function markUpdatePending(record: UpdateHealthRecord, version: string): UpdateHealthRecord {
  if (!version) {
    return record;
  }
  return {
    ...record,
    quarantinedVersions: [...record.quarantinedVersions],
    pendingVersion: version,
    pendingSince: null,
    failedLaunches: 0,
  };
}

/**
 * Reconcile persisted health state with the version that is actually running
 * now. Detects an unstable previous launch via the beacon and decides whether
 * the running version should keep being monitored or be quarantined.
 */
export function reconcileLaunch(
  record: UpdateHealthRecord,
  runningVersion: string,
  now: string
): LaunchReconciliation {
  const next: UpdateHealthRecord = {
    ...record,
    quarantinedVersions: [...record.quarantinedVersions],
  };

  // 1) Detect an unstable previous launch via a left-open beacon.
  const priorLaunchUnstable = next.launchInProgress && next.launchVersion === runningVersion;
  if (priorLaunchUnstable && next.pendingVersion === runningVersion) {
    // A pending (just-updated) build started last time but never stabilized.
    next.failedLaunches += 1;
  } else if (next.launchInProgress && next.launchVersion !== runningVersion) {
    // Beacon belongs to a different version (manual reinstall/downgrade). Reset.
    next.failedLaunches = 0;
    if (next.pendingVersion === next.launchVersion) {
      next.pendingVersion = null;
      next.pendingSince = null;
    }
  }

  // 2) Begin pending tracking for the running version.
  if (next.pendingVersion === runningVersion && !next.pendingSince) {
    next.pendingSince = now;
  }

  // 3) Quarantine a pending build that has failed too many launches.
  if (next.pendingVersion === runningVersion && next.failedLaunches >= MAX_FAILED_LAUNCHES) {
    if (!next.quarantinedVersions.includes(runningVersion)) {
      next.quarantinedVersions.push(runningVersion);
    }
    next.pendingVersion = null;
    next.pendingSince = null;
    next.failedLaunches = 0;
    next.launchInProgress = false;
    next.launchVersion = null;
    return { record: next, action: "quarantine", quarantinedVersion: runningVersion };
  }

  // 4) Otherwise arm the beacon for this launch and keep monitoring.
  next.launchInProgress = true;
  next.launchVersion = runningVersion;
  return { record: next, action: "monitor", quarantinedVersion: null };
}

/**
 * Mark the running version healthy after it stayed up for a stable interval.
 * Promotes a pending version to lastGoodVersion and closes the beacon.
 */
export function markHealthy(
  record: UpdateHealthRecord,
  runningVersion: string,
  now: string
): UpdateHealthRecord {
  void now;
  const next: UpdateHealthRecord = {
    ...record,
    quarantinedVersions: [...record.quarantinedVersions],
  };
  next.lastGoodVersion = runningVersion;
  if (next.pendingVersion === runningVersion) {
    next.pendingVersion = null;
    next.pendingSince = null;
    next.failedLaunches = 0;
  }
  next.launchInProgress = false;
  next.launchVersion = null;
  return next;
}

/**
 * Close the launch beacon without promoting to healthy. Called on a clean quit
 * so that a normal short session is not mistaken for a crash on next launch.
 */
export function closeLaunchBeacon(record: UpdateHealthRecord): UpdateHealthRecord {
  if (!record.launchInProgress) {
    return record;
  }
  return {
    ...record,
    quarantinedVersions: [...record.quarantinedVersions],
    launchInProgress: false,
    launchVersion: null,
  };
}
