import type { WebContents } from "electron";

import {
  isBlockedBrowserHostNavigation,
  isBrowserHostRequestAllowed
} from "./browserHostNetworkGuard";

export function hardenEmbeddedWebContents(webContents: WebContents): void {
  webContents.setWindowOpenHandler(() => {
    return { action: "deny" };
  });
  webContents.on("will-navigate", (event, url) => {
    if (isBlockedBrowserHostNavigation(url)) {
      event.preventDefault();
    }
  });
  webContents.session.webRequest.onBeforeRequest((details, callback) => {
    void handleBrowserHostBeforeRequest(details, callback);
  });
  webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  webContents.session.setPermissionCheckHandler(() => false);
  webContents.setAudioMuted(true);
}

export async function handleBrowserHostBeforeRequest(
  details: { url: string },
  callback: (response: { cancel?: boolean }) => void
): Promise<void> {
  try {
    callback({ cancel: !(await isBrowserHostRequestAllowed(details.url)) });
  } catch {
    callback({ cancel: true });
  }
}
