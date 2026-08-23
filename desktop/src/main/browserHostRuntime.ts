import {
  BrowserView,
  WebContentsView,
  type WebContents
} from "electron";

export type BrowserContainer =
  | {
      kind: "webContentsView";
      view: WebContentsView;
    }
  | {
      kind: "browserView";
      view: BrowserView;
    };

export const BROWSER_ACTION_MAX_DELAY_MS = 30_000;

export function safeCredentialErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  const safePrefixes = [
    "Browser page domain changed",
    "Browser page or credential fields changed",
    "Browser session is no longer available",
    "Credential purpose is not allowed",
    "Credential task binding does not match",
    "Credential use requires",
    "Credential use ticket",
    "Exactly one filled password field",
    "Invalid credential ref id",
    "Invalid run id",
    "Invalid session id",
    "Invalid task id",
    "MFA, passcodes, and verification fields",
    "Saved credential",
    "Saved credentials",
    "Secure OS credential storage",
    "The page did not provide"
  ];
  return safePrefixes.some((prefix) => message.startsWith(prefix))
    ? message
    : "Credential operation failed";
}

export function createBrowserContainer(partition: string): BrowserContainer {
  const webPreferences = {
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    partition
  };

  if (typeof WebContentsView === "function") {
    return {
      kind: "webContentsView",
      view: new WebContentsView({ webPreferences })
    };
  }

  return {
    kind: "browserView",
    view: new BrowserView({ webPreferences })
  };
}

export function destroyWebContents(webContents: WebContents): void {
  if (!webContents.isDestroyed()) {
    webContents.close({ waitForBeforeUnload: false });
  }
}

export function runDomAction(webContents: WebContents, script: string): Promise<unknown> {
  return webContents.executeJavaScript(script, true);
}

export function delay(ms: number): Promise<void> {
  if (!Number.isSafeInteger(ms) || ms < 0 || ms > BROWSER_ACTION_MAX_DELAY_MS) {
    return Promise.reject(
      new RangeError(`Browser action delay must be an integer from 0 to ${BROWSER_ACTION_MAX_DELAY_MS} ms`)
    );
  }
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}
