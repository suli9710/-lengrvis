import { randomBytes } from "node:crypto";
import { chmodSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync, unlinkSync, writeSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { cwd as getCwd } from "node:process";

import { app } from "electron";

import { protectLocalSecret, unprotectLocalSecret } from "./localSecret";

export const DESKTOP_API_TOKEN_FILE = "desktop_api.secret";

const CONFIG_PARENT_SEARCH_DEPTH = 5;
const TOKEN_BYTES = 32;
const DEFAULT_DATA_DIR_NAME = ".lengrvis_data";

type DesktopApiTokenSource = "file" | "env" | "created";

export class DesktopApiTokenPersistError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DesktopApiTokenPersistError";
  }
}

export interface DesktopApiTokenResolution {
  token: string;
  tokenPath: string;
  dataDir: string;
  configDir: string;
  source: DesktopApiTokenSource;
}

export interface DesktopApiTokenOptions {
  command?: string;
  configDir?: string;
  dataDir?: string;
  env?: NodeJS.ProcessEnv;
  generateToken?: () => string;
}

export function resolveDesktopApiToken(options: DesktopApiTokenOptions = {}): DesktopApiTokenResolution {
  const env = options.env ?? process.env;
  const configDir = options.configDir
    ? resolvePath(options.configDir, getCwd())
    : resolveBackendConfigDir({ command: options.command, env });
  const dataDir = resolveBackendDataDir({ configDir, dataDir: options.dataDir, env });
  const tokenPath = join(dataDir, DESKTOP_API_TOKEN_FILE);
  const fileToken = readTokenFile(tokenPath);

  if (fileToken) {
    return { token: fileToken, tokenPath, dataDir, configDir, source: "file" };
  }

  const envToken = firstEnv(env, "LENGRVIS_DESKTOP_API_TOKEN");
  if (envToken) {
    const persisted = persistTokenIfAbsent(tokenPath, envToken, "env");
    return { tokenPath, dataDir, configDir, ...persisted };
  }

  const generatedToken = options.generateToken?.() ?? randomBytes(TOKEN_BYTES).toString("hex");
  const persisted = persistTokenIfAbsent(tokenPath, generatedToken, "created");
  return { tokenPath, dataDir, configDir, ...persisted };
}

export function resolveBackendConfigDir(options: { command?: string; env?: NodeJS.ProcessEnv } = {}): string {
  const env = options.env ?? process.env;
  const explicit = firstEnv(env, "LENGRVIS_CONFIG_DIR");
  if (explicit) {
    return resolvePath(explicit, getCwd());
  }

  for (const candidate of configSearchRoots(options.command)) {
    const configDir = findAncestorWithAny(candidate, [".env", "config.yaml"]);
    if (configDir) {
      return configDir;
    }
  }

  for (const candidate of configSearchRoots(options.command)) {
    const projectRoot = findAncestorWithAny(candidate, [
      join("backend", "app", "config.py"),
      join("scripts", "install_service.ps1"),
      "config.example.yaml"
    ]);
    if (projectRoot) {
      return projectRoot;
    }
  }

  return safeResolve(getCwd());
}

export function resolveBackendDataDir(options: {
  configDir: string;
  dataDir?: string;
  env?: NodeJS.ProcessEnv;
}): string {
  const env = options.env ?? process.env;
  const configDir = safeResolve(options.configDir);
  const dotenv = readDotenv(join(configDir, ".env"));
  const configuredDataDir =
    firstToken(options.dataDir, firstEnv(env, "LENGRVIS_DATA_DIR"), firstEnv(dotenv, "LENGRVIS_DATA_DIR"))
    ?? readConfigYamlDataDir(join(configDir, "config.yaml"));

  return configuredDataDir ? resolvePath(configuredDataDir, configDir) : preferredDataDir(configDir);
}

function persistTokenIfAbsent(
  tokenPath: string,
  token: string,
  source: Exclude<DesktopApiTokenSource, "file">
): Pick<DesktopApiTokenResolution, "token" | "source"> {
  try {
    mkdirSync(dirname(tokenPath), { recursive: true });
    writeTokenFile(tokenPath, token);
    return { token, source };
  } catch (error) {
    if (isFileAlreadyExistsError(error)) {
      const existing = readTokenFile(tokenPath);
      if (existing) {
        return { token: existing, source: "file" };
      }
      try {
        writeTokenFile(tokenPath, token);
        return { token, source };
      } catch (persistError) {
        throw new DesktopApiTokenPersistError(
          `Desktop API token file exists but could not be read or rewritten at ${tokenPath}: ${
            persistError instanceof Error ? persistError.message : String(persistError)
          }`
        );
      }
    }
    throw new DesktopApiTokenPersistError(
      `Desktop API token could not be persisted to ${tokenPath}: ${
        error instanceof Error ? error.message : String(error)
      }`
    );
  }
}

function writeTokenFile(tokenPath: string, token: string): void {
  const stored = protectLocalSecret(token);
  writeSecretFileAtomic(tokenPath, stored);
}

