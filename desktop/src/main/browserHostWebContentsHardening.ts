import type { WebContents } from "electron";

import {
  isBlockedBrowserHostNavigation,
  isBrowserHostRequestAllowed
} from "./browserHostNetworkGuard";

export interface BrowserHostDownloadAttempt {
  url: string;
}

export interface BrowserHostWebContentsHardeningOptions {
  onDownloadBlocked?: (attempt: BrowserHostDownloadAttempt) => void;
}

interface BrowserHostDownloadEvent {
  preventDefault(): void;
}

interface BrowserHostDownloadItem {
  cancel(): void;
  getURL(): string;
}

const REDACTED_URL_VALUE = "[redacted]";

export function hardenEmbeddedWebContents(
  webContents: WebContents,
  options: BrowserHostWebContentsHardeningOptions = {}
): void {
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
  webContents.session.on("will-download", (event, item) => {
    blockBrowserHostDownload(event, item, options.onDownloadBlocked);
  });
  webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  webContents.session.setPermissionCheckHandler(() => false);
  webContents.setAudioMuted(true);
}

export function blockBrowserHostDownload(
  event: BrowserHostDownloadEvent,
  item: BrowserHostDownloadItem,
  onDownloadBlocked?: (attempt: BrowserHostDownloadAttempt) => void
): void {
  event.preventDefault();
  try {
    item.cancel();
  } catch {
    // preventDefault already closed the download path.
  }

  if (!onDownloadBlocked) return;
  let url = "";
  try {
    url = redactBrowserHostEventUrl(item.getURL());
  } catch {
    // The item may already be invalid after cancellation.
  }
  try {
    onDownloadBlocked({ url });
  } catch {
    // Observability must never reopen or destabilize the blocked download.
  }
}

function redactBrowserHostEventUrl(value: string): string {
  if (!value) return "";
  try {
    const parsed = new URL(value);
    if (parsed.username) parsed.username = REDACTED_URL_VALUE;
    if (parsed.password) parsed.password = REDACTED_URL_VALUE;
    for (const key of [...parsed.searchParams.keys()]) {
      parsed.searchParams.set(key, REDACTED_URL_VALUE);
    }
    if (parsed.hash) parsed.hash = `#${REDACTED_URL_VALUE}`;
    return parsed.toString();
  } catch {
    const metadataStart = value.search(/[?#]/);
    return metadataStart >= 0
      ? `${value.slice(0, metadataStart)}?${REDACTED_URL_VALUE}`
      : value;
  }
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
