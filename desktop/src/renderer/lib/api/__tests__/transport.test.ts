import { describe, expect, it } from "vitest";
import {
  FALLBACK_BACKEND_URL,
  absoluteRendererLoopbackBackendUrl,
  appendRendererBackendQuery,
  buildRendererLoopbackBackendApiUrl,
  buildRendererLoopbackBackendWebSocketUrl,
  isDeniedRendererApiPath,
  isRendererLoopbackHostname,
  isSafeRendererBackendQueryKey,
  normalizeRendererApiPath,
  normalizeRendererLoopbackBackendBaseUrl,
  validateRendererBackendRelativeEndpoint
} from "../transport";

describe("normalizeRendererLoopbackBackendBaseUrl", () => {
  it("accepts loopback http origins", () => {
    expect(normalizeRendererLoopbackBackendBaseUrl("http://127.0.0.1:8000/some/path")).toBe("http://127.0.0.1:8000");
    expect(normalizeRendererLoopbackBackendBaseUrl("https://localhost:9443")).toBe("https://localhost:9443");
    expect(normalizeRendererLoopbackBackendBaseUrl("http://[::1]:8000")).toBe("http://[::1]:8000");
  });

  it("falls back to the default loopback URL for blank input", () => {
    expect(normalizeRendererLoopbackBackendBaseUrl(undefined)).toBe(FALLBACK_BACKEND_URL);
    expect(normalizeRendererLoopbackBackendBaseUrl("   ")).toBe(FALLBACK_BACKEND_URL);
  });

  it("rejects non-loopback hosts and non-http protocols", () => {
    expect(normalizeRendererLoopbackBackendBaseUrl("http://evil.example.com")).toBeNull();
    expect(normalizeRendererLoopbackBackendBaseUrl("http://192.168.1.10:8000")).toBeNull();
    expect(normalizeRendererLoopbackBackendBaseUrl("ftp://127.0.0.1")).toBeNull();
    expect(normalizeRendererLoopbackBackendBaseUrl("file:///etc/passwd")).toBeNull();
    expect(normalizeRendererLoopbackBackendBaseUrl("not a url")).toBeNull();
  });
});

describe("isRendererLoopbackHostname", () => {
  it("accepts the loopback family", () => {
    for (const host of ["localhost", "LOCALHOST", "127.0.0.1", "127.1.2.3", "::1", "[::1]"]) {
      expect(isRendererLoopbackHostname(host)).toBe(true);
    }
  });

  it("rejects everything else", () => {
    for (const host of ["example.com", "127.0.0.1.evil.com", "0.0.0.0", "10.0.0.1", "128.0.0.1"]) {
      expect(isRendererLoopbackHostname(host)).toBe(false);
    }
  });
});

describe("validateRendererBackendRelativeEndpoint", () => {
  const roots = ["/api"] as const;

  it("returns valid backend-relative endpoints unchanged", () => {
    expect(validateRendererBackendRelativeEndpoint("/api/tasks", roots)).toBe("/api/tasks");
    expect(validateRendererBackendRelativeEndpoint("/api", roots)).toBe("/api");
  });

  it("rejects traversal, separators, schemes and query strings", () => {
    const bad = [
      "/api/../etc/passwd",
      "/api/%2e%2e/secrets",
      "//evil.com/api",
      "/api//double",
      "/api\\windows",
      "/api/%2Fsneaky",
      "http://evil.com/api",
      "/api/tasks?x=1",
      "/api/tasks#frag",
      "/api/tasks ",
      "/outside/api",
      ""
    ];
    for (const endpoint of bad) {
      expect(() => validateRendererBackendRelativeEndpoint(endpoint, roots), endpoint).toThrow();
    }
  });

  it("rejects oversized endpoints", () => {
    expect(() => validateRendererBackendRelativeEndpoint(`/api/${"a".repeat(600)}`, roots)).toThrow();
  });
});

describe("buildRendererLoopbackBackendApiUrl", () => {
  it("builds loopback API URLs with query params", () => {
    const url = buildRendererLoopbackBackendApiUrl("http://127.0.0.1:8000", "/api/tasks", { limit: 5, q: "x" });
    expect(url).toBe("http://127.0.0.1:8000/api/tasks?limit=5&q=x");
  });

  it("returns null for non-loopback base URLs", () => {
    expect(buildRendererLoopbackBackendApiUrl("http://evil.com", "/api/tasks")).toBeNull();
  });

  it("throws for endpoints outside /api", () => {
    expect(() => buildRendererLoopbackBackendApiUrl("http://127.0.0.1:8000", "/ws/stream")).toThrow();
  });
});

