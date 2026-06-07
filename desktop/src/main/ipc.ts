import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent, type OpenDialogOptions } from "electron";
import { existsSync } from "node:fs";

import {
  API_REQUEST_ALLOWED_KEYS,
  API_REQUEST_DENIED_EXACT_PATHS,
  API_REQUEST_DENIED_PATH_PREFIXES,
  API_REQUEST_SECURITY_LIMITS,
  IPC_CHANNELS
} from "../shared/ipc";
import type { ApiMethod, ApiQueryValue, ApiRequest, ApiResponse } from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { pathToFileURL } from "node:url";

const DEFAULT_TIMEOUT_MS = 30_000;
const ALLOWED_API_METHODS = new Set<ApiMethod>(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const ALLOWED_EXTERNAL_PROTOCOLS = new Set(["https:", "http:", "mailto:"]);
const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";
const API_REQUEST_ALLOWED_KEY_SET = new Set<string>(API_REQUEST_ALLOWED_KEYS);
const API_REQUEST_DENIED_EXACT_PATH_SET = new Set<string>(API_REQUEST_DENIED_EXACT_PATHS);
const API_REQUEST_RESERVED_KEYS = new Set(["__proto__", "constructor", "prototype"]);

class ApiRequestValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiRequestValidationError";
  }
}

interface ValidatedApiRequest {
  endpoint: string;
  method: ApiMethod;
  query?: Record<string, Exclude<ApiQueryValue, null | undefined>>;
  serializedBody?: string;
  timeoutMs: number;
}

export function registerIpcHandlers(backend: BackendProcessManager): void {
  ipcMain.handle(IPC_CHANNELS.backendStatus, (event) => {
    assertTrustedRenderer(event);
    return backend.getStatus();
  });
  ipcMain.handle(IPC_CHANNELS.backendStart, (event) => {
    assertTrustedRenderer(event);
    return backend.start();
  });
  ipcMain.handle(IPC_CHANNELS.backendStop, (event) => {
    assertTrustedRenderer(event);
    return backend.stop();
  });
  ipcMain.handle(IPC_CHANNELS.backendForeground, (event) => {
    assertTrustedRenderer(event);
    return backend.enterForeground();
  });
  ipcMain.handle(IPC_CHANNELS.backendBackground, (event) => {
    assertTrustedRenderer(event);
    return backend.enterBackground();
  });

  ipcMain.handle(IPC_CHANNELS.openExternal, async (event, url: string) => {
    assertTrustedRenderer(event);
    await openSafeExternalUrl(url);
  });

  ipcMain.handle(IPC_CHANNELS.getFileIcon, async (event, filePath: string) => {
    assertTrustedRenderer(event);
    return getFileIconDataUrl(filePath);
  });

  ipcMain.handle(IPC_CHANNELS.chooseDirectory, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "选择文件夹",
      properties: ["openDirectory", "createDirectory"]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.chooseDocument, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "选择文档",
      properties: ["openFile"],
      filters: [
        {
          name: "可读取文档",
          extensions: [
            "pdf",
            "docx",
            "txt",
            "md",
            "markdown",
            "log",
            "rst",
            "json",
            "yaml",
            "yml",
            "py",
            "ts",
            "tsx",
            "js",
            "csv",
            "xlsx",
            "pptx",
            "html",
            "htm",
            "png",
            "jpg",
            "jpeg",
            "webp",
            "bmp",
            "tif",
            "tiff"
          ]
        },
        { name: "所有文件", extensions: ["*"] }
      ]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.knownFolders, (event) => {
    assertTrustedRenderer(event);
    return {
      desktop: app.getPath("desktop"),
      downloads: app.getPath("downloads"),
      documents: app.getPath("documents"),
      pictures: app.getPath("pictures")
    };
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillDirectory, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill package directory",
      properties: ["openDirectory"]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillZip, async (event) => {
    assertTrustedRenderer(event);
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill zip package",
      properties: ["openFile"],
      filters: [{ name: "Skill packages", extensions: ["zip"] }]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.apiRequest, async (event, request: ApiRequest) => {
    assertTrustedRenderer(event);
    return proxyApiRequest(backend.getBaseUrl(), request, backend.getDesktopApiToken());
  });

}

async function getFileIconDataUrl(filePath: string): Promise<string | null> {
  if (typeof filePath !== "string" || !filePath.trim() || filePath.includes("\0")) {
    return null;
  }
  if (!existsSync(filePath)) {
    return null;
  }
  try {
    const icon = await app.getFileIcon(filePath, { size: "normal" });
    if (icon.isEmpty()) {
      return null;
    }
    return icon.toDataURL();
  } catch {
    return null;
  }
}

async function proxyApiRequest<TData>(
  baseUrl: string,
  request: ApiRequest,
  desktopApiToken: string
): Promise<ApiResponse<TData>> {
  const receivedAt = new Date().toISOString();
  let timeout: ReturnType<typeof setTimeout> | undefined;

  try {
    const validatedRequest = validateApiRequest(request);
    const url = buildValidatedRequestUrl(baseUrl, validatedRequest);
    const controller = new AbortController();
    timeout = setTimeout(
      () => controller.abort(),
      validatedRequest.timeoutMs
    );

    const response = await fetch(url, {
      method: validatedRequest.method,
      headers: {
        Accept: "application/json",
        [DESKTOP_API_TOKEN_HEADER]: desktopApiToken,
        ...(validatedRequest.serializedBody !== undefined ? { "Content-Type": "application/json" } : {})
      },
      body: validatedRequest.serializedBody,
      signal: controller.signal
    });

    const data = await parseResponseBody(response);

    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: {
          code: `HTTP_${response.status}`,
          message: getErrorMessage(data, response.statusText),
          details: data
        },
        receivedAt
      };
    }

    return {
      ok: true,
      status: response.status,
      data: data as TData,
      receivedAt
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Request failed";
    if (error instanceof ApiRequestValidationError) {
      return {
        ok: false,
        status: 0,
        error: {
          code: "INVALID_RENDERER_API_REQUEST",
          message
        },
        receivedAt
      };
    }

    return {
      ok: false,
      status: 0,
      error: {
        code: "NETWORK_ERROR",
        message
      },
      receivedAt
    };
  } finally {
    if (timeout) {
      clearTimeout(timeout);
    }
  }
}

export function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  return buildValidatedRequestUrl(baseUrl, validateApiRequest(request));
}

