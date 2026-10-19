import { describe, expect, it, vi } from "vitest";
import type { ApiResponse } from "../shared/types";
import type { InternalDesktopBridgeRequest } from "./ipcApiProxy";
import { performCoordinatedPrivacyErase } from "./ipcPrivacyErase";

const body = { confirm: "erase-local-data", include_settings: false };
const endpoint = "/api/system/privacy/erase-local-data";
const response = <T>(data: T): ApiResponse<T> => ({ ok: true, status: 200, data, receivedAt: "now" });
const rejected = (status: number): ApiResponse<never> => ({ ok: false, status, error: { code: "rejected", message: "rejected" }, receivedAt: "now" });

function fixture(failure?: "desktop" | "backend" | "unknown-prepare" | "unknown-complete", prepareStatus?: number) {
  const calls: string[] = [];
  let pending = false;
  let credentials = 1;
  let backend = "pending";
  let desktop = "pending";
  const eraser = {
    beginLocalPrivateDataErase: vi.fn(() => { calls.push("desktop.reserve"); const previous = pending; pending = true; return previous; }),
    eraseLocalPrivateData: vi.fn(async () => {
      calls.push("desktop.erase"); credentials = 0;
      if (failure === "desktop") throw new Error("partial synthetic failure");
    }),
    finishLocalPrivateDataErase: vi.fn(() => { calls.push("desktop.release"); pending = false; })
  };
  const request = async <T>(input: InternalDesktopBridgeRequest): Promise<ApiResponse<T>> => {
    calls.push(input.endpoint.slice(endpoint.length) || "/erase");
    if (input.endpoint.endsWith("/prepare")) {
      if (prepareStatus !== undefined) return rejected(prepareStatus);
      if (failure === "unknown-prepare") return rejected(0);
      return response({ operation_id: "same-operation", reservation_token: "main-only-capability", backend, desktop }) as ApiResponse<T>;
    }
    expect(input.headers?.["X-Lengrvis-Erase-Reservation-Token"]).toBe("main-only-capability");
    if (input.endpoint.endsWith("/complete")) {
      desktop = (input.body as { desktop_complete: boolean }).desktop_complete ? "complete" : "incomplete";
      if (failure === "unknown-complete" && backend === "complete") return rejected(0);
      return response({ ok: backend === desktop && backend === "complete", progress: { backend, desktop } }) as ApiResponse<T>;
    }
    if (failure === "backend") return rejected(500);
    backend = "complete";
    return response({ backend, desktop }) as ApiResponse<T>;
  };
  return {
    run: () => performCoordinatedPrivacyErase({ body, eraser, request, confirmationHeaders: async () => { calls.push("proof"); return { "native-proof": "verified" }; } }),
    calls, eraser,
    get pending() { return pending; },
    get credentials() { return credentials; },
    get desktop() { return desktop; },
    get backend() { return backend; },
    allowRetry: () => { failure = undefined; prepareStatus = undefined; }
  };
}

describe("coordinated privacy erase", () => {
  it.each([409, 403])("does not delete desktop data when preparation rejects busy or expired proof (%s)", async status => {
    const test = fixture(undefined, status);
    expect((await test.run()).ok).toBe(false);
    expect(test.credentials).toBe(1);
    expect(test.eraser.eraseLocalPrivateData).not.toHaveBeenCalled();
    expect(test.pending).toBe(false);
  });

  it("holds the gate and checkpoints desktop completion before a failed backend erase, then retries", async () => {
    const test = fixture("backend");
    const result = await test.run();
    expect(result.error?.code).toBe("LOCAL_DATA_ERASE_INCOMPLETE");
    expect(result.error?.message).toContain("桌面：已删除；后端：待完成");
    expect(test.calls).toEqual(["desktop.reserve", "proof", "/prepare", "desktop.erase", "/complete", "/erase"]);
    expect(test.desktop).toBe("complete");
    expect(test.pending).toBe(true);
    test.allowRetry();
    expect((await test.run()).ok).toBe(true);
    expect(test.backend).toBe("complete");
    expect(test.pending).toBe(false);
    expect(test.eraser.finishLocalPrivateDataErase).toHaveBeenCalledTimes(1);
  });

  it("records partial desktop cleanup and keeps the reservation for retry", async () => {
    const test = fixture("desktop");
    expect((await test.run()).ok).toBe(false);
    expect(test.desktop).toBe("incomplete");
    expect(test.backend).toBe("pending");
    expect(test.pending).toBe(true);
    expect(test.calls).not.toContain("/erase");
  });

  it.each(["unknown-prepare", "unknown-complete"] as const)("retains the gate when HTTP outcome is unknown (%s)", async failure => {
    const test = fixture(failure);
    expect((await test.run()).ok).toBe(false);
    expect(test.pending).toBe(true);
    expect(test.eraser.finishLocalPrivateDataErase).not.toHaveBeenCalled();
    test.allowRetry();
    expect((await test.run()).ok).toBe(true);
  });
});
