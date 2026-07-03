import { app, BrowserWindow, dialog, shell, type IpcMainInvokeEvent } from "electron";
import { existsSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import type { ApiResponse } from "../shared/types";
import { ApiRequestValidationError, isPlainRecord, validateBridgePathValue } from "./ipcValidation";

export interface IpcPathGrantStores {
  documentPathGrants: Set<string>;
  revealPathGrants: Set<string>;
}

export function rememberDocumentPathGrant(grants: Set<string>, filePath: string): void {
  grants.add(normalizeGrantPath(filePath));
}

export function rememberRevealPathGrant(grants: Set<string>, filePath: string): void {
  grants.add(normalizeGrantPath(filePath));
}

export function rememberRevealPathFromApiResponse(grants: Set<string>, response: ApiResponse<unknown>): void {
  if (!response.ok || !isPlainRecord(response.data) || typeof response.data.path !== "string") {
    return;
  }
  rememberRevealPathGrant(grants, response.data.path);
}

export async function ensureDocumentReadGrant(
  event: IpcMainInvokeEvent,
  grants: Set<string>,
  paths: string[]
): Promise<void> {
  const ungranted = [...new Set(paths)].filter((filePath) => !grants.has(normalizeGrantPath(filePath)));
  if (!ungranted.length) {
    return;
  }

  if (typeof dialog.showMessageBox !== "function") {
    throw new ApiRequestValidationError("Document access requires a desktop confirmation dialog");
  }

  const window = BrowserWindow.fromWebContents(event.sender);
  const detail = ungranted.map((filePath) => `- ${filePath}`).join("\n");
  const options = {
    type: "question" as const,
    buttons: ["Allow for this app session", "Cancel"],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
    title: "Confirm document access",
    message: ungranted.length === 1 ? "Allow Lengrvis to read this document?" : "Allow Lengrvis to read these documents?",
    detail: `This may include document text. The selected path stays available until the desktop app exits.\n\n${detail}`
  };
  const result = window
    ? await dialog.showMessageBox(window, options)
    : await dialog.showMessageBox(options);

  if (result.response !== 0) {
    throw new ApiRequestValidationError("Document access was not confirmed");
  }
  for (const filePath of ungranted) {
    rememberDocumentPathGrant(grants, filePath);
  }
}

export async function getFileIconDataUrl(
  filePath: string,
  grants: IpcPathGrantStores
): Promise<string | null> {
  let resolved: string;
  try {
    resolved = resolvePath(validateBridgePathValue(filePath, "file icon path"));
  } catch {
    return null;
  }
  if (!isRevealPathAuthorized(resolved, grants)) {
    return null;
  }
  if (!existsSync(resolved)) {
    return null;
  }
  try {
    const icon = await app.getFileIcon(resolved, { size: "normal" });
    if (icon.isEmpty()) {
      return null;
    }
    return icon.toDataURL();
  } catch {
    return null;
  }
}

export function showItemInFolder(
  filePath: string,
  grants: IpcPathGrantStores
): { ok: boolean; path: string; revealed: boolean; shown: boolean; error?: string } {
  const resolved = resolvePath(filePath);
  if (!isRevealPathAuthorized(resolved, grants)) {
    return {
      ok: false,
      path: "",
      revealed: false,
      shown: false,
      error: "Path is not authorized for reveal"
    };
  }
  if (!existsSync(resolved)) {
    return { ok: false, path: resolved, revealed: false, shown: false, error: "Path does not exist" };
  }
  try {
    shell.showItemInFolder(resolved);
    return { ok: true, path: resolved, revealed: true, shown: true };
  } catch (error) { // broad-exception-boundary
    return {
      ok: false,
      path: resolved,
      revealed: false,
      shown: false,
      error: error instanceof Error ? error.message : "Could not reveal path"
    };
  }
}

function normalizeGrantPath(filePath: string): string {
  return resolvePath(filePath).toLowerCase();
}

function isRevealPathAuthorized(resolvedPath: string, grants: IpcPathGrantStores): boolean {
  const normalized = normalizeGrantPath(resolvedPath);
  if (grants.documentPathGrants.has(normalized) || grants.revealPathGrants.has(normalized)) {
    return true;
  }
  return defaultRevealRoots().some((root) => isSameOrNestedPath(root, resolvedPath));
}

function defaultRevealRoots(): string[] {
  const roots = [
    process.env.LENGRVIS_CONFIG_DIR,
    process.env.LENGRVIS_DATA_DIR,
    safeElectronAppPath("userData")
  ].filter((value): value is string => typeof value === "string" && value.trim().length > 0);
  return [...new Set(roots.map((root) => resolvePath(root)))];
}

function safeElectronAppPath(name: "userData"): string {
  try {
    return app.getPath(name);
  } catch {
    return "";
  }
}

function isSameOrNestedPath(rootPath: string, candidatePath: string): boolean {
  const root = resolvePath(rootPath).toLowerCase().replace(/[\\/]+$/, "");
  const candidate = resolvePath(candidatePath).toLowerCase();
  return candidate === root || candidate.startsWith(`${root}\\`) || candidate.startsWith(`${root}/`);
}
