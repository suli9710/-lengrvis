import { describe, expect, it } from "vitest";

import { zhBackendTaskStatus } from "../lib/zh";
import { taskExplainStatusTone } from "./TaskExplainDialog";

describe("taskExplainStatusTone", () => {
  it("uses danger for terminal failures", () => {
    expect(taskExplainStatusTone("failed")).toBe("danger");
    expect(taskExplainStatusTone("denied")).toBe("danger");
    expect(taskExplainStatusTone("cancelled")).toBe("danger");
    expect(taskExplainStatusTone("repair_required")).toBe("danger");
  });

  it("uses warning for blocked or waiting work", () => {
    expect(taskExplainStatusTone("blocked")).toBe("warning");
    expect(taskExplainStatusTone("awaiting_approval")).toBe("warning");
    expect(taskExplainStatusTone("rolled_back")).toBe("warning");
  });

  it("keeps completed and running states distinct", () => {
    expect(taskExplainStatusTone("completed")).toBe("success");
    expect(taskExplainStatusTone("running")).toBe("info");
    expect(taskExplainStatusTone("execution")).toBe("info");
    expect(taskExplainStatusTone("final_review")).toBe("info");
  });

  it("localizes every status in the backend TaskPhase contract", () => {
    expect([
      "created",
      "goal_analysis",
      "planning",
      "consultation",
      "plan_review",
      "execution",
      "final_review",
      "completed",
      "failed",
      "cancelled",
      "rolled_back",
      "repair_required"
    ].map(zhBackendTaskStatus)).toEqual([
      "已创建",
      "分析目标中",
      "规划中",
      "Agent 协作中",
      "审核计划中",
      "执行中",
      "核验结果中",
      "已完成",
      "失败",
      "已取消",
      "已回滚",
      "回滚后需要修复"
    ]);
  });
});
