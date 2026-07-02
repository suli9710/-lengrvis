import { afterEach, describe, expect, it } from "vitest";

import {
  browserHostErrorMessage,
  normalizeBrowserHostBounds,
  normalizeBrowserHostId,
  normalizeBrowserHostUrl,
  requireBrowserActionSelector,
  requireBrowserActionUrl
} from "./browserHostValidation";

const PRIVATE_NETWORK_ENV = "LENGRVIS_BROWSER_HOST_ALLOW_PRIVATE_NETWORK";

describe("browserHostValidation", () => {
  afterEach(() => {
    delete process.env[PRIVATE_NETWORK_ENV];
  });

  it("normalizes ids and public URLs while rejecting unsafe schemes", () => {
    expect(normalizeBrowserHostId("  session_1  ")).toBe("session_1");
    expect(normalizeBrowserHostId("   ")).toBeUndefined();
    expect(normalizeBrowserHostUrl("about:blank")).toBe("about:blank");
    expect(normalizeBrowserHostUrl("example.test/docs")).toBe("https://example.test/docs");
    expect(normalizeBrowserHostUrl("http://example.test")).toBe("http://example.test/");
    expect(() => normalizeBrowserHostUrl("file:///C:/Users/Suli/secret.txt")).toThrow(/Only http and https/);
    expect(() => normalizeBrowserHostUrl("http://127.0.0.1:8000")).toThrow(/blocks localhost/);
  });

  it("allows private URLs only with the explicit development override", () => {
    process.env[PRIVATE_NETWORK_ENV] = "1";

    expect(normalizeBrowserHostUrl("http://127.0.0.1:8000")).toBe("http://127.0.0.1:8000/");
  });

  it("clamps bounds to visible positive browser dimensions", () => {
    expect(normalizeBrowserHostBounds({ height: 79.2, width: 12.9, x: -10.4, y: 4.6 })).toEqual({
      height: 80,
      width: 80,
      x: 0,
      y: 5
    });
  });

  it("requires action URLs and selectors with clear errors", () => {
    expect(requireBrowserActionUrl({ kind: "navigate", url: "example.test" })).toBe("https://example.test/");
    expect(requireBrowserActionSelector({ kind: "click", selector: "  button.save  " })).toBe("button.save");
    expect(() => requireBrowserActionUrl({ kind: "navigate" })).toThrow(/requires a URL/);
    expect(() => requireBrowserActionSelector({ kind: "click", selector: " " })).toThrow(/requires a selector/);
  });

  it("keeps user-facing action errors stable", () => {
    expect(browserHostErrorMessage(new Error("navigation failed"))).toBe("navigation failed");
    expect(browserHostErrorMessage("plain failure")).toBe("Browser host action failed");
  });
});
