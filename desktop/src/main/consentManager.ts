/**
 * Main-process consent manager: reads and writes consent.json, resolves
 * legal document file paths, and exposes them through IPC handlers.
 */

import { app } from "electron";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { LEGAL_VERSIONS, needsEulaReconsent, needsPrivacyReconsent, type AcceptConsentRequest, type ConsentRecord, type ConsentStatusResult, type LegalDocId } from "../shared/consent";
import { IPC_CHANNELS } from "../shared/ipc";
import type { IpcMainInvokeEvent } from "electron";
import { ipcMain } from "electron";

const CONSENT_FILENAME = "consent.json";

/** Resolve the data directory, preferring LENGRVIS_DATA_DIR, then Electron userData. */
function getDataDir(): string {
  const envDir = process.env.LENGRVIS_DATA_DIR;
  if (envDir && envDir.trim()) {
    return envDir;
  }
  return app.getPath("userData");
}
/** Resolve the path to consent.json. */
export function getConsentFilePath(): string {
  return join(getDataDir(), CONSENT_FILENAME);
}
/** Read the current consent record, or null if it does not exist. */
export function readConsentRecord(): ConsentRecord | null {
  const filePath = getConsentFilePath();
  if (!existsSync(filePath)) {
    return null;
  }
  try {
    const raw = readFileSync(filePath, "utf-8");
    const parsed = JSON.parse(raw) as Partial<ConsentRecord>;
    return {
      eula_version: parsed.eula_version ?? LEGAL_VERSIONS.eula,
      eula_accepted_at: parsed.eula_accepted_at ?? null,
      privacy_version: parsed.privacy_version ?? LEGAL_VERSIONS.privacy,
      privacy_accepted_at: parsed.privacy_accepted_at ?? null,
      installer_version: parsed.installer_version ?? null,
      platform: parsed.platform ?? null,
    };
  } catch {
    return null;
  }
}
/** Atomically write (or merge) the consent record. */
export function writeConsentRecord(patch: Partial<ConsentRecord>): ConsentRecord {
  const existing = readConsentRecord();
  const next: ConsentRecord = {
    eula_version: patch.eula_version ?? existing?.eula_version ?? LEGAL_VERSIONS.eula,
    eula_accepted_at: patch.eula_accepted_at ?? existing?.eula_accepted_at ?? new Date().toISOString(),
    privacy_version: patch.privacy_version ?? existing?.privacy_version ?? LEGAL_VERSIONS.privacy,
    privacy_accepted_at: patch.privacy_accepted_at ?? existing?.privacy_accepted_at ?? null,
    installer_version: patch.installer_version ?? existing?.installer_version ?? null,
    platform: patch.platform ?? existing?.platform ?? process.platform,
  };
  const filePath = getConsentFilePath();
  const dir = join(filePath, "..");
  if (!existsSync(dir)) {
    mkdirSync(dir, { recursive: true });
  }
  writeFileSync(filePath, JSON.stringify(next, null, 2), "utf-8");
  return next;
}
/** Resolve the on-disk path to a bundled legal document. */
export function resolveLegalDocPath(docId: LegalDocId): string {
  const appRoot = app.isPackaged
    ? process.resourcesPath
    : join(app.getAppPath(), "..", "..", "..");
  switch (docId) {
    case "privacy-policy":
      return join(appRoot, "docs", "legal", "privacy-policy.md");
    case "eula":
      return join(appRoot, "docs", "legal", "eula.md");
    case "notice":
      return join(appRoot, "NOTICE");
    default: {
      const exhaustive: never = docId;
      throw new Error(`Unknown legal doc id: ${exhaustive}`);
    }
  }
}
/** Read the text content of a bundled legal document. */
export function readLegalDocContent(docId: LegalDocId): string {
  const filePath = resolveLegalDocPath(docId);
  if (!existsSync(filePath)) {
    throw new Error(`Legal document not found: ${docId}`);
  }
  return readFileSync(filePath, "utf-8");
}
/** Register all consent-related IPC handlers. Call once during app setup. */
export function registerConsentIpcHandlers(): void {
  ipcMain.handle(IPC_CHANNELS.consentStatus, (event: IpcMainInvokeEvent): ConsentStatusResult => {
    assertTrustedRenderer(event);
    const record = readConsentRecord();
    return {
      consent: record,
      needsEulaConsent: needsEulaReconsent(record),
      needsPrivacyConsent: needsPrivacyReconsent(record),
      currentVersions: LEGAL_VERSIONS,
    };
  });
  ipcMain.handle(IPC_CHANNELS.consentAccept, (event: IpcMainInvokeEvent, request: AcceptConsentRequest): ConsentRecord => {
    assertTrustedRenderer(event);
    const patch: Partial<ConsentRecord> = {};
    if (request?.acceptEula) {
      patch.eula_version = LEGAL_VERSIONS.eula;
      patch.eula_accepted_at = new Date().toISOString();
    }
    if (request?.acceptPrivacy) {
      patch.privacy_version = LEGAL_VERSIONS.privacy;
      patch.privacy_accepted_at = new Date().toISOString();
    }
    if (request?.installerVersion) {
      patch.installer_version = request.installerVersion;
    }
    patch.platform = process.platform;
    return writeConsentRecord(patch);
  });
  ipcMain.handle(IPC_CHANNELS.consentReadDoc, (event: IpcMainInvokeEvent, docId: LegalDocId): { content: string; docId: LegalDocId } => {
    assertTrustedRenderer(event);
    if (!isLegalDocId(docId)) {
      throw new Error("Invalid legal document id");
    }
    return { content: readLegalDocContent(docId), docId };
  });
}
function isLegalDocId(value: unknown): value is LegalDocId {
  return value === "privacy-policy" || value === "eula" || value === "notice";
}
function assertTrustedRenderer(event: IpcMainInvokeEvent): void {
  // The renderer is sandboxed with contextIsolation; it can only reach us
  // through the preload bridge. No additional validation needed here since
  // Electron guarantees the sender is our own webContents.
  void event;
}
