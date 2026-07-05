import type { WebContents } from "electron";
import { describe, expect, it, vi } from "vitest";

import {
  handleBrowserHostBeforeRequest,
  hardenEmbeddedWebContents
} from "./browserHostWebContentsHardening";

describe("browserHostWebContentsHardening", () => {
  it("hardens embedded contents with denied popups, navigation, permissions, and audio", () => {
    const captured: {
      willNavigate?: (event: { preventDefault: () => void }, url: string) => void;
      permissionRequest?: (_contents: unknown, _permission: string, callback: (allowed: boolean) => void) => void;
      permissionCheck?: () => boolean;
    } = {};
    const onBeforeRequest = vi.fn();
    const setWindowOpenHandler = vi.fn();
    const setAudioMuted = vi.fn();
    const webContents = {
      setWindowOpenHandler,
      on: vi.fn((eventName: string, handler: (event: { preventDefault: () => void }, url: string) => void) => {
        if (eventName === "will-navigate") {
          captured.willNavigate = handler;
        }
      }),
      session: {
        webRequest: { onBeforeRequest },
        setPermissionRequestHandler: vi.fn(
          (handler: (_contents: unknown, _permission: string, callback: (allowed: boolean) => void) => void) => {
            captured.permissionRequest = handler;
          }
        ),
        setPermissionCheckHandler: vi.fn((handler: () => boolean) => {
          captured.permissionCheck = handler;
        }),
      },
      setAudioMuted
    } as unknown as WebContents;

    hardenEmbeddedWebContents(webContents);

    expect(setWindowOpenHandler.mock.calls[0][0]()).toEqual({ action: "deny" });
    expect(onBeforeRequest).toHaveBeenCalledTimes(1);
    expect(setAudioMuted).toHaveBeenCalledWith(true);

    const preventDefault = vi.fn();
    captured.willNavigate?.({ preventDefault }, "file:///C:/Users/Suli/secret.txt");
    expect(preventDefault).toHaveBeenCalledTimes(1);

    let permissionAllowed = true;
    captured.permissionRequest?.({}, "camera", (allowed: boolean) => {
      permissionAllowed = allowed;
    });
    expect(permissionAllowed).toBe(false);
    expect(captured.permissionCheck?.()).toBe(false);
  });

  it("fails request interception closed for local file URLs", async () => {
    const callback = vi.fn();

    await handleBrowserHostBeforeRequest({ url: "file:///C:/Users/Suli/secret.txt" }, callback);

    expect(callback).toHaveBeenCalledWith({ cancel: true });
  });

  it("allows about blank probes without DNS lookup", async () => {
    const callback = vi.fn();

    await handleBrowserHostBeforeRequest({ url: "about:blank" }, callback);

    expect(callback).toHaveBeenCalledWith({ cancel: false });
  });
});
