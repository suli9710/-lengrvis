import { app, crashReporter } from "electron";
import type { WebContents } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Crash reporting pipeline.
 *
 * Privacy-first by design: Electron's crashReporter collects minidumps locally
 * (under the OS crashDumps directory) and never uploads them unless an explicit
 * HTTPS endpoint is configured via LENGRVIS_CRASH_SUBMIT_URL. We additionally
 * keep a compact, redacted JSONL log of process-gone events that feeds the
 * post-update rollback health gate and the diagnostics export. We deliberately
 * record only non-PII fields (process type, reason, exit code, app version) and
 * never attach file paths, arguments, or user content.
 */

const CRASH_LOG_FILENAME = "crash-reports.log";
const MAX_CRASH_RECORDS = 50;

export interface CrashRecord {
  ts: string;
  process: string;
  reason: string;
  exitCode: number;
  version: string;
  serviceName?: string;
}

let listenersRegistered = false;

function getDataDir(): string {
  const envDir = process.env.LENGRVIS_DATA_DIR;
  if (envDir && envDir.trim()) {
    return envDir;
  }
  return app.getPath("userData");
}

export function getCrashLogPath(): string {
  return join(getDataDir(), CRASH_LOG_FILENAME);
}

function resolveSubmitUrl(): string {
  const url = process.env.LENGRVIS_CRASH_SUBMIT_URL;
  return url && /^https:\/\//i.test(url.trim()) ? url.trim() : "";
}

/**
 * Start Electron's crash collector and register process-gone listeners.
 * Safe to call once, as early as possible during startup (before app ready).
 */
export function setupCrashReporter(): void {
  const submitURL = resolveSubmitUrl();
  try {
    crashReporter.start({
      productName: "Lengrvis",
      companyName: "Lengrvis",
      submitURL,
      uploadToServer: submitURL.length > 0,
      compress: true,
      // Only safe, non-PII metadata. Never attach paths, args, or user content.
      globalExtra: { app_version: app.getVersion() },
    });
  } catch (error) { // broad-exception-boundary
    console.warn("crashReporter.start failed; continuing without it:", error);
  }
  registerProcessCrashListeners();
}

function registerProcessCrashListeners(): void {
  if (listenersRegistered) {
    return;
  }
  listenersRegistered = true;

  app.on("child-process-gone", (_event, details) => {
    if (details.reason === "clean-exit" || details.reason === "killed") {
      return;
    }
    appendCrashRecord({
      ts: new Date().toISOString(),
      process: details.type ?? "child",
      reason: String(details.reason),
      exitCode: typeof details.exitCode === "number" ? details.exitCode : -1,
      version: app.getVersion(),
      serviceName: details.serviceName,
    });
  });

  app.on("web-contents-created", (_event, contents: WebContents) => {
    contents.on("render-process-gone", (_e, details) => {
      if (details.reason === "clean-exit") {
        return;
      }
      appendCrashRecord({
        ts: new Date().toISOString(),
        process: "renderer",
        reason: String(details.reason),
        exitCode: typeof details.exitCode === "number" ? details.exitCode : -1,
        version: app.getVersion(),
      });
    });
  });
}

export function readCrashRecords(): CrashRecord[] {
  try {
    const filePath = getCrashLogPath();
    if (!existsSync(filePath)) {
      return [];
    }
    return readFileSync(filePath, "utf-8")
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
      .map((line) => {
        try {
          return JSON.parse(line) as CrashRecord;
        } catch {
          return null;
        }
      })
      .filter((record): record is CrashRecord => record !== null && typeof record.ts === "string");
  } catch {
    return [];
  }
}

function appendCrashRecord(record: CrashRecord): void {
  try {
    const filePath = getCrashLogPath();
    const dir = join(filePath, "..");
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }
    const records = readCrashRecords();
    records.push(record);
    const trimmed = records.slice(-MAX_CRASH_RECORDS);
    writeFileSync(filePath, `${trimmed.map((r) => JSON.stringify(r)).join("\n")}\n`, "utf-8");
  } catch (error) { // broad-exception-boundary
    console.warn("Failed to append crash record:", error);
  }
}
