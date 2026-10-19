import type { WebContents } from "electron";
import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";

import {
  blockBrowserHostDownload,
  handleBrowserHostBeforeRequest,
  hardenEmbeddedWebContents
} from "./browserHostWebContentsHardening";

describe("browserHostWebContentsHardening", () => {
  it("removes the shared download listener after the last tab closes", () => {
    const session = Object.assign(new EventEmitter(), {
      webRequest: { onBeforeRequest: vi.fn() },
      setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn()
    });
    const first = Object.assign(new EventEmitter(), { id: 1, session, setWindowOpenHandler: vi.fn(), setAudioMuted: vi.fn() });
    const second = Object.assign(new EventEmitter(), { id: 2, session, setWindowOpenHandler: vi.fn(), setAudioMuted: vi.fn() });
    hardenEmbeddedWebContents(first as unknown as WebContents);
    hardenEmbeddedWebContents(second as unknown as WebContents);
    expect(session.listenerCount("will-download")).toBe(1);
    first.emit("destroyed");
    expect(session.listenerCount("will-download")).toBe(1);
    second.emit("destroyed");
    expect(session.listenerCount("will-download")).toBe(0);
  });
  it("hardens embedded contents with denied popups, navigation, permissions, and audio", () => {
    const captured: {
      willNavigate?: (event: { preventDefault: () => void }, url: string) => void;
      willDownload?: (
        event: { preventDefault: () => void },
        item: { cancel: () => void; getURL: () => string }
      ) => void;
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
        on: vi.fn((eventName: string, handler: typeof captured.willDownload) => {
          if (eventName === "will-download") {
            captured.willDownload = handler;
          }
        }),
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
    const onDownloadBlocked = vi.fn();

    hardenEmbeddedWebContents(webContents, { onDownloadBlocked });

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

    const preventDownload = vi.fn();
    const cancelDownload = vi.fn();
    captured.willDownload?.(
      { preventDefault: preventDownload },
      { cancel: cancelDownload, getURL: () => "https://example.test/report.csv" }
    );
    expect(preventDownload).toHaveBeenCalledTimes(1);
    expect(cancelDownload).toHaveBeenCalledTimes(1);
    expect(onDownloadBlocked).toHaveBeenCalledWith({ url: "https://example.test/report.csv" });
  });

  it("keeps downloads blocked when cancellation or observability throws", () => {
    const preventDefault = vi.fn();

    expect(() => blockBrowserHostDownload(
      { preventDefault },
      {
        cancel: () => {
          throw new Error("item already disposed");
        },
        getURL: () => "https://example.test/file.zip"
      },
      () => {
        throw new Error("observer unavailable");
      }
    )).not.toThrow();
    expect(preventDefault).toHaveBeenCalledTimes(1);
  });

  it("redacts every query value and fragment before reporting a blocked download", () => {
    const onDownloadBlocked = vi.fn();
    blockBrowserHostDownload(
      { preventDefault: vi.fn() },
      {
        cancel: vi.fn(),
        getURL: () =>
          "https://user:password@example.test/report.csv?download=1&sig=secret&refresh_token=refresh&id_token=id&code=oauth&X-Goog-Credential=credential&X-Goog-Signature=signature#session=fragment"
      },
      onDownloadBlocked
    );
    const url = onDownloadBlocked.mock.calls[0][0].url as string;

    expect(url).toContain("download=%5Bredacted%5D");
    expect(url).not.toContain("user:password");
    expect(url).not.toContain("download=1");
    expect(url).not.toContain("=secret");
    expect(url).not.toContain("=refresh");
    expect(url).not.toContain("=oauth");
    expect(url).not.toContain("=credential");
    expect(url).not.toContain("=signature");
    expect(url).not.toContain("=fragment");
    expect(url.match(/%5Bredacted%5D|\[redacted\]/g)?.length).toBeGreaterThanOrEqual(10);
  });

  it("does not fall back to an unsanitized URL when the cancelled item is unavailable", () => {
    const onDownloadBlocked = vi.fn();

    blockBrowserHostDownload(
      { preventDefault: vi.fn() },
      {
        cancel: vi.fn(),
        getURL: () => {
          throw new Error("item already disposed");
        }
      },
      onDownloadBlocked
    );

    expect(onDownloadBlocked).toHaveBeenCalledWith({ url: "" });
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

  it("routes shared profile requests to the owning tab without replacing earlier guards", async () => {
    const onBeforeRequest = vi.fn();
    const session = {
      webRequest: { onBeforeRequest }, on: vi.fn(),
      setPermissionRequestHandler: vi.fn(), setPermissionCheckHandler: vi.fn()
    };
    const destroyedHandlers = new Map<number, () => void>();
    const tab = (id: number) => ({ id, session,
      on: vi.fn((event: string, handler: () => void) => { if (event === "destroyed") destroyedHandlers.set(id, handler); }),
      setWindowOpenHandler: vi.fn(), setAudioMuted: vi.fn()
    }) as unknown as WebContents;
    const first = tab(1), second = tab(2);
    const deny = vi.fn(() => false), allow = vi.fn(() => true);
    hardenEmbeddedWebContents(first, { onBeforeRequest: deny });
    hardenEmbeddedWebContents(second, { onBeforeRequest: allow });
    expect(onBeforeRequest).toHaveBeenCalledTimes(1);
    const listener = onBeforeRequest.mock.calls[0][0];
    const intercepted = (id: number) => new Promise(resolve => listener({ url: "about:blank", webContentsId: id }, resolve));
    expect(await intercepted(1)).toEqual({ cancel: true });
    expect(await intercepted(2)).toEqual({ cancel: false });
    expect(deny).toHaveBeenCalledTimes(1);
    expect(allow).toHaveBeenCalledTimes(1);
    destroyedHandlers.get(1)!();
    expect(await intercepted(1)).toEqual({ cancel: false });
    expect(deny).toHaveBeenCalledTimes(1);
  });

  it("does not let an approval filter override the network boundary", async () => {
    const callback = vi.fn();
    const filter = vi.fn(() => true);
    await handleBrowserHostBeforeRequest({ url: "file:///C:/private.txt" }, callback, filter);
    expect(filter).toHaveBeenCalledWith({ url: "file:///C:/private.txt" }, false);
    expect(callback).toHaveBeenCalledWith({ cancel: true });
  });
});
