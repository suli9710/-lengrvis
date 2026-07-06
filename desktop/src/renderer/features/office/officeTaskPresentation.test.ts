import { describe, expect, it, vi } from "vitest";

import type { TaskEvent } from "../../../shared/executionTypes";
import {
  deriveOfficeTaskPresentation,
  taskDisplayState,
  taskDisplayTitle
} from "./officeTaskPresentation";

function task(overrides: Partial<TaskEvent> = {}): TaskEvent {
  return {
    id: "task-1",
    title: "总结季度文档",
    description: "生成文档摘要",
    state: "running",
    agent: "document",
    createdAt: "2026-07-03T01:00:00.000Z",
    updatedAt: "2026-07-03T02:00:00.000Z",
    ...overrides
  };
}

function derive(tasks: TaskEvent[], hasDraft = false) {
  return deriveOfficeTaskPresentation({
    tasks,
    hasDraft,
    readinessItems: [],
    trustItems: [],
    pendingApprovalCount: 0,
    selectedSkill: null
  });
}

describe("deriveOfficeTaskPresentation", () => {
  it("sorts recent work and exposes blocked tasks as the current priority", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-03T03:00:00.000Z"));

    const model = derive([
      task({ id: "running", updatedAt: "2026-07-03T02:00:00.000Z" }),
      task({ id: "blocked", title: "确认文件范围", state: "blocked", updatedAt: "2026-07-03T02:30:00.000Z" }),
      task({ id: "old", state: "completed", updatedAt: "2026-06-30T02:00:00.000Z" })
    ]);

    expect(model.currentTasks.map((item) => item.id)).toEqual(["blocked", "running"]);
    expect(model.displayedTasks.map((item) => item.id)).toEqual(["blocked", "running"]);
    expect(model.activeTaskLabel).toBe("有项目需要你确认");
    expect(model.blockedTaskCount).toBe(1);
    expect(model.runningTaskCount).toBe(1);
    expect(model.taskPilot).toMatchObject({ action: "approve", tone: "blocked", task: { id: "blocked" } });
    expect(model.resultTimeline).toMatchObject({ action: "approve", tone: "blocked", task: { id: "blocked" } });

    vi.useRealTimers();
  });

  it("marks only verified completion evidence as a completed result", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-03T03:00:00.000Z"));

    const model = derive([
      task({
        state: "completed",
        completionEvidence: {
          level: "completed_result",
          status: "verified_completed_result",
          evidenceKind: "document_summary",
          resultVerified: true,
          resultArtifacts: [],
          missing: [],
          signoff: true,
          summary: "摘要已核验"
        }
      })
    ]);

    expect(model.taskPilot).toMatchObject({ status: "已完成", tone: "done", actionLabel: "查看结果" });
    expect(model.resultTimeline).toMatchObject({
      statusLabel: "完成结果已核验",
      tone: "ready",
      canTreatAsDone: true
    });
    expect(model.taskWorkspaceItems).toContainEqual(expect.objectContaining({ label: "结果状态", value: "完成结果已核验", tone: "ready" }));
    expect(model.outcomeCards.find((card) => card.id === "document")).toMatchObject({
      statusLabel: "完成结果已核验",
      tone: "ready"
    });

    vi.useRealTimers();
  });
});

describe("task display copy", () => {
  it("hides raw paths and reflects safe failures", () => {
    const unsafeTask = task({
      title: String.raw`scan C:\Users\Suli\secret.txt`,
      state: "completed",
      completionEvidence: {
        level: "safe_failure",
        status: "safe_failure",
        evidenceKind: "safe_failure",
        resultVerified: false,
        resultArtifacts: [],
        missing: ["result"],
        signoff: false,
        summary: "任务安全停止"
      }
    });

    expect(taskDisplayTitle(unsafeTask, "最近任务")).toBe("最近任务");
    expect(taskDisplayState(unsafeTask)).toBe("安全停止");
  });
});