function writeSecretFileAtomic(tokenPath: string, stored: string): void {
  const tmpPath = `${tokenPath}.tmp`;
  try {
    unlinkSync(tmpPath);
  } catch {
    // Ignore stale temp files from a crashed writer.
  }
  const fd = openSync(tmpPath, "wx", 0o600);
  try {
    writeSync(fd, `${stored}\n`, undefined, "utf-8");
  } finally {
    closeSync(fd);
  }
  renameSync(tmpPath, tokenPath);
  try {
    chmodSync(tokenPath, 0o600);
  } catch {
    // Best-effort parity with the backend; Windows ACLs may not honor chmod.
  }
}

function readTokenFile(tokenPath: string): string | null {
  if (!existsSync(tokenPath)) {
    return null;
  }
  let stored = "";
  try {
    stored = readFileSync(tokenPath, "utf-8").trim();
  } catch (error) {
    throw new DesktopApiTokenPersistError(
      `Failed to read desktop API token from ${tokenPath}: ${error instanceof Error ? error.message : String(error)}`
    );
  }
  if (!stored) {
    return null;
  }
  try {
    return unprotectLocalSecret(stored);
  } catch (error) {
    throw new DesktopApiTokenPersistError(
      `Failed to decrypt desktop API token at ${tokenPath}: ${error instanceof Error ? error.message : String(error)}`
    );
  }
}

function readDotenv(dotenvPath: string): Record<string, string> {
  if (!existsSync(dotenvPath)) {
    return {};
  }

  const values: Record<string, string> = {};
  try {
    for (const rawLine of readFileSync(dotenvPath, "utf-8").split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || !line.includes("=")) {
        continue;
      }
      const [key, ...valueParts] = line.split("=");
      values[key.trim()] = unquote(valueParts.join("=").trim());
    }
  } catch {
    return {};
  }
  return values;
}

function readConfigYamlDataDir(configPath: string): string | undefined {
  if (!existsSync(configPath)) {
    return undefined;
  }

  try {
    const lines = readFileSync(configPath, "utf-8").split(/\r?\n/);
    let inPathsSection = false;
    let pathsIndent = -1;

    for (const rawLine of lines) {
      const withoutComment = stripInlineComment(rawLine);
      if (!withoutComment.trim()) {
        continue;
      }

      const indent = withoutComment.search(/\S/);
      const sectionMatch = withoutComment.match(/^(\s*)([A-Za-z0-9_-]+)\s*:\s*$/);
      if (sectionMatch) {
        inPathsSection = sectionMatch[2] === "paths";
        pathsIndent = inPathsSection ? sectionMatch[1].length : -1;
        continue;
      }

      if (inPathsSection && indent <= pathsIndent) {
        inPathsSection = false;
      }

      const dataDirMatch = withoutComment.match(/^\s*data_dir\s*:\s*(.+?)\s*$/);
      if (dataDirMatch && (inPathsSection || indent === 0)) {
        const value = unquote(dataDirMatch[1].trim());
        if (value) {
          return value;
        }
      }
    }
  } catch {
    return undefined;
  }

  return undefined;
}

function stripInlineComment(value: string): string {
  let quote: string | null = null;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if ((char === "'" || char === "\"") && value[index - 1] !== "\\") {
      quote = quote === char ? null : quote ?? char;
    }
    if (char === "#" && !quote) {
      return value.slice(0, index);
    }
  }
  return value;
}

function unquote(value: string): string {
  const trimmed = value.trim();
  if (
    (trimmed.startsWith("\"") && trimmed.endsWith("\""))
    || (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}

function configSearchRoots(command?: string): string[] {
  return uniqueExistingDirectories([
    safeResolve(getCwd()),
    safeCall(() => app.getAppPath()),
    safeCall(() => process.resourcesPath),
    command ? dirname(command) : undefined
  ]);
}

function uniqueExistingDirectories(values: Array<string | undefined>): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    if (!value) {
      continue;
    }
    const resolved = safeResolve(value);
    const key = resolved.toLowerCase();
    if (!seen.has(key) && existsSync(resolved)) {
      seen.add(key);
      result.push(resolved);
    }
  }
  return result;
}

function findAncestorWithAny(start: string, relativeMarkers: string[]): string | null {
  let current = safeResolve(start);
  for (let depth = 0; depth <= CONFIG_PARENT_SEARCH_DEPTH; depth += 1) {
    if (relativeMarkers.some((marker) => existsSync(join(current, marker)))) {
      return current;
    }
    const parent = dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return null;
}

function firstToken(...values: Array<string | undefined>): string | undefined {
  for (const value of values) {
    const token = String(value ?? "").trim();
    if (token) {
      return token;
    }
  }
  return undefined;
}

function firstEnv(env: NodeJS.ProcessEnv | Record<string, string>, name: string): string | undefined {
  return firstToken(...envAliases(name).map((alias) => env[alias]));
}

function envAliases(name: string): string[] {
  return [name];
}

function preferredDataDir(configDir: string): string {
  return join(configDir, DEFAULT_DATA_DIR_NAME);
}

function resolvePath(value: string, baseDir: string): string {
  return isAbsolute(value) ? safeResolve(value) : resolve(baseDir, value);
}

function safeResolve(value: string): string {
  try {
    return resolve(value);
  } catch {
    return value;
  }
}

function safeCall(callback: () => string | undefined): string | undefined {
  try {
    return callback();
  } catch {
    return undefined;
  }
}

function isFileAlreadyExistsError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as NodeJS.ErrnoException).code === "EEXIST";
}
