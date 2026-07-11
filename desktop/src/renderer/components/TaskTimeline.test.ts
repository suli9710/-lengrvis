import { describe, expect, it } from "vitest";

import { taskControlFailureMessage } from "./TaskTimeline";

describe("taskControlFailureMessage", () => {
  it("preserves an actionable Error message", () => {
    expect(taskControlFailureMessage(new Error("暂停任务失败"))).toBe("暂停任务失败");
  });

  it("normalizes unknown and empty failures", () => {
    expect(taskControlFailureMessage("unexpected rejection")).toBe("任务控制失败");
    expect(taskControlFailureMessage(new Error("   "))).toBe("任务控制失败");
  });
});
