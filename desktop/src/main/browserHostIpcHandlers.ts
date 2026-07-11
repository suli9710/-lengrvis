import type { IpcMainInvokeEvent, WebContents } from "electron";

import { IPC_CHANNELS } from "../shared/ipc";
import type {
  BrowserAction,
  BrowserHostActionRequest,
  BrowserHostActionResult,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot
} from "../shared/browserTypes";
import {
  sanitizeActionResultForRenderer,
  sanitizeSnapshotForRenderer
} from "../shared/browserHostRedaction";
import { isReadOnlyBrowserHostAction } from "./browserHostBridge";
import { normalizeBrowserHostUrl } from "./browserHostValidation";
import type { NativeConfirmationDialogOptions } from "./ipcNativeConfirmation";

const MAX_BROWSER_CONFIRMATION_ORIGIN_CHARS = 256;

export type BrowserHostIpcListener = (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown;

export type BrowserHostIpcRegistrar = {
  handle: (channel: string, listener: BrowserHostIpcListener) => void;
};

export type BrowserHostIpcTarget = {
  getSnapshot: () => BrowserHostSnapshot;
  open: (request?: BrowserHostOpenRequest) => Promise<BrowserHostActionResult>;
  show: (sessionId: string) => BrowserHostActionResult;
  hide: () => BrowserHostActionResult;
  setBounds: (bounds: BrowserHostBounds) => BrowserHostActionResult;
  pause: (sessionId: string) => BrowserHostActionResult;
  resume: (sessionId: string) => BrowserHostActionResult;
  takeover: (sessionId: string) => BrowserHostActionResult;
  release: (sessionId: string) => BrowserHostActionResult;
  stop: (sessionId: string) => Promise<BrowserHostActionResult>;
  performAction: (sessionId: string, action: BrowserAction) => Promise<BrowserHostActionResult>;
};

export type BrowserHostRendererAssertion = (event: IpcMainInvokeEvent) => void;
export type BrowserHostActionConfirmation = (
  event: IpcMainInvokeEvent,
  options: NativeConfirmationDialogOptions
) => Promise<void>;
export type BrowserHostReadOnlyActionPredicate = (action: BrowserAction | undefined) => action is BrowserAction;
export type BrowserHostActionResultSanitizer = (result: BrowserHostActionResult) => BrowserHostActionResult;
export type BrowserHostSnapshotSanitizer = (snapshot: BrowserHostSnapshot) => BrowserHostSnapshot;

export interface BrowserHostRendererTrustDependencies {
  hasAttachedWindow: (sender: WebContents) => boolean;
  isTrustedRendererUrl: (url: string) => boolean;
}

export interface BrowserHostIpcHandlerDependencies {
  assertTrustedRenderer?: BrowserHostRendererAssertion;
  confirmNativeDesktopAction?: BrowserHostActionConfirmation;
  isReadOnlyAction?: BrowserHostReadOnlyActionPredicate;
  sanitizeActionResult?: BrowserHostActionResultSanitizer;
  sanitizeSnapshot?: BrowserHostSnapshotSanitizer;
}

export function registerBrowserHostIpcHandlers({
  handle,
  host,
  assertTrustedRenderer = defaultAssertBrowserHostRenderer,
  confirmNativeDesktopAction = defaultConfirmNativeDesktopAction,
  isReadOnlyAction = isReadOnlyBrowserHostAction,
  sanitizeActionResult = sanitizeActionResultForRenderer,
  sanitizeSnapshot = sanitizeSnapshotForRenderer
}: BrowserHostIpcRegistrar & { host: BrowserHostIpcTarget } & BrowserHostIpcHandlerDependencies): void {
  handle(IPC_CHANNELS.browserHostSnapshot, (event) => {
    assertTrustedRenderer(event);
    return sanitizeSnapshot(host.getSnapshot());
  });

  handle(IPC_CHANNELS.browserHostOpen, async (event, request) => {
    assertTrustedRenderer(event);
    const openRequest = request as BrowserHostOpenRequest;
    await confirmNativeDesktopAction(event, browserHostOpenConfirmationOptions(openRequest));
    return sanitizeActionResult(await host.open(openRequest));
  });

  handle(IPC_CHANNELS.browserHostShow, (event, sessionId) => {
    assertTrustedRenderer(event);
    return sanitizeActionResult(host.show(String(sessionId)));
  });

  handle(IPC_CHANNELS.browserHostHide, (event) => {
    assertTrustedRenderer(event);
    return sanitizeActionResult(host.hide());
  });

  handle(IPC_CHANNELS.browserHostSetBounds, (event, bounds) => {
    assertTrustedRenderer(event);
    return sanitizeActionResult(host.setBounds(bounds as BrowserHostBounds));
  });

  handle(IPC_CHANNELS.browserHostPause, (event, sessionId) => {
    assertTrustedRenderer(event);
    return sanitizeActionResult(host.pause(String(sessionId)));
  });

  handle(IPC_CHANNELS.browserHostResume, (event, sessionId) => {
    assertTrustedRenderer(event);
    return sanitizeActionResult(host.resume(String(sessionId)));
  });

  handle(IPC_CHANNELS.browserHostTakeover, (event, sessionId) => {
    assertTrustedRenderer(event);
    void sessionId;
    return deniedRendererBrowserHostWrite(
      host,
      "BrowserHost takeover requires an approval grant.",
      sanitizeActionResult
    );
  });

  handle(IPC_CHANNELS.browserHostRelease, (event, sessionId) => {
    assertTrustedRenderer(event);
    return sanitizeActionResult(host.release(String(sessionId)));
  });

  handle(IPC_CHANNELS.browserHostStop, (event, sessionId) => {
    assertTrustedRenderer(event);
    return Promise.resolve(host.stop(String(sessionId))).then(sanitizeActionResult);
  });

  handle(IPC_CHANNELS.browserHostAction, async (event, request) => {
    assertTrustedRenderer(event);
    const actionRequest = request as Partial<BrowserHostActionRequest> | undefined;
    const action = actionRequest?.action;
    if (!isRendererBrowserHostActionAllowed(action, isReadOnlyAction)) {
      return deniedRendererBrowserHostWrite(
        host,
        "BrowserHost input actions require an approval grant.",
        sanitizeActionResult
      );
    }
    return sanitizeActionResult(await host.performAction(String(actionRequest?.sessionId ?? ""), action));
  });
}

function browserHostOpenConfirmationOptions(request: BrowserHostOpenRequest): NativeConfirmationDialogOptions {
  const target = browserHostConfirmationTarget(request?.url);
  return {
    title: "Confirm browser session",
    message: "Open a managed browser session?",
    detail: [
      `Target: ${target}`,
      "The session may navigate to external websites using the app's configured browser-network permissions."
    ].join("\n")
  };
}

function browserHostConfirmationTarget(rawUrl: unknown): string {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) {
    return "about:blank";
  }
  try {
    const normalized = normalizeBrowserHostUrl(rawUrl);
    if (!normalized || normalized === "about:blank") {
      return "about:blank";
    }
    return truncateBrowserHostConfirmationOrigin(new URL(normalized).origin);
  } catch {
    return "unavailable (invalid or blocked URL)";
  }
}

