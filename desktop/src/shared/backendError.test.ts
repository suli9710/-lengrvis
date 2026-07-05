import { describe, expect, it } from "vitest";

import { backendErrorMessage, structuredBackendErrorMessage } from "./backendError";

describe("backendError", () => {
  it("formats FastAPI structured detail messages with next actions", () => {
    expect(
      backendErrorMessage({
        detail: {
          message: "LAN HTTPS/WSS is not ready",
          next_action: "Configure LAN TLS, then generate a new pairing code."
        }
      })
    ).toBe("LAN HTTPS/WSS is not ready 下一步：Configure LAN TLS, then generate a new pairing code.");
  });

  it("prioritizes structured detail messages over top-level messages", () => {
    expect(
      backendErrorMessage({
        message: "generic failure",
        detail: {
          message: "specific failure"
        }
      })
    ).toBe("specific failure");
  });

  it("falls back through common backend error shapes", () => {
    expect(backendErrorMessage({ message: "top-level" })).toBe("top-level");
    expect(backendErrorMessage({ detail: "plain detail" })).toBe("plain detail");
    expect(backendErrorMessage({ error: { message: "nested" } })).toBe("nested");
    expect(backendErrorMessage({}, "fallback")).toBe("fallback");
  });

  it("ignores malformed structured details", () => {
    expect(structuredBackendErrorMessage({ detail: { message: " " } })).toBe("");
    expect(structuredBackendErrorMessage({ detail: { message: "safe", next_action: 42 } })).toBe("safe");
  });
});
