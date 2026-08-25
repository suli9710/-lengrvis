import { describe, expect, it } from "vitest";

import type { ApprovalRequest } from "../shared/executionTypes";
import { approvalAuthorizationIsFresh, selectedPendingApproval } from "./appViewModel";

function approval(overrides: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id: "approval-1",
    title: "审批",
    reason: "确认动作",
    requester: "HumanGateAgent",
    riskLevel: "medium",
    createdAt: "2026-07-11T10:00:00Z",
    expiresAt: "2026-07-11T10:15:00Z",
    proposedAction: "write",
    status: "pending",
    ...overrides
  };
}

describe("approval freshness", () => {
  it("requires a pending approval with a valid future expiry", () => {
    const now = Date.parse("2026-07-11T10:10:00Z");

    expect(approvalAuthorizationIsFresh(approval(), now)).toBe(true);
    expect(approvalAuthorizationIsFresh(approval({ expiresAt: "2026-07-11T10:10:00Z" }), now)).toBe(false);
    expect(approvalAuthorizationIsFresh(approval({ expiresAt: undefined }), now)).toBe(false);
    expect(approvalAuthorizationIsFresh(approval({ status: "approved" }), now)).toBe(false);
  });

  it("skips expired task approvals when selecting the next decision", () => {
    const expired = approval({ id: "expired", taskId: "task-1", expiresAt: "2020-01-01T00:00:00Z" });
    const fresh = approval({ id: "fresh", taskId: "task-2", expiresAt: "2999-01-01T00:00:00Z" });

    expect(selectedPendingApproval([expired, fresh], "task-1")?.id).toBe("fresh");
  });
});
