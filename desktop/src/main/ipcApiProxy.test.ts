import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { proxyApiRequest } from "./ipcApiProxy";

const TOKEN = "ipc-proxy-test-token";

const fetchMock = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
  expect(init?.redirect).toBe("error");
  expect(new Headers(init?.headers).get("X-Lengrvis-Desktop-Token")).toBe(TOKEN);
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" }
  });
});

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("proxyApiRequest", () => {
  it("refuses redirects for requests carrying the desktop token", async () => {
    const result = await proxyApiRequest<{ ok: boolean }>(
      "http://127.0.0.1:8000",
      { endpoint: "/api/settings" },
      TOKEN
    );

    expect(result).toMatchObject({ ok: true, status: 200, data: { ok: true } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://127.0.0.1:8000/api/settings");
  });
});
