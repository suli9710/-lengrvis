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
import type { NativeConfirmationDialogOptions } from "./ipcNativeConfirmation";

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
    await confirmNativeDesktopAction(event, {
      title: "Confirm browser session",
      message: "Open a managed browser session?",
      detail: "The session may navigate to external websites using the app's configured browser-network permissions."
    });
    return sanitizeActionResult(await host.open(request as BrowserHostOpenRequest));
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
