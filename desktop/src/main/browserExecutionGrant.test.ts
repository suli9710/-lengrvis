import { createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";
import { BrowserExecutionGrantVerifier } from "./browserExecutionGrant";

const token = "test-desktop-only-key";
function sign(changes: Record<string, unknown> = {}) {
  const payload = Buffer.from(JSON.stringify({
    schema: "browser-execution/v1", nonce: "a".repeat(32), expires_at: 20_000,
    operation: "act", session_id: "session-owned", task_id: "task-owned", account_id: "account-owned",
    profile_key: "scope-owned", allowed_origins: ["https://example.com"],
    action: { kind: "fill", url: "https://example.com/form", selector: "#notes", text: "draft" },
    approval_id: "approval-owned", expected_fingerprint: "b".repeat(64), scope_versions: { "scope-owned": 1 },
    ...changes
  })).toString("base64");
  return { payload, signature: createHmac("sha256", token).update(`browser-execution/v1:${payload}`).digest("hex") };
}

describe("managed browser execution grants", () => {
  it("consumes the exact approved command once", () => {
    const verifier = new BrowserExecutionGrantVerifier(() => token, () => 10_000);
    const grant = sign();
    expect(verifier.consume(grant).action.text).toBe("draft");
    expect(() => verifier.consume(grant)).toThrow("already used");
  });
  it.each([
    { expires_at: 9_000 }, { approval_id: "" }, { expected_fingerprint: "" },
    { allowed_origins: ["https://*.example.com"] },
    { action: { kind: "fill", url: "https://other.example/form", selector: "#notes" } }
  ])("rejects expiry, missing approval and changed scope: %j", (changes) => {
    expect(() => new BrowserExecutionGrantVerifier(() => token, () => 10_000).consume(sign(changes))).toThrow();
  });
  it("rejects payload alteration without a new signature", () => {
    const grant = sign();
    grant.payload = Buffer.from(JSON.stringify({ ...JSON.parse(Buffer.from(grant.payload, "base64").toString()), task_id: "another-task" })).toString("base64");
    expect(() => new BrowserExecutionGrantVerifier(() => token, () => 10_000).consume(grant)).toThrow("signature");
  });
});
