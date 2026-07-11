import { app } from "electron";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import {
  closeLaunchBeacon,
  createInitialHealthRecord,
  isQuarantined,
  markHealthy,
  markUpdatePending,
  normalizeHealthRecord,
  reconcileLaunch,
  type LaunchAction,
  type UpdateHealthRecord,
} from "../shared/updateHealth";
import { writeJsonAtomically } from "./atomicJsonStore";

/**
 * Main-process persistence + orchestration for the post-update health gate.
 * The pure decision logic lives in shared/updateHealth.ts (and is unit-tested);
 * this module only handles reading/writing the record to userData and exposing
 * a small API to autoUpdater.ts and main.ts.
 */

const FILENAME = "update-health.json";

let cache: UpdateHealthRecord | null = null;

function getDataDir(): string {
  const envDir = process.env.LENGRVIS_DATA_DIR;
  if (envDir && envDir.trim()) {
    return envDir;
  }
  return app.getPath("userData");
}

function getFilePath(): string {
  return join(getDataDir(), FILENAME);
}

function read(): UpdateHealthRecord {
  const filePath = getFilePath();
  if (!existsSync(filePath)) {
    return createInitialHealthRecord();
  }
  try {
    return normalizeHealthRecord(
      JSON.parse(readFileSync(filePath, "utf-8")) as Partial<UpdateHealthRecord>
    );
  } catch {
    return createInitialHealthRecord();
  }
}

function getRecord(): UpdateHealthRecord {
  if (!cache) {
    cache = read();
  }
  return cache;
}

function persist(next: UpdateHealthRecord): void {
  try {
    const filePath = getFilePath();
    writeJsonAtomically(filePath, next);
    cache = next;
  } catch (error) { // broad-exception-boundary
    console.warn("Failed to persist update health record:", error);
  }
}

export interface StartupHealthResult {
  action: LaunchAction;
  runningVersion: string;
  quarantinedVersion: string | null;
}

/** Record that electron-updater downloaded an update that will install on restart. */
export function noteUpdateDownloaded(version: string | null | undefined): void {
  if (!version) {
    return;
  }
  persist(markUpdatePending(getRecord(), version));
}

/** Reconcile persisted health with the running version. Call once on startup. */
export function reconcileOnStartup(runningVersion: string): StartupHealthResult {
  const result = reconcileLaunch(getRecord(), runningVersion, new Date().toISOString());
  persist(result.record);
  return {
    action: result.action,
    runningVersion,
    quarantinedVersion: result.quarantinedVersion,
  };
}

/** Mark the running version healthy after a stable uptime interval. */
export function confirmHealthy(runningVersion: string): void {
  persist(markHealthy(getRecord(), runningVersion, new Date().toISOString()));
}

/** Close the launch beacon on a clean quit (a normal short session is not a crash). */
export function closeLaunch(): void {
  persist(closeLaunchBeacon(getRecord()));
}

export function isVersionQuarantined(version: string | null | undefined): boolean {
  return isQuarantined(getRecord(), version ?? null);
}

export function getLastGoodVersion(): string | null {
  return getRecord().lastGoodVersion;
}