function buildValidatedRequestUrl(baseUrl: string, request: ValidatedApiRequest): URL {
  const backendUrl = new URL(baseUrl);
  if (!["http:", "https:"].includes(backendUrl.protocol)) {
    throw new ApiRequestValidationError("Backend API base URL must be HTTP(S)");
  }

  const backendOrigin = backendUrl.origin;
  const url = new URL(request.endpoint, backendUrl);
  if (url.origin !== backendOrigin) {
    throw new ApiRequestValidationError("Renderer API request escaped the configured backend origin");
  }

  for (const [key, value] of Object.entries(request.query ?? {})) {
    url.searchParams.set(key, String(value));
  }
  if (url.search.length > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new ApiRequestValidationError("Renderer API query is too large");
  }

  return url;
}

function validateApiRequest(request: unknown): ValidatedApiRequest {
  if (!isPlainRecord(request)) {
    throw new ApiRequestValidationError("Renderer API request is malformed");
  }

  rejectUnexpectedApiRequestKeys(request);
  const endpoint = validateApiEndpoint(request.endpoint);
  const method = validateApiMethod(request.method);
  const query = validateApiQuery(request.query);
  const timeoutMs = validateApiTimeout(request.timeoutMs);
  const serializedBody = serializeApiRequestBody(request, method);
  return { endpoint, method, query, serializedBody, timeoutMs };
}

function rejectUnexpectedApiRequestKeys(request: Record<string, unknown>): void {
  for (const key of Object.keys(request)) {
    if (!API_REQUEST_ALLOWED_KEY_SET.has(key)) {
      const detail = key === "headers" ? "custom headers are not allowed" : `field is not allowed: ${key}`;
      throw new ApiRequestValidationError(`Renderer API request ${detail}`);
    }
  }
}

