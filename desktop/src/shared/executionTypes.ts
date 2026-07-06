import type { ChatRole } from "./catalogTypes";
import type { CleanupPlan } from "./cleanupTypes";

export interface RunEventPayload {
  id: string;
  run_id: string;
  name: string;
  event?: string;
  event_type?: string;
  sequence: number;
  payload: Record<string, unknown>;
  created_at: string;
  replay?: boolean;
}

export type TaskState = "queued" | "running" | "blocked" | "paused" | "completed" | "failed";

export interface TaskBoundaryEvent {
  id: string;
  kind: string;
  title: string;
  detail: string;
  severity: "info" | "warning" | "danger" | "success" | string;
  stepId?: string;
  createdAt: string;
  payload?: Record<string, unknown>;
}

export interface TaskEvent {
  id: string;
  runId?: string;
  sourceTaskId?: string;
  title: string;
  description: string;
  state: TaskState;
  agent: string;
  createdAt: string;
  updatedAt: string;
  recordings?: TaskStepRecording[];
  cleanupPlan?: CleanupPlan;
  boundaryEvents?: TaskBoundaryEvent[];
  completionEvidence?: TaskCompletionEvidence;
  resultQuality?: TaskResultQuality;
}

export interface TaskArtifact {
  path: string;
  kind: "changed" | "output" | string;
  toolName: string;
  stepId: string;
  createdAt: string;
  exists: boolean;
  isDir: boolean;
  sizeBytes: number;
}

export interface TaskArtifactsSummary {
  taskId: string;
  artifacts: TaskArtifact[];
  counts: {
    total: number;
    existing: number;
    missing: number;
    changed: number;
    generated: number;
  };
}

export interface TaskStepRecordingFrame {
  phase: string;
  ok: boolean;
  capturedAt: string;
  url?: string;
  width?: number;
  height?: number;
  error?: string;
}

export interface TaskStepRecording {
  stepId: string;
  toolName: string;
  agent: string;
  frames: TaskStepRecordingFrame[];
}

export type TaskCompletionEvidenceLevel =
  | "submission"
  | "task_created"
  | "visible_progress"
  | "completed_result"
  | "safe_failure";

export type TaskCompletionEvidenceStatus =
  | "unverified"
  | "task_evidence_only"
  | "visible_progress"
  | "safe_failure"
  | "verified_completed_result";

export interface TaskCompletionArtifact {
  kind: string;
  label: string;
  redacted: boolean;
  count?: number;
}

export interface TaskCompletionEvidence {
  level: TaskCompletionEvidenceLevel;
  status: TaskCompletionEvidenceStatus;
  evidenceKind: string;
  resultVerified: boolean;
  resultArtifacts: TaskCompletionArtifact[];
  missing: string[];
  signoff: boolean;
  summary: string;
  privacyNote?: string;
}

export type TaskResultQualityState =
  | "verified_result"
  | "visible_progress"
  | "safe_failure"
  | "task_evidence_only";

export interface TaskResultQuality {
  state: TaskResultQualityState;
  label: string;
  summary: string;
  resultVerified: boolean;
  canTreatAsDone: boolean;
  needsReview: boolean;
  missingChecks: string[];
  nextStep: string;
  signoff: boolean;
  redacted: boolean;
  privacyNote?: string;
}

export interface TaskExplainEvidence {
  source: string;
  id: string;
  createdAt?: string;
  actor?: string;
  eventType?: string;
  stepId?: string;
  summary: string;
}

export interface TaskExplainReview {
  id: string;
  stepId?: string | null;
  targetType: string;
  verdict: string;
  riskLevel: string;
  reasons: string[];
  requiredChanges: string[];
  userConfirmationMessage: string;
  safeAlternative: string;
  createdAt: string;
  evidence: TaskExplainEvidence[];
}

export interface TaskExplainMessage {
  id: string;
  stepId?: string | null;
  fromAgent: string;
  toAgent?: string | null;
  messageType: string;
  content: string;
  createdAt: string;
  evidence: TaskExplainEvidence[];
  action?: {
    kind: string;
    toolName: string;
    rationale: string;
    followUpQuestion: string;
  };
}

export interface TaskExplainStep {
  id: string;
  stepId: string;
  order: number;
  agentName: string;
  toolName: string;
  description: string;
  status: string;
  riskLevel: string;
  requiresApproval: boolean;
  expectedObservation: string;
  rollbackStrategy: string;
  plannerReason: string;
  safetyReviews: TaskExplainReview[];
  subagentSuggestions: TaskExplainMessage[];
  observations: TaskExplainMessage[];
}

export interface TaskExplainChainItem {
  stage: string;
  title: string;
  summary: string;
  evidence: TaskExplainEvidence[];
}

