import type {
  TaskExplain,
  TaskExplainChainItem,
  TaskExplainEvidence,
  TaskExplainMessage,
  TaskExplainReview,
  TaskExplainStep
} from "../../../shared/executionTypes";
import type {
  BackendTaskExplain,
  BackendTaskExplainChainItem,
  BackendTaskExplainEvidence,
  BackendTaskExplainMessage,
  BackendTaskExplainReview,
  BackendTaskExplainStep
} from "./executionBackendTypes";
import { mapTaskCompletionEvidence } from "./completionEvidenceMappers";
import { zhBackendText } from "../zh";

export function mapTaskExplain(data: BackendTaskExplain): TaskExplain {
  const finalResult = data.final_result ?? {};
  const completionEvidence = mapTaskCompletionEvidence(finalResult.completion_evidence ?? data.completion_evidence, {
    resultVerified: finalResult.result_verified ?? data.result_verified,
    completedResult: finalResult.completed_result ?? data.completed_result,
    evidenceKind: finalResult.evidence_kind ?? data.evidence_kind
  });
  return {
    taskId: String(data.task_id ?? ""),
    userGoal: zhBackendText(String(data.user_goal ?? "")),
    status: String(data.status ?? ""),
    mode: String(data.mode ?? ""),
    generatedAt: String(data.generated_at ?? ""),
    complete: Boolean(data.complete),
    missingSections: (data.missing_sections ?? []).map(String),
    dataSources: Object.fromEntries(Object.entries(data.data_sources ?? {}).map(([key, value]) => [key, Number(value ?? 0)])),
    userGoalRecord: {
      text: zhBackendText(String(data.user_goal_record?.text ?? "")),
      evidence: (data.user_goal_record?.evidence ?? []).map(mapExplainEvidence)
    },
    supervisorJudgment: {
      summary: zhBackendText(String(data.supervisor_judgment?.summary ?? "")),
      delegate: Boolean(data.supervisor_judgment?.delegate),
      agentHint: String(data.supervisor_judgment?.agent_hint ?? ""),
      inferred: Boolean(data.supervisor_judgment?.inferred),
      evidence: (data.supervisor_judgment?.evidence ?? []).map(mapExplainEvidence)
    },
    plannerReasoning: {
      summary: zhBackendText(String(data.planner_reasoning?.summary ?? "")),
      planId: String(data.planner_reasoning?.plan_id ?? ""),
      goal: zhBackendText(String(data.planner_reasoning?.goal ?? "")),
      assumptions: (data.planner_reasoning?.assumptions ?? []).map((item) => zhBackendText(String(item))),
      stepCount: Number(data.planner_reasoning?.step_count ?? 0),
      globalRiskLevel: String(data.planner_reasoning?.global_risk_level ?? ""),
      requiresUserApproval: Boolean(data.planner_reasoning?.requires_user_approval),
      evidence: (data.planner_reasoning?.evidence ?? []).map(mapExplainEvidence)
    },
    globalSafetyReviews: (data.global_safety_reviews ?? []).map(mapExplainReview),
    steps: (data.steps ?? []).map(mapExplainStep),
    subagentSuggestions: (data.subagent_suggestions ?? []).map(mapExplainMessage),
    completionEvidence,
    finalResult: {
      status: String(finalResult.status ?? ""),
      summary: zhBackendText(String(finalResult.summary ?? "")),
      safetyReviews: (finalResult.safety_reviews ?? []).map(mapExplainReview),
      evidence: (finalResult.evidence ?? []).map(mapExplainEvidence),
      completionEvidence
    },
    chain: (data.chain ?? []).map(mapExplainChainItem)
  };
}

export function mapExplainStep(step: BackendTaskExplainStep): TaskExplainStep {
  return {
    id: String(step.id ?? step.step_id ?? ""),
    stepId: String(step.step_id ?? step.id ?? ""),
    order: Number(step.order ?? 0),
    agentName: String(step.agent_name ?? ""),
    toolName: String(step.tool_name ?? ""),
    description: zhBackendText(String(step.description ?? "")),
    status: String(step.status ?? ""),
    riskLevel: String(step.risk_level ?? ""),
    requiresApproval: Boolean(step.requires_approval),
    expectedObservation: zhBackendText(String(step.expected_observation ?? "")),
    rollbackStrategy: zhBackendText(String(step.rollback_strategy ?? "")),
    plannerReason: zhBackendText(String(step.planner_reason ?? "")),
    safetyReviews: (step.safety_reviews ?? []).map(mapExplainReview),
    subagentSuggestions: (step.subagent_suggestions ?? []).map(mapExplainMessage),
    observations: (step.observations ?? []).map(mapExplainMessage)
  };
}

export function mapExplainReview(review: BackendTaskExplainReview): TaskExplainReview {
  return {
    id: String(review.id ?? ""),
    stepId: review.step_id === undefined ? undefined : review.step_id,
    targetType: String(review.target_type ?? ""),
    verdict: String(review.verdict ?? ""),
    riskLevel: String(review.risk_level ?? ""),
    reasons: (review.reasons ?? []).map((item) => zhBackendText(String(item))),
    requiredChanges: (review.required_changes ?? []).map((item) => zhBackendText(String(item))),
    userConfirmationMessage: zhBackendText(String(review.user_confirmation_message ?? "")),
    safeAlternative: zhBackendText(String(review.safe_alternative ?? "")),
    createdAt: String(review.created_at ?? ""),
    evidence: (review.evidence ?? []).map(mapExplainEvidence)
  };
}

export function mapExplainMessage(message: BackendTaskExplainMessage): TaskExplainMessage {
  return {
    id: String(message.id ?? ""),
    stepId: message.step_id === undefined ? undefined : message.step_id,
    fromAgent: String(message.from_agent ?? ""),
    toAgent: message.to_agent === undefined ? undefined : message.to_agent,
    messageType: String(message.message_type ?? ""),
    content: zhBackendText(String(message.content ?? "")),
    createdAt: String(message.created_at ?? ""),
    evidence: (message.evidence ?? []).map(mapExplainEvidence),
    action: message.action
      ? {
          kind: String(message.action.kind ?? ""),
          toolName: String(message.action.tool_name ?? ""),
          rationale: zhBackendText(String(message.action.rationale ?? "")),
          followUpQuestion: zhBackendText(String(message.action.follow_up_question ?? ""))
        }
      : undefined
  };
}

export function mapExplainChainItem(item: BackendTaskExplainChainItem): TaskExplainChainItem {
  return {
    stage: String(item.stage ?? ""),
    title: String(item.title ?? ""),
    summary: zhBackendText(String(item.summary ?? "")),
    evidence: (item.evidence ?? []).map(mapExplainEvidence)
  };
}

export function mapExplainEvidence(item: BackendTaskExplainEvidence): TaskExplainEvidence {
  return {
    source: String(item.source ?? ""),
    id: String(item.id ?? ""),
    createdAt: item.created_at ? String(item.created_at) : undefined,
    actor: item.actor ? String(item.actor) : undefined,
    eventType: item.event_type ? String(item.event_type) : undefined,
    stepId: item.step_id ? String(item.step_id) : undefined,
    summary: zhBackendText(String(item.summary ?? ""))
  };
}
