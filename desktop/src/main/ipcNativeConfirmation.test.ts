import { describe, expect, it } from "vitest";

import { approvalAuthorizationError, approvalConfirmationDialogOptions } from "./ipcNativeConfirmation";

describe("approvalAuthorizationError", () => {
  const now = Date.parse("2026-07-11T10:00:00Z");

  it("accepts only fresh pending approval authorizations", () => {
    const fresh = {
      status: "pending",
      expires_at: "2026-07-11T10:15:00Z"
    };

    expect(approvalAuthorizationError(fresh, "approve", now)).toBe("");
    expect(approvalAuthorizationError(fresh, "reject", now)).toBe("");
  });

  it("fails closed for expired, missing and terminal approvals", () => {
    expect(approvalAuthorizationError({ status: "pending", expires_at: "2026-07-11T10:00:00Z" }, "approve", now))
      .toContain("expired");
    expect(approvalAuthorizationError({ status: "pending" }, "approve", now)).toContain("missing or invalid");
    expect(approvalAuthorizationError({ status: "expired", expires_at: "2026-07-11T10:15:00Z" }, "approve", now))
      .toContain("not executable");
    expect(approvalAuthorizationError({ status: "approved", expires_at: "2026-07-11T10:15:00Z" }, "reject", now))
      .toContain("not rejectable");
  });

  it("supports nested approval detail payloads", () => {
    expect(approvalAuthorizationError({
      approval: { status: "pending", expires_at: "2026-07-11T10:15:00Z" }
    }, "approve", now)).toBe("");
  });
});

it("includes the actual form destination and ordinary business content in native confirmation", () => {
  const dialog = approvalConfirmationDialogOptions("approval-owned", { diff_preview: { confirmation_details: [
    { destination: "https://example.com/commit", items: [{ label: "amount", content: "100" }, { label: "message", content: "待发送的普通正文" }] }
  ] } }, "approve");
  expect(dialog.detail).toContain("https://example.com/commit");
  expect(dialog.detail).toContain("待发送的普通正文");
  expect(dialog.detail).toContain('"100"');
});