export interface TaskExplain {
  taskId: string;
  userGoal: string;
  status: string;
  mode: string;
  generatedAt: string;
  complete: boolean;
  missingSections: string[];
  dataSources: Record<string, number>;
  userGoalRecord: {
    text: string;
    evidence: TaskExplainEvidence[];
  };
  supervisorJudgment: {
    summary: string;
    delegate: boolean;
    agentHint: string;
    inferred: boolean;
    evidence: TaskExplainEvidence[];
  };
  plannerReasoning: {
    summary: string;
    planId: string;
    goal: string;
    assumptions: string[];
    stepCount: number;
    globalRiskLevel: string;
    requiresUserApproval: boolean;
    evidence: TaskExplainEvidence[];
  };
  globalSafetyReviews: TaskExplainReview[];
  steps: TaskExplainStep[];
  subagentSuggestions: TaskExplainMessage[];
  completionEvidence: TaskCompletionEvidence;
  resultQuality: TaskResultQuality;
  finalResult: {
    status: string;
    summary: string;
    safetyReviews: TaskExplainReview[];
    evidence: TaskExplainEvidence[];
    completionEvidence: TaskCompletionEvidence;
    resultQuality: TaskResultQuality;
  };
  chain: TaskExplainChainItem[];
}

export type PlanStepState = "pending" | "active" | "done" | "blocked";
export type PermissionMode = "plan" | "default" | "trusted_edits" | "auto_review" | "dont_ask";

export interface PlanStep {
  id: string;
  title: string;
  detail: string;
  state: PlanStepState;
  owner: string;
  toolName?: string;
  riskLevel?: string;
  effects?: string[];
  resourceKinds?: string[];
  trustTier?: string;
  approvalState?: string;
  deferredTool?: boolean;
}

export interface Plan {
  id: string;
  title: string;
  objective: string;
  updatedAt: string;
  steps: PlanStep[];
}

export interface AgentMessage {
  id: string;
  role: ChatRole;
  name?: string;
  content: string;
  createdAt: string;
  toolCalls?: OpenAIToolCall[];
  toolCallId?: string;
  metadata?: Record<string, unknown>;
  agent?: string;
  kind?: "handoff" | "observation" | "action" | "result";
}

export interface AgentConversation {
  id: string;
  title: string;
  status: "idle" | "running" | "waiting" | "done";
  messages: AgentMessage[];
}

export interface OpenAIToolCall {
  id: string;
  type: "function";
  function: {
    name: string;
    arguments: string;
  };
}

export type SafetySeverity = "low" | "medium" | "high" | "critical";

export interface SafetyFinding {
  id: string;
  severity: SafetySeverity;
  title: string;
  detail: string;
  status: "open" | "accepted" | "dismissed";
}

export interface SafetyReview {
  id: string;
  status: "clear" | "needs_review" | "blocked";
  updatedAt: string;
  findings: SafetyFinding[];
  boundaryEvents?: TaskBoundaryEvent[];
}

export interface ApprovalRequest {
  id: string;
  taskId?: string;
  stepId?: string | null;
  approvalType?: string;
  title: string;
  reason: string;
  requester: string;
  riskLevel: SafetySeverity;
  createdAt: string;
  proposedAction: string;
  status: "pending" | "approved" | "denied" | "expired" | "unavailable";
  rawPayload?: unknown;
  cleanupPlan?: CleanupPlan;
  toolName?: string;
  toolTrustTier?: string;
  toolEffects?: string[];
  resourceKinds?: string[];
  policyMode?: PermissionMode | string;
  dryRunSummary?: string;
  modelAction?: Record<string, unknown>;
  runtimeControlFields?: Record<string, unknown>;
  engineeringBoundary?: Record<string, unknown>;
}

export interface BackendApprovalPayload {
  id: string;
  task_id?: string | null;
  step_id?: string | null;
  approval_type: string;
  message: string;
  diff_preview: unknown;
  tool_name?: string;
  risk_level?: string;
  tool_trust_tier?: string;
  tool_effects?: string[];
  resource_kinds?: string[];
  policy_mode?: string;
  permission_mode?: string;
  dry_run_summary?: string;
  model_action?: unknown;
  runtime_control_fields?: unknown;
  runtime_fields?: unknown;
  engineering_boundary?: unknown;
  status: string;
  created_at: string;
}

export interface ApprovalDecision {
  approvalId: string;
  decision: "approved" | "denied";
  note?: string;
}

export interface CommandInfo {
  name: string;
  title: string;
  description: string;
  category: string;
  inputSchema: Record<string, unknown>;
}

export interface CommandExecutionResult {
  ok: boolean;
  command: string;
  title?: string;
  result?: unknown;
  diagnostics?: string[];
  error?: string;
  nextAction?: string;
}
