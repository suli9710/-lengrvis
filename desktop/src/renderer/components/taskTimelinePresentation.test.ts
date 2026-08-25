import { describe, expect, it } from "vitest";

import type { TaskEvent, TaskState } from "../../shared/executionTypes";
import { timelineUserStatusCopy, toneForState, workspaceAction } from "./taskTimelinePresentation";

const task = (state: TaskState) => ({ state } as TaskEvent);

describe("task timeline terminal-state presentation", () => {
  it("keeps safety denial distinct from user cancellation", () => {
    expect(timelineUserStatusCopy(task("denied"))).toMatchObject({
      stage: "任务被安全或权限边界拒绝",
      tone: "danger"
    });
    expect(timelineUserStatusCopy(task("cancelled"))).toMatchObject({
      stage: "任务已由用户取消",
      tone: "neutral"
    });
    expect(workspaceAction(task("denied"))).toBe("已拒绝");
    expect(workspaceAction(task("cancelled"))).toBe("已取消");
    expect(toneForState("denied")).toBe("danger");
    expect(toneForState("cancelled")).toBe("neutral");
  });
});
