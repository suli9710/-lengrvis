import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent, type OpenDialogOptions } from "electron";
import { existsSync } from "node:fs";

import { IPC_CHANNELS } from "../shared/ipc";
import type { ApiRequest, ApiResponse } from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";
import { pathToFileURL } from "node:url";

const DEFAULT_TIMEOUT_MS = 30_000;
const ALLOWED_API_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const ALLOWED_EXTERNAL_PROTOCOLS = new Set(["https:", "http:", "mailto:"]);
const DESKTOP_API_TOKEN_HEADER = "X-Lengrvis-Desktop-Token";

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
    const url = buildRequestUrl(baseUrl, request);
    const controller = new AbortController();
    timeout = setTimeout(
      () => controller.abort(),
      request.timeoutMs ?? DEFAULT_TIMEOUT_MS
    );

    const response = await fetch(url, {
      method: request.method ?? "GET",
      headers: {
        Accept: "application/json",
        [DESKTOP_API_TOKEN_HEADER]: desktopApiToken,
        ...(request.body ? { "Content-Type": "application/json" } : {})
      },
      body: request.body ? JSON.stringify(request.body) : undefined,
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
  if (!request || typeof request !== "object" || typeof request.endpoint !== "string") {
    throw new Error("Renderer API request is malformed");
  }
  if (!ALLOWED_API_METHODS.has(request.method ?? "GET")) {
    throw new Error("Renderer API request method is not allowed");
  }
  if (
    !request.endpoint.startsWith("/") ||
    request.endpoint.startsWith("//") ||
    request.endpoint.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(request.endpoint)
  ) {
    throw new Error("Renderer API requests must use backend-relative endpoints");
  }

  const backendOrigin = new URL(baseUrl).origin;
  const url = new URL(request.endpoint, baseUrl);
  if (url.origin !== backendOrigin) {
    throw new Error("Renderer API request escaped the configured backend origin");
  }

  for (const [key, value] of Object.entries(request.query ?? {})) {
    if (value !== null && value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }

  return url;
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
