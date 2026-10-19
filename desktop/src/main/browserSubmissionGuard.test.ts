import { describe, expect, it } from "vitest";
import { BrowserSubmissionGuard, type BrowserHostRequest } from "./browserSubmissionGuard";

const expected = {
  url: "https://example.test/submit?flow=owned",
  entries: [["amount", "100"], ["notes", "中文 😀\r\n第二行"], ["choice", "a"], ["choice", "b"]] as [string, string][]
};
const request = (): BrowserHostRequest => ({
  url: expected.url, method: "POST", resourceType: "mainFrame",
  uploadData: [{ bytes: Buffer.from(new URLSearchParams(expected.entries).toString()) }]
});

describe("native submission request binding", () => {
  it("permits one exact request, preserves duplicate fields and Unicode, and allows a receipt GET", async () => {
    const guard = new BrowserSubmissionGuard();
    const attempt = guard.arm(expected);
    const original = request();
    const bytes = original.uploadData![0].bytes;
    original.uploadData = [{ bytes: bytes.subarray(0, 10) }, { bytes: bytes.subarray(10) }];
    expect(guard.allowRequest(original, true)).toBe(true);
    expect(await attempt.wait()).toBe("submitted");
    expect(guard.allowRequest(request(), true)).toBe(false);
    expect(guard.allowRequest({ url: "https://example.test/receipt", method: "GET", resourceType: "mainFrame" }, true)).toBe(true);
  });

  it.each(["amount", "url", "method", "duplicate", "file", "blob", "oversized", "network"])("cancels a changed %s before submission", async change => {
    const guard = new BrowserSubmissionGuard();
    const attempt = guard.arm(expected);
    const changed = request();
    if (change === "amount") changed.uploadData = [{ bytes: Buffer.from("amount=777") }];
    if (change === "url") changed.url += "&other=1";
    if (change === "method") changed.method = "GET";
    if (change === "duplicate") changed.uploadData![0].bytes = Buffer.concat([changed.uploadData![0].bytes, Buffer.from("&amount=100")]);
    if (change === "file") changed.uploadData![0].file = "C:/private.txt";
    if (change === "blob") changed.uploadData![0].blobUUID = "blob-handle";
    if (change === "oversized") changed.uploadData![0].bytes = Buffer.alloc(256 * 1024 + 1);
    expect(guard.allowRequest(changed, change !== "network")).toBe(false);
    expect(await attempt.wait()).toBe("not_submitted");
    expect(guard.allowRequest(request(), true)).toBe(false);
  });

  it("does not let scripts or iframes spend a native navigation approval", async () => {
    const guard = new BrowserSubmissionGuard();
    const attempt = guard.arm(expected);
    expect(guard.allowRequest({ ...request(), resourceType: "xhr" }, true)).toBe(false);
    expect(guard.allowRequest({ ...request(), resourceType: "subFrame" }, true)).toBe(false);
    expect(guard.allowRequest(request(), true)).toBe(true);
    expect(await attempt.wait()).toBe("submitted");
  });

  it("keeps a timed out attempt unknown and blocks a late POST", async () => {
    const guard = new BrowserSubmissionGuard();
    const attempt = guard.arm(expected);
    expect(await attempt.wait(1)).toBe("unknown");
    expect(guard.allowRequest(request(), true)).toBe(false);
  });

  it("requires a new concrete approval after a known rejection", async () => {
    const guard = new BrowserSubmissionGuard();
    const first = guard.arm(expected);
    expect(() => guard.arm(expected)).toThrow("pending");
    first.notSubmitted();
    expect(await first.wait()).toBe("not_submitted");
    const second = guard.arm(expected);
    expect(guard.allowRequest(request(), true)).toBe(true);
    expect(await second.wait()).toBe("submitted");
  });

  it("does not authorize unapproved writes when a managed attempt is released", async () => {
    const guard = new BrowserSubmissionGuard();
    const attempt = guard.arm(expected);
    guard.release();
    expect(await attempt.wait()).toBe("unknown");
    expect(guard.allowRequest(request(), true)).toBe(false);
    expect(guard.allowRequest(request(), false)).toBe(false);
    expect(guard.allowRequest(request(), true, true)).toBe(true);
    guard.close();
    expect(guard.allowRequest(request(), true, true)).toBe(false);
  });

  it("keeps queued requests blocked while a revoked session is being destroyed", async () => {
    const guard = new BrowserSubmissionGuard();
    const attempt = guard.arm(expected);
    guard.close();
    expect(await attempt.wait()).toBe("unknown");
    expect(guard.allowRequest(request(), true)).toBe(false);
    expect(guard.allowRequest({ url: expected.url, method: "GET", resourceType: "mainFrame" }, true)).toBe(false);
    expect(() => guard.arm(expected)).toThrow("closed");
  });
});
