import { describe, expect, it } from "vitest";

import { validateMemoryReviewRequest } from "./memorySchedule";

describe("validateMemoryReviewRequest", () => {
  it("normalizes camelCase review input", () => {
    expect(validateMemoryReviewRequest({
      memoryId: "mem-123",
      reviewedBy: " desktop-user ",
      conflictStatus: "RESOLVED",
      resolveConflict: true
    })).toEqual({
      memoryId: "mem-123",
      reviewedBy: "desktop-user",
      conflictStatus: "resolved",
      resolveConflict: true
    });
  });

  it("accepts backend field aliases but rejects extra fields", () => {
    expect(validateMemoryReviewRequest({ memory_id: "mem-456", reviewed_by: "reviewer" })).toEqual({
      memoryId: "mem-456",
      reviewedBy: "reviewer",
      conflictStatus: undefined,
      resolveConflict: undefined
    });
    expect(() => validateMemoryReviewRequest({ memoryId: "mem-456", content: "replace it" })).toThrow(
      "memory review request field is not allowed: content"
    );
  });

  it("rejects invalid identifiers and conflict states", () => {
    expect(() => validateMemoryReviewRequest({ memoryId: "../mem" })).toThrow();
    expect(() => validateMemoryReviewRequest({ memoryId: "mem-1", conflictStatus: "trusted" })).toThrow(
      "memory conflict status is invalid"
    );
  });
});
