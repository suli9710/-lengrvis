import type { BrowserHostSnapshot, BrowserSession } from "../../../shared/types";
import type { Plan, SafetyReview } from "../../../shared/executionTypes";

export function emptyBrowserHostSnapshot(hostAvailable: boolean): BrowserHostSnapshot {
  return {
    sessions: [],
    events: [],
    activeSessionId: null,
    visible: false,
    hostAvailable
  };
}

export function mergeBrowserSessionArrays(primary: BrowserSession[], secondary: BrowserSession[]): BrowserSession[] {
  const byId = new Map<string, BrowserSession>();
  for (const session of primary) byId.set(session.id, session);
  for (const session of secondary) byId.set(session.id, { ...byId.get(session.id), ...session });
  return [...byId.values()].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function emptyPlan(): Plan {
  return {
    id: "empty",
    title: "暂无活动计划",
    objective: "提交一个任务后会在这里生成计划。",
    updatedAt: new Date().toISOString(),
    steps: []
  };
}

export function emptySafetyReview(): SafetyReview {
  return {
    id: "empty",
    status: "clear",
    updatedAt: new Date().toISOString(),
    findings: []
  };
}
