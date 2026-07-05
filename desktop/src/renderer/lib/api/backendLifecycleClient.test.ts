import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiResponse, BackendStatus } from "../../../shared/desktopBridgeTypes";
import {
  getBackendStatusEndpoint,
  probeBackendHealthEndpoint,
  startBackendEndpoint,
  stopBackendEndpoint
} from "./backendLifecycleClient";

function backendStatus(overrides: Partial<BackendStatus> = {}): BackendStatus {
  return {
    state: "running",
    baseUrl: "http://127.0.0.1:8000",
    lastCheckedAt: "2026-07-05T00:00:00.000Z",
    message: "ready",
    health: { ok: true },
    ...overrides
  };
}

function apiResponse<TData>(overrides: Partial<ApiResponse<TData>> = {}): ApiResponse<TData> {
  return {
    ok: true,
    status: 200,
    receivedAt: "2026-07-05T00:00:00.000Z",
    ...overrides
  };
}

describe("backend lifecycle client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    (window as unknown as { lengrvis?: unknown }).lengrvis = undefined;
  });

  it("uses Electron backend adapter when available", async () => {
    const status = backendStatus({ message: "electron-ready" });
    const startStatus = backendStatus({ message: "started" });
    const stopStatus = backendStatus({ state: "stopped", message: "stopped" });
    const backend = {
      getStatus: vi.fn().mockResolvedValue(status),
      start: vi.fn().mockResolvedValue(startStatus),
      stop: vi.fn().mockResolvedValue(stopStatus)
    };
    (window as unknown as { lengrvis?: unknown }).lengrvis = { backend };

    await expect(getBackendStatusEndpoint(vi.fn())).resolves.toBe(status);
    await expect(startBackendEndpoint(async () => backendStatus({ message: "fallback" }))).resolves.toBe(startStatus);
    await expect(stopBackendEndpoint(async () => backendStatus({ message: "fallback" }))).resolves.toBe(stopStatus);

    expect(backend.getStatus).toHaveBeenCalledTimes(1);
    expect(backend.start).toHaveBeenCalledTimes(1);
    expect(backend.stop).toHaveBeenCalledTimes(1);
  });

  it("maps dev-web health checks into backend status", async () => {
    const request = vi.fn().mockResolvedValue(apiResponse({ data: { status: "ok" } }));

    const status = await getBackendStatusEndpoint(request);

    expect(request).toHaveBeenCalledWith({ endpoint: "/api/health", timeoutMs: 1500 });
    expect(status).toMatchObject({
      state: "running",
      baseUrl: "http://127.0.0.1:8000",
      message: "后端已连接",
      health: { ok: true }
    });
    expect(typeof status.health?.latencyMs).toBe("number");
  });

  it("falls back to local status for start and stop outside Electron", async () => {
    const fallbackStatus = backendStatus({ message: "fallback-status" });
    const getStatus = vi.fn().mockResolvedValue(fallbackStatus);

    await expect(startBackendEndpoint(getStatus)).resolves.toBe(fallbackStatus);
    await expect(stopBackendEndpoint(getStatus)).resolves.toBe(fallbackStatus);

    expect(getStatus).toHaveBeenCalledTimes(2);
  });

  it("probes normalized loopback backend URLs and falls back for unsafe hosts", async () => {
    const windowWithTimers = window as unknown as {
      setTimeout: typeof globalThis.setTimeout;
      clearTimeout: typeof globalThis.clearTimeout;
    };
    windowWithTimers.setTimeout = globalThis.setTimeout.bind(globalThis);
    windowWithTimers.clearTimeout = globalThis.clearTimeout.bind(globalThis);
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ status: "ok" }), {
      status: 200,
      headers: { "Content-Type": "application/json" }
    }));
    vi.stubGlobal("fetch", fetch);

    const status = await probeBackendHealthEndpoint("http://localhost:8123/path");
    const fallbackStatus = await probeBackendHealthEndpoint("https://example.com");

    expect(status).toMatchObject({
      state: "running",
      baseUrl: "http://localhost:8123",
      message: "后端已响应任务请求",
      health: { ok: true }
    });
    expect(fallbackStatus).toMatchObject({
      state: "running",
      baseUrl: "http://127.0.0.1:8000",
      health: { ok: true }
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(String(fetch.mock.calls[0][0])).toBe("http://localhost:8123/api/health");
    expect(String(fetch.mock.calls[1][0])).toBe("http://127.0.0.1:8000/api/health");
  });
});