function truncateBrowserHostConfirmationOrigin(origin: string): string {
  return origin.length > MAX_BROWSER_CONFIRMATION_ORIGIN_CHARS
    ? `${origin.slice(0, MAX_BROWSER_CONFIRMATION_ORIGIN_CHARS)}...`
    : origin;
}

export function assertBrowserHostRenderer(
  event: IpcMainInvokeEvent,
  { hasAttachedWindow, isTrustedRendererUrl }: BrowserHostRendererTrustDependencies
): void {
  const url = event.senderFrame?.url ?? "";
  if (!hasAttachedWindow(event.sender) || !isTrustedRendererUrl(url)) {
    throw new Error("Browser host request came from an unknown renderer");
  }
}

export function isRendererBrowserHostActionAllowed(
  action: BrowserAction | undefined,
  isReadOnlyAction: BrowserHostReadOnlyActionPredicate = isReadOnlyBrowserHostAction
): action is BrowserAction {
  return isReadOnlyAction(action);
}

export function deniedRendererBrowserHostWrite(
  host: Pick<BrowserHostIpcTarget, "getSnapshot">,
  error: string,
  sanitizeActionResult: BrowserHostActionResultSanitizer = sanitizeActionResultForRenderer
): BrowserHostActionResult {
  return sanitizeActionResult({
    ok: false,
    error,
    snapshot: host.getSnapshot()
  });
}

function defaultAssertBrowserHostRenderer(event: IpcMainInvokeEvent): void {
  const { BrowserWindow } = require("electron") as typeof import("electron");
  const { isTrustedRendererUrl } = require("./ipc") as typeof import("./ipc");
  assertBrowserHostRenderer(event, {
    hasAttachedWindow: (sender) => Boolean(BrowserWindow.fromWebContents(sender)),
    isTrustedRendererUrl
  });
}

async function defaultConfirmNativeDesktopAction(
  event: IpcMainInvokeEvent,
  options: NativeConfirmationDialogOptions
): Promise<void> {
  const { confirmNativeDesktopAction } = require("./ipc") as typeof import("./ipc");
  await confirmNativeDesktopAction(event, options);
}
