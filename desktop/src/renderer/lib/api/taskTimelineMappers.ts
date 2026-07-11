import type { CleanupPlan } from "../../../shared/cleanupTypes";
import type { AgentConversation, TaskEvent } from "../../../shared/executionTypes";
import { zhBackendTaskStatus, zhBackendText } from "../zh";
import type { BackendAgentMessage, BackendRollbackSummary, BackendStepRecordingFrame, BackendStepRecordingPayload, BackendTask, BackendTimeline } from "./executionBackendTypes";
import { cleanupPlanFromApprovalPayload } from "./cleanupMappers";
import { mapOptionalTaskCompletionEvidence } from "./completionEvidenceMappers";
import { mapOptionalTaskResultQuality } from "./resultQualityMappers";
import { mapBoundaryEvents, mapTaskState } from "./runMappers";
import { absoluteRendererLoopbackBackendUrl, getBackendBaseUrl } from "./transport";

export function mapTaskEvent(task: BackendTask): TaskEvent {
  const cleanupPlan = cleanupPlanFromApprovalPayload(task.cleanup_plan ?? task.cleanupPlan ?? task.diff_preview);
  const completionEvidence = mapOptionalTaskCompletionEvidence(task.completion_evidence, {
    resultVerified: task.result_verified,
    completedResult: task.completed_result
  });
  const rollback = mapRollbackSummary(task.metadata?.rollback);
  return {
    id: task.id,
    sourceTaskId: task.id,
    title: task.user_goal,
    description: zhBackendText(task.final_summary) || `当前后端状态：${zhBackendTaskStatus(task.status)}`,
    state: rollback ? (rollback.state === "succeeded" ? "rolled_back" : "repair_required") : mapTaskState(task.status),
    agent: "调度 Agent",
    createdAt: task.created_at,
    updatedAt: task.updated_at,
    recordings: [],
    cleanupPlan,
    boundaryEvents: mapBoundaryEvents(task.boundary_events),
    completionEvidence,
    resultQuality: mapOptionalTaskResultQuality(task.result_quality, completionEvidence),
    rollback
  };
}

function mapRollbackSummary(value?: BackendRollbackSummary): TaskEvent["rollback"] {
  if (!value || typeof value !== "object") return undefined;
  return {
    state: String(value.state ?? "failed"),
    attempted: Number(value.attempted ?? 0),
    succeeded: Number(value.succeeded ?? 0),
    verified: Number(value.verified ?? value.succeeded ?? 0),
    verificationFailed: Number(value.verification_failed ?? 0),
    failed: Number(value.failed ?? 0),
    manualRequired: Number(value.manual_required ?? 0),
    unrecoverable: Number(value.unrecoverable ?? 0)
  };
}

export function mapTaskRecordings(timeline: BackendTimeline): NonNullable<TaskEvent["recordings"]> {
  const byStep = new Map<string, NonNullable<TaskEvent["recordings"]>[number]>();
  const direct = Array.isArray(timeline.recordings) ? timeline.recordings : [];
  const fromMessages = timeline.messages
    .map((message) => metadataPayloadFor<BackendStepRecordingPayload>(message))
    .filter((payload): payload is BackendStepRecordingPayload => payload?.kind === "step_screenshot");

  for (const item of direct) {
    mergeRecording(
      byStep,
      String(item.step_id ?? ""),
      String(item.tool_name ?? ""),
      String(item.agent ?? ""),
      Array.isArray(item.frames) ? item.frames : []
    );
  }
  for (const payload of fromMessages) {
    mergeRecording(
      byStep,
      String(payload.step_id ?? ""),
      String(payload.tool_name ?? ""),
      String(payload.agent ?? ""),
      Array.isArray(payload.frames) ? payload.frames : []
    );
  }

  return Array.from(byStep.values()).map((recording) => ({
    ...recording,
    frames: dedupeFrames(recording.frames).sort((a, b) => Date.parse(a.capturedAt) - Date.parse(b.capturedAt))
  }));
}

export function cleanupPlanFromTimeline(timeline: BackendTimeline): CleanupPlan | undefined {
  const direct = cleanupPlanFromApprovalPayload(timeline.cleanup_plan ?? timeline.cleanupPlan);
  if (direct) return direct;

  for (const message of timeline.messages) {
    const payload = metadataPayloadFor<unknown>(message);
    const plan = cleanupPlanFromApprovalPayload(payload);
    if (plan) return plan;
  }
  return undefined;
}

export function mergeRecording(
  target: Map<string, NonNullable<TaskEvent["recordings"]>[number]>,
  stepId: string,
  toolName: string,
  agent: string,
  frames: BackendStepRecordingFrame[]
) {
  if (!stepId || !frames.length) return;
  const current = target.get(stepId) ?? { stepId, toolName, agent, frames: [] };
  current.toolName = current.toolName || toolName;
  current.agent = current.agent || agent;
  current.frames.push(...frames.map(mapRecordingFrame));
  target.set(stepId, current);
}

export function mapRecordingFrame(frame: BackendStepRecordingFrame): NonNullable<TaskEvent["recordings"]>[number]["frames"][number] {
  const url = typeof frame.url === "string" && frame.url ? absoluteBackendUrl(frame.url) : undefined;
  return {
    phase: String(frame.phase ?? ""),
    ok: frame.ok !== false,
    capturedAt: String(frame.captured_at ?? ""),
    url,
    width: Number(frame.width ?? 0) || undefined,
    height: Number(frame.height ?? 0) || undefined,
    error: typeof frame.error === "string" ? frame.error : undefined
  };
}

export function dedupeFrames<TFrame extends { phase: string; capturedAt: string; url?: string }>(frames: TFrame[]): TFrame[] {
  const seen = new Set<string>();
  const result: TFrame[] = [];
  for (const frame of frames) {
    const key = `${frame.phase}|${frame.capturedAt}|${frame.url ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(frame);
  }
  return result;
}

export function absoluteBackendUrl(path: string): string | undefined {
  return absoluteRendererLoopbackBackendUrl(path, getBackendBaseUrl()) || undefined;
}

export function mapAgentKind(kind?: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (kind === "observation") return "observation";
  if (kind === "review" || kind === "critique") return "handoff";
  if (kind === "final") return "result";
  return "action";
}

export function agentNameFor(message?: BackendAgentMessage): string {
  return message?.name ?? message?.metadata?.from_agent ?? message?.from_agent ?? "assistant";
}

export function metadataPayloadFor<TPayload>(message?: BackendAgentMessage): TPayload | undefined {
  const payload = message?.metadata?.structured_payload ?? message?.structured_payload;
  return payload as TPayload | undefined;
}
