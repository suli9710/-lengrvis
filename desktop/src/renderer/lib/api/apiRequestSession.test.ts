import { afterEach, describe, expect, it, vi } from "vitest";

import { RendererApiRequestSession, safeIpcApiRequest } from "./apiRequestSession";
import { rendererBatchControllers } from "./transport";

function installElectronApi(request = vi.fn(), abortInflight = vi.fn()) {
  (window as unknown as { lengrvis?: unknown }).lengrvis = {
    api: {
      request,
      abortInflight
    }
  };
  return { request, abortInflight };
}

function installWindowTimers(): void {
  const windowWithTimers = window as unknown as {
    setTimeout: typeof globalThis.setTimeout;
    clearTimeout: typeof globalThis.clearTimeout;
  };
  windowWithTimers.setTimeout = globalThis.setTimeout.bind(globalThis);
  windowWithTimers.clearTimeout = globalThis.clearTimeout.bind(globalThis);
}

describe("RendererApiRequestSession", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    rendererBatchControllers.clear();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("adds the active batch abort group to Electron API requests", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, receivedAt: "now" });
    installElectronApi(request);
    const session = new RendererApiRequestSession();

    await session.beginBatch("workspace-refresh");
    await session.request({ endpoint: "/api/settings" });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/settings",
      abortGroup: "workspace-refresh"
    });
  });

  it("restores nested batch abort groups when an inner batch ends", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, receivedAt: "now" });
    installElectronApi(request);
    const session = new RendererApiRequestSession();

    await session.beginBatch("outer");
    await session.beginBatch("inner");
    session.endBatch("inner");
    await session.request({ endpoint: "/api/system/info" });
    session.endBatch("outer");
    await session.request({ endpoint: "/api/system/diagnostics" });

    expect(request).toHaveBeenNthCalledWith(1, {
      endpoint: "/api/system/info",
      abortGroup: "outer"
    });
    expect(request).toHaveBeenNthCalledWith(2, {
      endpoint: "/api/system/diagnostics"
    });
  });

  it("lets explicit request abort groups override the active batch", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, receivedAt: "now" });
    installElectronApi(request);
    const session = new RendererApiRequestSession();

    await session.beginBatch("workspace-refresh");
    await session.request({ endpoint: "/api/system/info", abortGroup: "manual" });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/system/info",
      abortGroup: "manual"
    });
  });

  it("does not attach the active batch abort group to mutations", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, receivedAt: "now" });
    installElectronApi(request);
    const session = new RendererApiRequestSession();

    // A snapshot refresh batch is active while the user submits an approval.
    await session.beginBatch("task-snapshot");
    await session.request({ endpoint: "/api/approvals/a1/decision", method: "POST", body: { decision: "approve" } });

    // The POST must not carry the batch group, otherwise the next
    // beginBatch("task-snapshot") -> abortInflight would cancel it silently.
    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/approvals/a1/decision",
      method: "POST",
      body: { decision: "approve" }
    });
  });

  it("still lets a mutation opt into an explicit abort group", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, receivedAt: "now" });
    installElectronApi(request);
    const session = new RendererApiRequestSession();

    await session.beginBatch("task-snapshot");
    await session.request({ endpoint: "/api/chat", method: "POST", abortGroup: "manual" });

    expect(request).toHaveBeenCalledWith({
      endpoint: "/api/chat",
      method: "POST",
      abortGroup: "manual"
    });
  });

  it("aborts local batch controllers and notifies Electron", async () => {
    const abortInflight = vi.fn().mockResolvedValue(undefined);
    installElectronApi(vi.fn(), abortInflight);
    const controller = new AbortController();
    rendererBatchControllers.set("workspace-refresh", controller);
    const session = new RendererApiRequestSession();

    await session.abortInflight("workspace-refresh");

    expect(controller.signal.aborted).toBe(true);
    expect(rendererBatchControllers.has("workspace-refresh")).toBe(false);
    expect(abortInflight).toHaveBeenCalledWith("workspace-refresh");
  });

  it("keeps abort cleanup best-effort when Electron is already closing", async () => {
    const abortInflight = vi.fn().mockRejectedValue(new Error("window closed"));
    installElectronApi(vi.fn(), abortInflight);
    const session = new RendererApiRequestSession();

    await expect(session.abortInflight("workspace-refresh")).resolves.toBeUndefined();
    await expect(session.beginBatch("workspace-refresh")).resolves.toBeUndefined();
  });

  it("falls back to direct loopback requests without Electron", async () => {
    installWindowTimers();
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    }));
    vi.stubGlobal("fetch", fetch);
    const session = new RendererApiRequestSession();

    const response = await session.request<{ status: string }>({ endpoint: "/api/health" });

    expect(response).toMatchObject({ ok: true, status: 200, data: { status: "ok" } });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(String(fetch.mock.calls[0][0])).toBe("http://127.0.0.1:8000/api/health");
  });

  it("emits renderer request diagnostics before dispatch", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, receivedAt: "now" });
    installElectronApi(request);
    const dispatchEvent = vi.fn().mockReturnValue(true);
    (window as unknown as { dispatchEvent: typeof dispatchEvent }).dispatchEvent = dispatchEvent;
    const session = new RendererApiRequestSession();

    await session.request({ endpoint: "/api/system/info" });

    const event = dispatchEvent.mock.calls[0][0] as CustomEvent;
    expect(event.type).toBe("lengrvis-api-request");
    expect(event.detail).toEqual({ endpoint: "/api/system/info", method: "GET" });
  });

  it("normalizes rejected Electron IPC requests into a safe ApiResponse", async () => {
    const request = vi.fn().mockRejectedValue(
      new Error("authorization=super-secret-token at C:\\Users\\Private\\backend.log")
    );
    installElectronApi(request);
    const session = new RendererApiRequestSession();

    const response = await session.request({ endpoint: "/api/system/info" });

    expect(response).toMatchObject({
      ok: false,
      status: 0,
      error: {
        code: "IPC_REQUEST_FAILED",
        message: "Lengrvis 桌面连接暂时不可用，请重启应用后再试。"
      }
    });
    expect(JSON.stringify(response)).not.toContain("super-secret-token");
    expect(JSON.stringify(response)).not.toContain("Users\\Private");
  });

  it("normalizes synchronous and asynchronous specialized IPC failures", async () => {
    const [synchronous, asynchronous] = await Promise.all([
      safeIpcApiRequest(() => {
        throw new Error("sync token=super-secret at C:\\Private\\bridge.ts");
      }),
      safeIpcApiRequest(() => Promise.reject(new Error("async token=super-secret")))
    ]);

    for (const response of [synchronous, asynchronous]) {
      expect(response).toMatchObject({
        ok: false,
        status: 0,
        error: {
          code: "IPC_REQUEST_FAILED",
          message: "Lengrvis 桌面连接暂时不可用，请重启应用后再试。"
        }
      });
      expect(JSON.stringify(response)).not.toContain("super-secret");
      expect(JSON.stringify(response)).not.toContain("Private");
    }
  });
});
