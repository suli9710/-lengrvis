import { BrowserWindow, dialog, ipcMain, shell, type OpenDialogOptions } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type { ApiRequest, ApiResponse } from "../shared/types";
import type { BackendProcessManager } from "./backendProcess";

const DEFAULT_TIMEOUT_MS = 30_000;

export function registerIpcHandlers(backend: BackendProcessManager): void {
  ipcMain.handle(IPC_CHANNELS.backendStatus, () => backend.getStatus());
  ipcMain.handle(IPC_CHANNELS.backendStart, () => backend.start());
  ipcMain.handle(IPC_CHANNELS.backendStop, () => backend.stop());

  ipcMain.handle(IPC_CHANNELS.openExternal, async (_event, url: string) => {
    await shell.openExternal(url);
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillDirectory, async (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill package directory",
      properties: ["openDirectory"]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.chooseSkillZip, async (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    const options: OpenDialogOptions = {
      title: "Select skill zip package",
      properties: ["openFile"],
      filters: [{ name: "Skill packages", extensions: ["zip"] }]
    };
    const result = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options);
    return result.canceled ? null : result.filePaths[0] ?? null;
  });

  ipcMain.handle(IPC_CHANNELS.apiRequest, async (_event, request: ApiRequest) => {
    return proxyApiRequest(backend.getBaseUrl(), request);
  });

}

async function proxyApiRequest<TData>(
  baseUrl: string,
  request: ApiRequest
): Promise<ApiResponse<TData>> {
  const receivedAt = new Date().toISOString();

  try {
    const url = buildRequestUrl(baseUrl, request);
    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      request.timeoutMs ?? DEFAULT_TIMEOUT_MS
    );

    const response = await fetch(url, {
      method: request.method ?? "GET",
      headers: {
        Accept: "application/json",
        ...(request.body ? { "Content-Type": "application/json" } : {})
      },
      body: request.body ? JSON.stringify(request.body) : undefined,
      signal: controller.signal
    });

    clearTimeout(timeout);

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
  }
}

function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  if (/^https?:\/\//i.test(request.endpoint)) {
    throw new Error("Renderer API requests must use backend-relative endpoints");
  }

  const normalizedEndpoint = request.endpoint.startsWith("/")
    ? request.endpoint
    : `/${request.endpoint}`;
  const url = new URL(normalizedEndpoint, baseUrl);

  for (const [key, value] of Object.entries(request.query ?? {})) {
    if (value !== null && value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }

  return url;
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
      return message;
    }
  }

  return fallback || "Backend request failed";
}
