import { describe, expect, it } from "vitest";

import {
  redactSensitiveText,
  redactUrl,
  sanitizeActionForRenderer,
  sanitizeRecordForRenderer
} from "./browserHostRedaction";

describe("browserHostRedaction", () => {
  it("redacts credentials and sensitive query values from URLs", () => {
    expect(redactUrl("https://user:pass@example.test/path?token=abc&safe=1#access_token=secret")).toBe(
      "https://%5Bredacted%5D:%5Bredacted%5D@example.test/path?token=%5Bredacted%5D&safe=1#access_token=[redacted]"
    );
  });

  it("redacts sensitive URL parameters in malformed text", () => {
    expect(redactUrl("open https://example.test/?token=abc&safe=1")).toBe(
      "open https://example.test/?token=[redacted]&safe=1"
    );
  });

  it("redacts bearer tokens, OpenAI-style keys, and embedded URLs from text", () => {
    const redacted = redactSensitiveText(
      "Authorization: Bearer abcdefghij sk-testsecret https://example.test/?password=pw"
    );

    expect(redacted).toContain("Authorization: [redacted]");
    expect(redacted).toContain("sk-[redacted]");
    expect(redacted).toContain("password=[redacted]");
    expect(redacted).not.toContain("testsecret");
    expect(redacted).not.toContain("password=pw");
  });

  it("redacts selectors, typed text, and field names from actions", () => {
    expect(
      sanitizeActionForRenderer({
        kind: "fill",
        url: "https://example.test/login?session=abc",
        selector: "#password",
        text: "secret",
        fields: { password: "secret" }
      })
    ).toEqual({
      kind: "fill",
      url: "https://example.test/login?session=%5Bredacted%5D",
      selector: "[redacted]",
      text: "[redacted]",
      fields: { field_1: "[redacted]" }
    });
  });

  it("redacts nested browser observation records", () => {
    expect(
      sanitizeRecordForRenderer({
        href: "https://example.test/?api_key=abc",
        visible_text: "private body",
        token: "abc",
        nested: [{ url: "https://example.test/?code=abc", label: "safe" }]
      })
    ).toEqual({
      href: "https://example.test/?api_key=%5Bredacted%5D",
      visible_text: "[redacted:text]",
      token: "[redacted]",
      nested: [{ url: "https://example.test/?code=%5Bredacted%5D", label: "safe" }]
    });
  });
});
