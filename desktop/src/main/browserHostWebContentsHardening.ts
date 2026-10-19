import type { WebContents } from "electron";
import type { BrowserHostRequest } from "./browserSubmissionGuard";

import {
  isBlockedBrowserHostNavigation,
  isBrowserHostRequestAllowed
} from "./browserHostNetworkGuard";

export interface BrowserHostDownloadAttempt {
  url: string;
}

export interface BrowserHostWebContentsHardeningOptions {
  onDownloadBlocked?: (attempt: BrowserHostDownloadAttempt) => void;
  onBeforeRequest?: (request: BrowserHostRequest, networkAllowed: boolean) => boolean;
}

interface BrowserHostDownloadEvent {
  preventDefault(): void;
}

interface BrowserHostDownloadItem {
  cancel(): void;
  getURL(): string;
}

const REDACTED_URL_VALUE = "[redacted]";
// Electron keeps only the last listener per Session. Route shared-profile tabs
// through one listener so opening another tab cannot replace its neighbour's guard.
const requestRoutes = new WeakMap<WebContents["session"], Map<number, BrowserHostWebContentsHardeningOptions>>();
const routeCleanup = new WeakMap<WebContents["session"], (id: number) => void>();

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
  let routes = requestRoutes.get(webContents.session);
  if (!routes) {
    routes = new Map();
    requestRoutes.set(webContents.session, routes);
    const session = webContents.session;
    const sessionRoutes = routes;
    const onDownload = (event: BrowserHostDownloadEvent, item: BrowserHostDownloadItem, source?: WebContents) => {
      const route = source ? sessionRoutes.get(source.id) : sessionRoutes.size === 1 ? sessionRoutes.values().next().value : undefined;
      blockBrowserHostDownload(event, item, route?.onDownloadBlocked);
    };
    session.on("will-download", onDownload);
    // The last tab releases callbacks, so its closed matter cannot be retained
    // by the shared Session or receive another tab's download notifications.
    const removeRoute = (id: number) => {
      sessionRoutes.delete(id);
      if (!sessionRoutes.size) {
        session.removeListener("will-download", onDownload);
        requestRoutes.delete(session);
        routeCleanup.delete(session);
      }
    };
    routeCleanup.set(webContents.session, removeRoute);
    webContents.session.webRequest.onBeforeRequest((details, callback) => {
      const route = details.webContentsId === undefined ? undefined : sessionRoutes.get(details.webContentsId);
      void handleBrowserHostBeforeRequest(details, callback, route?.onBeforeRequest);
    });
  }
  routes.set(webContents.id, options);
  webContents.on("destroyed", () => routeCleanup.get(webContents.session)?.(webContents.id));
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
  details: BrowserHostRequest,
  callback: (response: { cancel?: boolean }) => void,
  filter?: BrowserHostWebContentsHardeningOptions["onBeforeRequest"]
): Promise<void> {
  try {
    const networkAllowed = await isBrowserHostRequestAllowed(details.url);
    const approved = filter ? filter(details, networkAllowed) : true;
    callback({ cancel: !networkAllowed || !approved });
  } catch {
    callback({ cancel: true });
  }
}