function validateApiEndpoint(value: unknown): string {
  if (typeof value !== "string") {
    throw new ApiRequestValidationError("Renderer API endpoint is required");
  }
  if (!value || value.length > API_REQUEST_SECURITY_LIMITS.maxEndpointChars) {
    throw new ApiRequestValidationError("Renderer API endpoint length is invalid");
  }
  if (value.trim() !== value || /\s|[\u0000-\u001F\u007F]/.test(value)) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe characters");
  }
  if (value.includes("?") || value.includes("#")) {
    throw new ApiRequestValidationError("Renderer API endpoint must not include query strings or fragments");
  }
  if (
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("//") ||
    value.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(value)
  ) {
    throw new ApiRequestValidationError("Renderer API requests must use backend-relative endpoints");
  }
  if (/%2f|%5c/i.test(value)) {
    throw new ApiRequestValidationError("Renderer API endpoint must not contain encoded path separators");
  }

  let decodedPath: string;
  try {
    decodedPath = decodeURIComponent(value);
  } catch {
    throw new ApiRequestValidationError("Renderer API endpoint encoding is invalid");
  }

  if (decodedPath.includes("\\") || decodedPath.includes("//")) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe path separators");
  }
  if (decodedPath !== "/api" && !decodedPath.startsWith("/api/")) {
    throw new ApiRequestValidationError("Renderer API requests must target backend API paths");
  }

  const segments = decodedPath.split("/");
  if (segments.some((segment) => segment === "." || segment === "..")) {
    throw new ApiRequestValidationError("Renderer API endpoint contains unsafe path segments");
  }

  const normalizedPath = `/${segments.filter(Boolean).join("/")}`;
  rejectDeniedApiPath(normalizedPath);
  return value;
}

function rejectDeniedApiPath(pathname: string): void {
  if (API_REQUEST_DENIED_EXACT_PATH_SET.has(pathname)) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
  if (
    API_REQUEST_DENIED_PATH_PREFIXES.some(
      (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
    )
  ) {
    throw new ApiRequestValidationError("Renderer API endpoint requires an explicit desktop bridge");
  }
}

function validateApiMethod(value: unknown): ApiMethod {
  if (value === undefined) {
    return "GET";
  }
  if (typeof value !== "string" || value !== value.toUpperCase() || !ALLOWED_API_METHODS.has(value as ApiMethod)) {
    throw new ApiRequestValidationError("Renderer API request method is not allowed");
  }
  return value as ApiMethod;
}

function validateApiQuery(value: unknown): ValidatedApiRequest["query"] {
  if (value === undefined) {
    return undefined;
  }
  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError("Renderer API query must be an object");
  }

  const entries = Object.entries(value);
  if (entries.length > API_REQUEST_SECURITY_LIMITS.maxQueryParams) {
    throw new ApiRequestValidationError("Renderer API query has too many parameters");
  }

  let totalBytes = 0;
  const query: NonNullable<ValidatedApiRequest["query"]> = {};
  for (const [key, queryValue] of entries) {
    assertSafeFieldName(key, "Renderer API query key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    if (queryValue === null || queryValue === undefined) {
      continue;
    }
    if (!["string", "number", "boolean"].includes(typeof queryValue)) {
      throw new ApiRequestValidationError("Renderer API query values must be primitive");
    }
    if (typeof queryValue === "number" && !Number.isFinite(queryValue)) {
      throw new ApiRequestValidationError("Renderer API query number is invalid");
    }
    const stringValue = String(queryValue);
    const valueBytes = utf8ByteLength(stringValue);
    if (valueBytes > API_REQUEST_SECURITY_LIMITS.maxQueryValueChars) {
      throw new ApiRequestValidationError("Renderer API query value is too large");
    }
    totalBytes += utf8ByteLength(key) + valueBytes;
    query[key] = queryValue as Exclude<ApiQueryValue, null | undefined>;
  }

  if (totalBytes > API_REQUEST_SECURITY_LIMITS.maxQueryBytes) {
    throw new ApiRequestValidationError("Renderer API query is too large");
  }

  return Object.keys(query).length ? query : undefined;
}

function validateApiTimeout(value: unknown): number {
  if (value === undefined) {
    return DEFAULT_TIMEOUT_MS;
  }
  if (
    typeof value !== "number" ||
    !Number.isFinite(value) ||
    !Number.isInteger(value) ||
    value <= 0 ||
    value > API_REQUEST_SECURITY_LIMITS.maxTimeoutMs
  ) {
    throw new ApiRequestValidationError("Renderer API timeout is invalid");
  }
  return value;
}

function serializeApiRequestBody(request: Record<string, unknown>, method: ApiMethod): string | undefined {
  if (!Object.prototype.hasOwnProperty.call(request, "body") || request.body === undefined) {
    return undefined;
  }
  if (method === "GET") {
    throw new ApiRequestValidationError("Renderer API GET requests cannot include a body");
  }

  assertJsonSafeValue(request.body, 0, new WeakSet<object>());
  const serialized = JSON.stringify(request.body);
  if (typeof serialized !== "string") {
    throw new ApiRequestValidationError("Renderer API body must be JSON serializable");
  }
  if (utf8ByteLength(serialized) > API_REQUEST_SECURITY_LIMITS.maxBodyBytes) {
    throw new ApiRequestValidationError("Renderer API body is too large");
  }
  return serialized;
}

