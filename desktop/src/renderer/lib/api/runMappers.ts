import type { PerceptionSuggestionLaunchResponse } from "../../../shared/catalogTypes";
import type { AgentConversation, Plan, TaskBoundaryEvent, TaskEvent } from "../../../shared/executionTypes";
import type { BackendPlan, BackendRunCreateResponse, BackendRunEvent, BackendRunState, BackendRunTimeline, BackendSuggestionLaunchResponse } from "./executionBackendTypes";
import { cleanupPlanFromApprovalPayload } from "./cleanupMappers";
import { mapOptionalTaskCompletionEvidence } from "./completionEvidenceMappers";
import { mapOptionalTaskResultQuality } from "./resultQualityMappers";
import { zhAgentName, zhBackendTaskStatus, zhBackendText, zhToolName } from "../zh";

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function arrayOfObjects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

function recordOrUndefined(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function emptyPlan(): Plan {
  return {
    id: "empty",
    title: "暂无活动计划",
    objective: "提交一个任务后会在这里生成计划。",
    updatedAt: new Date().toISOString(),
    steps: []
  };
}

export function mapTaskState(status: string): TaskEvent["state"] {
  if (status === "completed") return "completed";
  if (status === "failed" || status === "denied" || status === "cancelled") return "failed";
  if (status === "paused") return "paused";
  if (status === "waiting_user_approval" || status === "awaiting_approval") return "blocked";
  return "running";
}

export function mapRunCreateResponse(data: BackendRunCreateResponse | BackendSuggestionLaunchResponse, fallbackTitle: string): PerceptionSuggestionLaunchResponse {
  const run = "run" in data ? data.run : undefined;
  const runId = data.run_id ?? run?.run_id ?? crypto.randomUUID();
  const phase = data.phase ?? run?.phase ?? "running";
  const engine = data.engine ?? run?.engine ?? "auto";
  const backendMessage = "message" in data ? data.message : undefined;
  const title = backendMessage ?? run?.message ?? fallbackTitle;
  return {
    runId,
    engine,
    message: {
      id: `${runId}-suggestion-started`,
      role: "assistant",
      author: "Lengrvis",
      content: `已开始处理建议任务，当前状态：${zhBackendTaskStatus(phase)}。`,
      createdAt: new Date().toISOString(),
      status: "sent"
    },
    taskUpdates: [
      {
        id: runId,
        runId,
        title,
        description: `状态：${zhBackendTaskStatus(phase)}`,
        state: mapTaskState(phase),
        agent: runEngineAgentName(engine, data.engine_capabilities ?? run?.engine_capabilities),
        createdAt: run?.created_at ?? new Date().toISOString(),
        updatedAt: run?.updated_at ?? run?.created_at ?? new Date().toISOString()
      }
    ]
  };
}

export function mapRunTaskEvent(run: BackendRunState): TaskEvent {
  const cleanupPlan = cleanupPlanFromApprovalPayload(run.cleanup_plan ?? run.cleanupPlan ?? run.diff_preview);
  const completionEvidence = mapOptionalTaskCompletionEvidence(run.completion_evidence, {
    resultVerified: run.result_verified,
    completedResult: run.completed_result
  });
  return {
    id: run.run_id,
    runId: run.run_id,
    sourceTaskId: run.task_id ?? undefined,
    title: run.message || run.run_id,
    description: runDescription(run),
    state: mapTaskState(run.phase),
    agent: runEngineAgentName(run.engine, run.engine_capabilities),
    createdAt: run.created_at || new Date().toISOString(),
    updatedAt: run.updated_at || run.created_at || new Date().toISOString(),
    recordings: [],
    cleanupPlan,
    completionEvidence,
    resultQuality: mapOptionalTaskResultQuality(run.result_quality, completionEvidence)
  };
}

export function mapBoundaryEvents(value: unknown): TaskBoundaryEvent[] {
  return arrayOfObjects(value).map((event) => ({
    id: String(event.id ?? crypto.randomUUID()),
    kind: String(event.kind ?? "boundary"),
    title: zhBackendText(String(event.title ?? "工程边界")),
    detail: zhBackendText(String(event.detail ?? "")),
    severity: String(event.severity ?? "info"),
    stepId: optionalString(event.step_id ?? event.stepId),
    createdAt: String(event.created_at ?? event.createdAt ?? new Date().toISOString()),
    payload: recordOrUndefined(event.payload)
  }));
}

export function zhRunEngine(engine?: string): string {
  if (engine === "developer") return "开发执行";
  if (engine === "os") return "电脑执行";
  if (engine === "auto") return "自动选择";
  return engine || "未知执行";
}

export function runEngineAgentName(
  engine?: string,
  capabilities?: { writes_enabled?: boolean; mode?: string; supervisor_agent_hint?: string }
): string {
  const hint = capabilities?.supervisor_agent_hint?.trim();
  if (hint) {
    return zhAgentName(hint);
  }
  if (engine === "developer") {
    return capabilities?.writes_enabled ? "开发执行引擎" : "开发引擎（只读）";
  }
  if (engine === "os") return "电脑执行引擎";
  return "执行引擎";
}

function runDescription(run: BackendRunState): string {
  const status = zhBackendText(run.error) || `状态：${zhBackendTaskStatus(run.phase)}（${zhRunEngine(run.engine)}）`;
  const disclosure = run.engine_capabilities?.disclosure;
  if (disclosure && run.engine === "developer" && run.engine_capabilities?.writes_enabled === false) {
    return `${status} · ${zhBackendText(disclosure)}`;
  }
  return status;
}

export function latestRunState(runs: BackendRunState[]): BackendRunState | null {
  return [...runs].sort((left, right) => {
    const leftTime = Date.parse(left.updated_at || left.created_at || "");
    const rightTime = Date.parse(right.updated_at || right.created_at || "");
    return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
  })[0] ?? null;
}

export function hasRunTimelineEvents(timeline: BackendRunTimeline): boolean {
  return Boolean(timeline.events?.length);
}

export function mapRunPlan(run: BackendRunState, timeline: BackendRunTimeline): Plan {
  const planEvent = [...(timeline.events ?? [])].reverse().find((event) => event.name === "plan.generated");
  const planPayload = (planEvent?.payload?.plan ?? planEvent?.payload?.structured_payload) as BackendPlan | undefined;
  if (!planPayload?.steps?.length) {
    return {
      ...emptyPlan(),
      id: run.run_id,
      title: run.message || run.run_id,
      objective: zhBackendText(run.error) || `状态：${zhBackendTaskStatus(run.phase)}`,
      updatedAt: run.updated_at
    };
  }
  return {
    id: planPayload.id || run.run_id,
    title: planPayload.goal || run.message || run.run_id,
    objective: planPayload.assumptions?.join(" ") || run.message,
    updatedAt: run.updated_at,
    steps: planPayload.steps.map((step) => ({
      id: step.id,
      title: zhToolName(step.tool_name),
      detail: zhBackendText(step.description),
      state: step.status === "succeeded" ? "done" : step.status === "waiting_user_approval" ? "blocked" : "pending",
      owner: step.agent_name,
      toolName: step.tool_name,
      riskLevel: step.risk_level,
      effects: step.tool_effects ?? [],
      resourceKinds: step.resource_kinds ?? [],
      trustTier: step.trust_tier,
      approvalState: step.requires_approval ? "required" : "not_required",
      deferredTool: Boolean(step.deferred_tool)
    }))
  };
}

export function mapRunConversation(run: BackendRunState, events: BackendRunEvent[]): AgentConversation {
  return {
    id: `${run.run_id}-events`,
    title: run.message || run.run_id,
    status: run.phase === "completed" ? "done" : run.phase === "awaiting_approval" ? "waiting" : "running",
    messages: events.map((event) => {
      const payload = event.payload ?? {};
      const agent = String(payload.from_agent ?? runEngineAgentName(run.engine));
      const content = String(payload.content ?? payload.transition_reason ?? event.name);
      return {
        id: event.id,
        role: "assistant" as const,
        name: agent,
        agent,
        content: zhBackendText(content),
        createdAt: event.created_at,
        metadata: { ...payload, event_type: event.name },
        kind: mapRunEventKind(event.name)
      };
    })
  };
}

export function mapRunEventKind(name: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (name === "tool.result" || name === "run.completed") return "result";
  if (name === "approval.needed" || name === "run.waiting_approval") return "handoff";
  if (name === "tool.progress") return "observation";
  return "action";
}
