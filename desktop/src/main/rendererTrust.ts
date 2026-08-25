import { app, BrowserWindow, type IpcMainInvokeEvent } from "electron";

import { isPackagedRendererEntryUrl } from "./rendererProtocol";

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
      return false;
    }
    if (parsed.protocol === "app:") {
      return isPackagedRendererEntryUrl(url);
    }
    const trustedOrigins = new Set<string>();
    if (!app.isPackaged) {
      trustedOrigins.add("http://127.0.0.1:5173");
      trustedOrigins.add("http://localhost:5173");
    }
    const devServerUrl = app.isPackaged ? "" : process.env.VITE_DEV_SERVER_URL;
    if (!app.isPackaged && devServerUrl) {
      trustedOrigins.add(new URL(devServerUrl).origin);
    }
    return trustedOrigins.has(parsed.origin);
  } catch {
    return false;
  }
}
