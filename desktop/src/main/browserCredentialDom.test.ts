import { describe, expect, it } from "vitest";

import {
  capturePageCredentialScript,
  credentialPageFingerprintScript,
  fillPageCredentialScript,
  parseCapturedPageCredential,
  parseFilledPageCredentialResult
} from "./browserCredentialDom";

describe("browserCredentialDom", () => {
  it("accepts ordinary credentials and rejects MFA/verification capture", () => {
    const fingerprint = `sha256:${"a".repeat(64)}`;
    expect(parseCapturedPageCredential({
      ok: true,
      username: "alice",
      password: "secret",
      origin: "https://example.test:8443",
      page_fingerprint: fingerprint
    })).toEqual({
      username: "alice",
      password: "secret",
      origin: "https://example.test:8443",
      page_fingerprint: fingerprint
    });
    expect(() => parseCapturedPageCredential({ ok: false, error_code: "mfa-or-verification-field" }))
      .toThrow(/cannot be saved/);
    expect(capturePageCredentialScript()).toContain("one[-_ ]?time");
    expect(capturePageCredentialScript()).toContain("new[-_ ]?password");
    expect(capturePageCredentialScript()).toContain('["one-time-code", "new-password"]');
    expect(credentialPageFingerprintScript()).toContain('crypto.subtle.digest("SHA-256"');
  });

  it("builds a domain-locked fill script whose result contains no credential values", () => {
    const script = fillPageCredentialScript("https://example.test:8443", `sha256:${"a".repeat(64)}`, "alice", "secret");
    expect(script).toContain("location.origin !==");
    expect(script).toContain("page-fingerprint-mismatch");
    expect(script).toContain("confirm[-_ ]?password");
    expect(parseFilledPageCredentialResult({ ok: true, filled_username: true, filled_password: true }))
      .toEqual({ filled_username: true, filled_password: true });
    expect(() => parseFilledPageCredentialResult({ ok: false, error_code: "origin-mismatch" }))
      .toThrow(/page or credential fields changed/);
  });
});