describe("buildRendererLoopbackBackendWebSocketUrl", () => {
  it("rewrites http to ws and https to wss", () => {
    expect(buildRendererLoopbackBackendWebSocketUrl("http://127.0.0.1:8000", "/ws/tasks")).toBe("ws://127.0.0.1:8000/ws/tasks");
    expect(buildRendererLoopbackBackendWebSocketUrl("https://localhost:9443", "/api/ws/runs")).toBe(
      "wss://localhost:9443/api/ws/runs"
    );
  });

  it("only allows websocket roots", () => {
    expect(() => buildRendererLoopbackBackendWebSocketUrl("http://127.0.0.1:8000", "/api/tasks")).toThrow();
  });
});

describe("absoluteRendererLoopbackBackendUrl", () => {
  it("resolves relative paths against the loopback backend", () => {
    expect(absoluteRendererLoopbackBackendUrl("/static/img.png", "http://127.0.0.1:8000")).toBe(
      "http://127.0.0.1:8000/static/img.png"
    );
  });

  it("refuses cross-origin absolute URLs", () => {
    expect(absoluteRendererLoopbackBackendUrl("http://evil.com/x.png", "http://127.0.0.1:8000")).toBe("");
  });

  it("returns empty string for empty input", () => {
    expect(absoluteRendererLoopbackBackendUrl("", "http://127.0.0.1:8000")).toBe("");
  });
});

describe("appendRendererBackendQuery", () => {
  it("appends primitive values and skips null/undefined", () => {
    const url = new URL("http://127.0.0.1:8000/api/x");
    appendRendererBackendQuery(url, { a: 1, b: "two", c: true, d: null, e: undefined });
    expect(url.search).toBe("?a=1&b=two&c=true");
  });

  it("rejects prototype-polluting keys and non-primitive values", () => {
    const url = new URL("http://127.0.0.1:8000/api/x");
    expect(() => appendRendererBackendQuery(url, JSON.parse('{"__proto__": "x"}'))).toThrow();
    expect(() => appendRendererBackendQuery(url, { constructor: "x" } as never)).toThrow();
    expect(() => appendRendererBackendQuery(url, { ok: Number.NaN })).toThrow();
    expect(() => appendRendererBackendQuery(url, { ok: {} as never })).toThrow();
  });
});

describe("isSafeRendererBackendQueryKey", () => {
  it("accepts plain keys and rejects dangerous ones", () => {
    expect(isSafeRendererBackendQueryKey("page_size")).toBe(true);
    expect(isSafeRendererBackendQueryKey("constructor")).toBe(false);
    expect(isSafeRendererBackendQueryKey("prototype")).toBe(false);
    expect(isSafeRendererBackendQueryKey("a".repeat(97))).toBe(false);
    expect(isSafeRendererBackendQueryKey("bad key")).toBe(false);
  });
});

describe("normalizeRendererApiPath / isDeniedRendererApiPath", () => {
  it("normalizes trailing slashes", () => {
    expect(normalizeRendererApiPath("/api/tasks/")).toBe("/api/tasks");
  });

  it("denies desktop-bridge-only paths in web mode", () => {
    expect(isDeniedRendererApiPath("/api/commands/execute", "POST")).toBe(true);
    expect(isDeniedRendererApiPath("/api/pair/start", "POST")).toBe(true);
    expect(isDeniedRendererApiPath("/api/runs", "POST")).toBe(true);
    expect(isDeniedRendererApiPath("/api/tasks/t1/rollback", "POST")).toBe(true);
    expect(isDeniedRendererApiPath("/api/settings/permission-policy", "PUT")).toBe(true);
  });

  it("allows ordinary read paths", () => {
    expect(isDeniedRendererApiPath("/api/tasks", "GET")).toBe(false);
    expect(isDeniedRendererApiPath("/api/runs", "GET")).toBe(false);
    expect(isDeniedRendererApiPath("/api/settings", "GET")).toBe(false);
  });
});
