import { createHmac } from "node:crypto";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BackendControlTransport } from "./backendControlTransport";

const TOKEN = "desktop-control-test-token";

type FetchHandler = (url: URL, init?: RequestInit) => Promise<Response>;

let fetchHandler: FetchHandler;
const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
  const url = input instanceof Request ? new URL(input.url) : new URL(String(input));
  return fetchHandler(url, init);
});

function jsonResponse(body: unknown, status = 200, statusText = "OK"): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { "Content-Type": "application/json" }
  });
}

function authenticatedHealth(url: URL, body: Record<string, unknown> = {}): Response {
  const challenge = url.searchParams.get("desktop_challenge") ?? "";
  const desktopProof = createHmac("sha256", TOKEN).update(challenge, "utf8").digest("hex");
  return jsonResponse({ status: "ok", desktop_proof: desktopProof, ...body });
}

function headerValue(init: RequestInit | undefined, name: string): string | null {
  return new Headers(init?.headers).get(name);
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

beforeEach(() => {
  fetchHandler = async (url) => {
    throw new Error(`Unexpected fetch: ${url}`);
  };
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("BackendControlTransport", () => {
  it.each([0, -1, Number.POSITIVE_INFINITY, Number.NaN])(
    "rejects an invalid identity lease TTL: %s",
    (identityLeaseTtlMs) => {
      expect(() => new BackendControlTransport(
        () => "http://127.0.0.1:8000",
        TOKEN,
        { identityLeaseTtlMs }
      )).toThrow(/positive finite duration/);
    }
  );

  it("proves loopback identity before sending the token and allowlists runtime fields", async () => {
    fetchHandler = async (url, init) => {
      if (url.pathname === "/health") return authenticatedHealth(url);
      expect(url.pathname).toBe("/api/runtime/status");
      expect(init?.redirect).toBe("error");
      expect(headerValue(init, "X-Lengrvis-Desktop-Token")).toBe(TOKEN);
      return jsonResponse({
        shellMode: "foreground",
        guardianState: "running",
        fullBackendState: "running",
        fullBackendPort: 8123,
        lastWakeReason: "desktop_opened",
        secretInternalField: "must-not-cross"
      });
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    const { health, runtime } = await transport.probeStatus();

    expect(health).toMatchObject({ ok: true, identityVerified: true });
    expect(runtime).toEqual({
      shellMode: "foreground",
      guardianState: "running",
      fullBackendState: "running",
      fullBackendPort: 8123,
      lastWakeReason: "desktop_opened"
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [healthUrl, healthInit] = fetchMock.mock.calls[0];
    expect(new URL(String(healthUrl)).searchParams.get("desktop_challenge")).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(headerValue(healthInit, "X-Lengrvis-Desktop-Token")).toBeNull();
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
  });

  it("clears a previous proof when a later health challenge is missing or invalid", async () => {
    let valid = true;
    fetchHandler = async (url) => valid ? authenticatedHealth(url) : jsonResponse({ status: "ok" });
    const transport = new BackendControlTransport(() => "http://localhost:8000", TOKEN);

    await expect(transport.probeHealth()).resolves.toMatchObject({ identityVerified: true });
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    valid = false;
    await expect(transport.probeHealth()).resolves.toMatchObject({ ok: false, identityVerified: false });
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    transport.invalidateIdentity();
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
  });

  it.each([
    [{ mode: "guardian", shellMode: "foreground", fullBackendState: "running" }, true],
    [{ mode: "guardian", shellMode: "background", fullBackendState: "running" }, false],
    [{ mode: "guardian", shellMode: "foreground", fullBackendState: "starting" }, false]
  ])("requires a foreground running full backend for guardian health: %o", async (body, expectedOk) => {
    fetchHandler = async (url) => authenticatedHealth(url, body);
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    await expect(transport.probeHealth()).resolves.toMatchObject({
      ok: expectedOk,
      identityVerified: true
    });
  });

  it("maps runtime modes to fixed endpoints and returns redacted-boundary errors", async () => {
    fetchHandler = async (url, init) => {
      if (url.pathname === "/health") return authenticatedHealth(url);
      expect(init?.redirect).toBe("error");
      expect(headerValue(init, "X-Lengrvis-Desktop-Token")).toBe(TOKEN);
      expect(init?.method).toBe("POST");
      if (url.pathname === "/api/runtime/foreground") {
        expect(init?.body).toBe(JSON.stringify({ reason: "desktop_opened" }));
        return jsonResponse({ detail: "guardian not ready" }, 503, "Service Unavailable");
      }
      expect(url.pathname).toBe("/api/runtime/background");
      expect(init?.body).toBe(JSON.stringify({ reason: "tray_background" }));
      return jsonResponse({ ok: true });
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    const foregroundError = await transport.setRuntimeMode("foreground", "desktop_opened");
    expect(foregroundError?.message).toMatch(/503.*guardian not ready/);
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    await expect(transport.setRuntimeMode("background", "tray_background")).resolves.toBeNull();
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    expect(fetchMock.mock.calls.filter(([url]) => new URL(String(url)).pathname === "/health")).toHaveLength(2);
  });

  it("requires a fresh proof before emergency stop and normalizes malformed results", async () => {
    fetchHandler = async (url, init) => {
      if (url.pathname === "/health") {
        expect(headerValue(init, "X-Lengrvis-Desktop-Token")).toBeNull();
        return authenticatedHealth(url);
      }
      expect(url.pathname).toBe("/api/runtime/emergency-stop");
      expect(init?.redirect).toBe("error");
      expect(headerValue(init, "X-Lengrvis-Desktop-Token")).toBe(TOKEN);
      return jsonResponse(["not-an-object"]);
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    await expect(transport.emergencyStop()).resolves.toEqual({ ok: false });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never sends or exposes the desktop token for a non-loopback base URL", async () => {
    let baseUrl = "http://127.0.0.1:8000";
    fetchHandler = async (url, init) => {
      expect(url.pathname).toBe("/health");
      expect(headerValue(init, "X-Lengrvis-Desktop-Token")).toBeNull();
      return authenticatedHealth(url);
    };
    const transport = new BackendControlTransport(() => baseUrl, TOKEN);
    await transport.probeHealth();
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);

    baseUrl = "https://api.example.test";
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    await expect(transport.probeHealth()).resolves.toMatchObject({ identityVerified: true });
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    await expect(transport.setRuntimeMode("foreground", "remote")).resolves.toBeInstanceOf(Error);
    await expect(transport.emergencyStop()).rejects.toThrow(/loopback backend base URL/);
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    expect(fetchMock.mock.calls.every(([, init]) => (
      headerValue(init, "X-Lengrvis-Desktop-Token") === null
    ))).toBe(true);
  });

  it("binds a verified token to the exact loopback origin", async () => {
    let baseUrl = "http://127.0.0.1:8000/guardian";
    fetchHandler = async (url) => authenticatedHealth(url);
    const transport = new BackendControlTransport(() => baseUrl, TOKEN);

    await expect(transport.probeHealth()).resolves.toMatchObject({ identityVerified: true });
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    baseUrl = "http://127.0.0.1:8000/another-path";
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    baseUrl = "http://127.0.0.1:8001";
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    baseUrl = "http://127.0.0.1:8000";
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("expires the compatibility token lease instead of releasing it indefinitely", async () => {
    let now = 1_000;
    fetchHandler = async (url) => authenticatedHealth(url);
    const transport = new BackendControlTransport(
      () => "http://127.0.0.1:8000",
      TOKEN,
      { identityLeaseTtlMs: 50, now: () => now }
    );

    await expect(transport.probeHealth()).resolves.toMatchObject({ identityVerified: true });
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    now += 51;
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    now = 1_000;
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
  });

  it("ignores an older valid proof that arrives after a newer invalid proof", async () => {
    const firstHealth = deferred<Response>();
    let firstHealthUrl: URL | null = null;
    let healthCalls = 0;
    fetchHandler = async (url) => {
      healthCalls += 1;
      if (healthCalls === 1) {
        firstHealthUrl = new URL(url);
        return firstHealth.promise;
      }
      return jsonResponse({ status: "ok" });
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    const olderProbe = transport.probeHealth();
    const newerProbe = transport.probeHealth();
    await expect(newerProbe).resolves.toMatchObject({ identityVerified: false });
    expect(firstHealthUrl).not.toBeNull();
    firstHealth.resolve(authenticatedHealth(firstHealthUrl!));
    await expect(olderProbe).resolves.toMatchObject({ ok: false, identityVerified: false });

    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not send the token when a proof lease becomes stale before dispatch", async () => {
    const firstHealth = deferred<Response>();
    let firstHealthUrl: URL | null = null;
    let healthCalls = 0;
    fetchHandler = async (url) => {
      expect(url.pathname).toBe("/health");
      healthCalls += 1;
      if (healthCalls === 1) {
        firstHealthUrl = new URL(url);
        return firstHealth.promise;
      }
      return jsonResponse({ status: "ok" });
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    const modeAttempt = transport.setRuntimeMode("foreground", "stale-proof");
    await expect(transport.probeHealth()).resolves.toMatchObject({ identityVerified: false });
    expect(firstHealthUrl).not.toBeNull();
    firstHealth.resolve(authenticatedHealth(firstHealthUrl!));
    await expect(modeAttempt).resolves.toBeInstanceOf(Error);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.every(([, init]) => (
      headerValue(init, "X-Lengrvis-Desktop-Token") === null
    ))).toBe(true);
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
  });

  it("rechecks a proof lease immediately before dispatching the token", async () => {
    let baseUrlReads = 0;
    const getBaseUrl = () => {
      baseUrlReads += 1;
      return baseUrlReads < 3 ? "http://127.0.0.1:8000" : "http://127.0.0.1:8001";
    };
    fetchHandler = async (url) => {
      expect(url.pathname).toBe("/health");
      return authenticatedHealth(url);
    };
    const transport = new BackendControlTransport(getBaseUrl, TOKEN);

    await expect(transport.setRuntimeMode("foreground", "origin-changed")).resolves.toBeInstanceOf(Error);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(headerValue(fetchMock.mock.calls[0][1], "X-Lengrvis-Desktop-Token")).toBeNull();
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
  });

  it("revokes a proof when status processing observes an origin change", async () => {
    let baseUrl = "http://127.0.0.1:8000";
    fetchHandler = async (url) => {
      expect(url.pathname).toBe("/health");
      baseUrl = "http://127.0.0.1:8001";
      return authenticatedHealth(url);
    };
    const transport = new BackendControlTransport(() => baseUrl, TOKEN);

    await expect(transport.probeStatus()).resolves.toMatchObject({
      health: { ok: false, identityVerified: false },
      runtime: {}
    });
    baseUrl = "http://127.0.0.1:8000";
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not let an older control failure revoke a newer valid proof", async () => {
    const oldControlResponse = deferred<Response>();
    fetchHandler = async (url) => {
      if (url.pathname === "/health") return authenticatedHealth(url);
      expect(url.pathname).toBe("/api/runtime/foreground");
      return oldControlResponse.promise;
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    const oldControl = transport.setRuntimeMode("foreground", "old-control");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await expect(transport.probeHealth()).resolves.toMatchObject({ identityVerified: true });
    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    oldControlResponse.resolve(jsonResponse({ detail: "late failure" }, 503, "Service Unavailable"));
    await expect(oldControl).resolves.toBeInstanceOf(Error);

    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("downgrades an older status result that completes after a newer proof", async () => {
    const oldRuntimeResponse = deferred<Response>();
    fetchHandler = async (url) => {
      if (url.pathname === "/health") return authenticatedHealth(url);
      expect(url.pathname).toBe("/api/runtime/status");
      return oldRuntimeResponse.promise;
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    const oldStatus = transport.probeStatus();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await expect(transport.probeHealth()).resolves.toMatchObject({ identityVerified: true });
    oldRuntimeResponse.resolve(jsonResponse({ shellMode: "foreground", guardianState: "running" }));
    await expect(oldStatus).resolves.toMatchObject({
      health: { ok: false, identityVerified: false },
      runtime: {}
    });

    expect(transport.getVerifiedDesktopApiToken()).toBe(TOKEN);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("rejects redirected or cross-origin health challenge responses", async () => {
    fetchHandler = async (url, init) => {
      expect(init?.redirect).toBe("error");
      const response = authenticatedHealth(url);
      Object.defineProperty(response, "url", {
        configurable: true,
        value: "http://127.0.0.1:8001/health"
      });
      return response;
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    await expect(transport.probeHealth()).resolves.toMatchObject({
      ok: false,
      identityVerified: false
    });
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
  });

  it("probes only health for remote origins and invalid identity proofs", async () => {
    fetchHandler = async (url, init) => {
      expect(url.pathname).toBe("/health");
      expect(headerValue(init, "X-Lengrvis-Desktop-Token")).toBeNull();
      return url.hostname === "api.example.test"
        ? authenticatedHealth(url)
        : jsonResponse({ status: "ok" });
    };
    const remote = new BackendControlTransport(() => "https://api.example.test", TOKEN);
    const invalid = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    await expect(remote.probeStatus()).resolves.toMatchObject({
      health: { identityVerified: true },
      runtime: {}
    });
    await expect(invalid.probeStatus()).resolves.toMatchObject({
      health: { identityVerified: false },
      runtime: {}
    });
    await expect(invalid.setRuntimeMode("foreground", "invalid-proof")).resolves.toBeInstanceOf(Error);
    await expect(invalid.emergencyStop()).rejects.toThrow(/identity challenge failed/);

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(fetchMock.mock.calls.every(([url]) => new URL(String(url)).pathname === "/health")).toBe(true);
  });

  it("clears identity after non-success runtime mode and emergency responses", async () => {
    fetchHandler = async (url) => {
      if (url.pathname === "/health") return authenticatedHealth(url);
      return jsonResponse({ detail: "control rejected" }, 503, "Service Unavailable");
    };
    const transport = new BackendControlTransport(() => "http://127.0.0.1:8000", TOKEN);

    await expect(transport.setRuntimeMode("foreground", "rejected")).resolves.toBeInstanceOf(Error);
    expect(transport.getVerifiedDesktopApiToken()).toBe("");
    await expect(transport.emergencyStop()).rejects.toThrow(/503/);
    expect(transport.getVerifiedDesktopApiToken()).toBe("");

    expect(fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)).toEqual([
      "/health",
      "/api/runtime/foreground",
      "/health",
      "/api/runtime/emergency-stop"
    ]);
  });
});