function assertJsonSafeValue(value: unknown, depth: number, seen: WeakSet<object>): void {
  if (depth > API_REQUEST_SECURITY_LIMITS.maxBodyDepth) {
    throw new ApiRequestValidationError("Renderer API body is too deeply nested");
  }

  if (value === null) {
    return;
  }

  if (typeof value === "string") {
    if (utf8ByteLength(value) > API_REQUEST_SECURITY_LIMITS.maxBodyStringBytes) {
      throw new ApiRequestValidationError("Renderer API body string is too large");
    }
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new ApiRequestValidationError("Renderer API body number is invalid");
    }
    return;
  }
  if (typeof value === "boolean") {
    return;
  }
  if (typeof value !== "object") {
    throw new ApiRequestValidationError("Renderer API body must be JSON serializable");
  }

  if (seen.has(value)) {
    throw new ApiRequestValidationError("Renderer API body cannot be circular");
  }
  seen.add(value);

  if (Array.isArray(value)) {
    if (value.length > API_REQUEST_SECURITY_LIMITS.maxBodyArrayItems) {
      throw new ApiRequestValidationError("Renderer API body array is too large");
    }
    for (const item of value) {
      assertJsonSafeValue(item, depth + 1, seen);
    }
    seen.delete(value);
    return;
  }

  if (!isPlainRecord(value)) {
    throw new ApiRequestValidationError("Renderer API body must contain plain JSON objects");
  }

  const keys = Object.keys(value);
  if (keys.length > API_REQUEST_SECURITY_LIMITS.maxBodyObjectKeys) {
    throw new ApiRequestValidationError("Renderer API body object has too many keys");
  }
  for (const key of keys) {
    assertSafeFieldName(key, "Renderer API body key", API_REQUEST_SECURITY_LIMITS.maxQueryKeyChars);
    assertJsonSafeValue(value[key], depth + 1, seen);
  }
  seen.delete(value);
}

function assertSafeFieldName(name: string, label: string, maxChars: number): void {
  if (!name || name.length > maxChars || /[\u0000-\u001F\u007F]/.test(name) || API_REQUEST_RESERVED_KEYS.has(name)) {
    throw new ApiRequestValidationError(`${label} is invalid`);
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function utf8ByteLength(value: string): number {
  return Buffer.byteLength(value, "utf8");
}

async function openSafeExternalUrl(rawUrl: string): Promise<void> {
  const parsed = new URL(rawUrl);
  if (!ALLOWED_EXTERNAL_PROTOCOLS.has(parsed.protocol)) {
    throw new Error("External URL protocol is not allowed");
  }
  await shell.openExternal(parsed.toString());
}

export function assertTrustedRenderer(event: IpcMainInvokeEvent): void {
  const url = event.senderFrame?.url ?? "";
  if (!BrowserWindow.fromWebContents(event.sender) || !isTrustedRendererUrl(url)) {
    throw new Error("IPC request came from an untrusted renderer");
  }
}

export function isTrustedRendererUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "file:") {
      const rendererRoot = pathToFileURL(`${__dirname}/../renderer/`).toString();
      return parsed.href.startsWith(rendererRoot);
    }
    if (parsed.protocol === "app:" && parsed.hostname === "local") {
      return true;
    }
    const trustedOrigins = new Set(["http://127.0.0.1:5173", "http://localhost:5173"]);
    const devServerUrl = process.env.VITE_DEV_SERVER_URL;
    if (devServerUrl) {
      trustedOrigins.add(new URL(devServerUrl).origin);
    }
    return trustedOrigins.has(parsed.origin);
  } catch {
    return false;
  }
}

export function isSafeExternalUrl(url: string): boolean {
  try {
    return ALLOWED_EXTERNAL_PROTOCOLS.has(new URL(url).protocol);
  } catch {
    return false;
  }
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";

  if (response.status === 204) {
    return undefined;
  }

  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return text ? { message: text } : undefined;
}

function getErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object" && "message" in data) {
    const message = (data as { message?: unknown }).message;
    if (typeof message === "string") {
      return userFacingBackendError(message);
    }
  }
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return userFacingBackendError(detail);
    }
  }

  return userFacingBackendError(fallback || "Backend request failed");
}

function userFacingBackendError(message: string): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("missing desktop api token") || normalized.includes("unauthorized")) {
    return "Lengrvis 正在保护本机接口。请重启桌面应用后再试；未授权页面不能直接读取本机数据。";
  }
  return message;
}
