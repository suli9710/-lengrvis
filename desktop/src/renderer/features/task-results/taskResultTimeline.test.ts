import { describe, expect, it } from "vitest";

import type { TaskEvent, TaskResultQuality } from "../../../shared/executionTypes";
import { buildTaskResultTimelineSummary } from "./taskResultTimeline";

function task(overrides: Partial<TaskEvent> = {}): TaskEvent {
  return {
    id: "task-1",
    title: "检查电脑状态",
    description: "只读检查系统状态",
    state: "running",
    agent: "system",
    createdAt: "2026-07-03T01:00:00.000Z",
    updatedAt: "2026-07-03T02:00:00.000Z",
    ...overrides
  };
}

function quality(overrides: Partial<TaskResultQuality> = {}): TaskResultQuality {
  return {
    state: "visible_progress",
    label: "有进度待核验",
    summary: "任务已经记录了可见进度。",
    resultVerified: false,
    canTreatAsDone: false,
    needsReview: true,
    missingChecks: ["最终结果复核"],
    nextStep: "打开任务详情核对结果。",
    signoff: false,
    redacted: true,
    privacyNote: "只展示脱敏摘要。",
    ...overrides
  };
}

describe("buildTaskResultTimelineSummary", () => {
  it("returns a draft-first empty state before the first task exists", () => {
    const summary = buildTaskResultTimelineSummary([], true);

    expect(summary).toMatchObject({
      task: null,
      title: "准备启动任务",
      statusLabel: "待发送",
      action: "compose",
      tone: "active",
      resultState: "none"
    });
    expect(summary.steps.map((step) => step.state)).toEqual(["current", "idle", "idle", "idle"]);
  });

  it("selects the latest verified result and marks it as done", () => {
    const summary = buildTaskResultTimelineSummary([
      task({ id: "old", state: "running", updatedAt: "2026-07-03T02:00:00.000Z" }),
      task({
        id: "verified",
        state: "completed",
        updatedAt: "2026-07-03T02:05:00.000Z",
        resultQuality: quality({
          state: "verified_result",
          summary: "系统状态检查已核验。",
          resultVerified: true,
          canTreatAsDone: true,
          needsReview: false,
          missingChecks: [],
          nextStep: "查看结果即可。"
        })
      })
    ]);

    expect(summary).toMatchObject({
      task: { id: "verified" },
      statusLabel: "完成结果已核验",
      tone: "ready",
      canTreatAsDone: true,
      actionLabel: "查看结果"
    });
    expect(summary.steps.at(-1)).toMatchObject({ id: "verify", state: "done" });
  });

  it("keeps visible progress actionable until verification is present", () => {
    const summary = buildTaskResultTimelineSummary([
      task({
        state: "completed",
        completionEvidence: {
          level: "completed_result",
          status: "visible_progress",
          evidenceKind: "tool_summary",
          resultVerified: false,
          resultArtifacts: [],
          missing: ["最终结果复核"],
          signoff: false,
          summary: "有工具进度，但还没有最终核验。"
        },
        resultQuality: quality()
      })
    ]);

    expect(summary).toMatchObject({
      statusLabel: "有进度，待核验",
      tone: "warning",
      resultState: "visible_progress",
      canTreatAsDone: false,
      missingChecks: ["最终结果复核"],
      nextStep: "打开任务详情核对结果。"
    });
    expect(summary.steps.at(-1)).toMatchObject({ id: "verify", state: "blocked" });
  });

  it("separates safe failures and approval stops from completed results", () => {
    const failed = buildTaskResultTimelineSummary([
      task({
        state: "completed",
        resultQuality: quality({
          state: "safe_failure",
          summary: "安全策略停止了任务。",
          missingChecks: ["可交付结果"],
          nextStep: "查看原因后重试。"
        })
      })
    ]);
    const blocked = buildTaskResultTimelineSummary([
      task({ id: "blocked", state: "blocked", resultQuality: quality({ state: "task_evidence_only" }) })
    ]);

    expect(failed).toMatchObject({ tone: "failed", actionLabel: "查看原因", canTreatAsDone: false });
    expect(failed.steps.find((step) => step.id === "verify")).toMatchObject({ state: "failed" });
    expect(blocked).toMatchObject({ action: "approve", actionLabel: "去确认", tone: "blocked" });
    expect(blocked.steps.find((step) => step.id === "scope")).toMatchObject({ state: "blocked" });
  });

  it("hides raw path and internal contract words from the summary title", () => {
    const summary = buildTaskResultTimelineSummary([
      task({
        title: String.raw`scan C:\Users\Suli\secret.txt with token=demo`,
        state: "completed",
        resultQuality: quality({ state: "task_evidence_only", missingChecks: [] })
      })
    ]);

    expect(summary.title).toBe("最近任务");
    const visibleSummary = [
      summary.title,
      summary.statusLabel,
      summary.detail,
      summary.actionLabel,
      summary.nextStep,
      summary.privacyNote,
      ...summary.missingChecks,
      ...summary.steps.flatMap((step) => [step.label, step.detail])
    ].join("\n");
    expect(visibleSummary).not.toMatch(/C:\\Users|token=|completion_evidence|result_verified|tool_result/i);
  });
});
