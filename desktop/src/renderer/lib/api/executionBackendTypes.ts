import type { AgentConversation, RunEventPayload } from "../../../shared/executionTypes";

export interface BackendRunCreateRequest {
  message: string;
  mode: "privacy" | "efficiency" | "hybrid";
  engine: "auto" | "os" | "developer";
  agent_hint?: string;
}

export interface BackendEngineCapabilities {
  writes_enabled?: boolean;
  mode?: string;
  disclosure?: string;
  supervisor_agent_hint?: string;
  route_rule?: string;
}

export interface BackendRunCreateResponse {
  run_id: string;
  engine: "os" | "developer";
  phase: string;
  engine_route_rule?: string;
  engine_capabilities?: BackendEngineCapabilities;
}

export interface BackendSuggestionLaunchRequest {
  suggestion_id: string;
  prompt?: string;
  mode: string;
}

export interface BackendSuggestionLaunchResponse {
  run_id?: string;
  engine?: "auto" | "os" | "developer" | string;
  phase?: string;
  message?: string;
  engine_capabilities?: BackendEngineCapabilities;
  run?: BackendRunState;
}

export interface BackendRunState {
  run_id: string;
  engine: "os" | "developer" | string;
  phase: string;
  task_id?: string | null;
  message: string;
  mode: string;
  requested_engine: "auto" | "os" | "developer" | string;
  engine_route_rule?: string;
  error?: string;
  created_at: string;
  updated_at: string;
  engine_capabilities?: BackendEngineCapabilities;
  cleanup_plan?: unknown;
  cleanupPlan?: unknown;
  diff_preview?: unknown;
  completion_evidence?: unknown;
  result_quality?: unknown;
  result_verified?: unknown;
  completed_result?: unknown;
}

export interface BackendRunEvent extends RunEventPayload {
  name: string;
}

export interface BackendRunTimeline {
  run: BackendRunState;
  events: BackendRunEvent[];
  count: number;
}

export type BackendRunStreamEvent =
  | { type: "connected"; run_id: string; engine?: string; phase?: string }
  | { type: "replay.completed"; run_id: string; last_sequence: number }
  | { type: "heartbeat"; run_id: string }
  | BackendRealtimeStatusEvent
  | (RunEventPayload & { type: "run_event"; event: string });

export interface BackendTaskArtifactItem {
  path: string;
  kind: string;
  tool_name: string;
  step_id: string;
  created_at: string;
  exists?: boolean;
  is_dir?: boolean;
  size_bytes?: number;
}

export interface BackendTaskArtifacts {
  task_id: string;
  artifacts?: BackendTaskArtifactItem[];
  counts?: {
    total?: number;
    existing?: number;
    missing?: number;
    changed?: number;
    generated?: number;
  };
}

export interface BackendTask {
  id: string;
  user_goal: string;
  status: string;
  mode: string;
  final_summary: string;
  created_at: string;
  updated_at: string;
  cleanup_plan?: unknown;
  cleanupPlan?: unknown;
  diff_preview?: unknown;
  boundary_events?: BackendBoundaryEvent[];
  completion_evidence?: unknown;
  result_quality?: unknown;
  result_verified?: unknown;
  completed_result?: unknown;
}

export interface BackendTimeline {
  task: string;
  messages: BackendAgentMessage[];
  reviews: BackendSafetyReview[];
  recordings?: BackendStepRecording[];
  cleanup_plan?: unknown;
  cleanupPlan?: unknown;
  boundary_events?: BackendBoundaryEvent[];
}

export interface BackendBoundaryEvent {
  id?: string;
  kind?: string;
  title?: string;
  detail?: string;
  severity?: string;
  step_id?: string;
  stepId?: string;
  created_at?: string;
  createdAt?: string;
  payload?: Record<string, unknown>;
}

export interface BackendStepRecording {
  step_id?: string;
  tool_name?: string;
  agent?: string;
  frames?: BackendStepRecordingFrame[];
}

export interface BackendStepRecordingPayload extends BackendStepRecording {
  kind?: string;
}

export interface BackendStepRecordingFrame {
  phase?: string;
  ok?: boolean;
  captured_at?: string;
  url?: string;
  width?: number;
  height?: number;
  error?: string;
}

export interface BackendTaskCompletionEvidenceFallback {
  resultVerified?: unknown;
  completedResult?: unknown;
  evidenceKind?: unknown;
}

export type BackendTaskStreamEvent =
  | {
      type: "connected" | "heartbeat" | "agent_message";
      task_id: string;
      message?: BackendAgentMessage;
    }
  | BackendRealtimeStatusEvent;

export interface BackendRealtimeStatusEvent {
  type: "stream_status";
  status: "open" | "reconnecting" | "closed" | "error" | "malformed";
  endpoint: string;
  message: string;
  raw?: string;
  code?: number;
  reason?: string;
}

export interface BackendAgentMessage {
  id: string;
  role?: "system" | "developer" | "user" | "assistant" | "tool";
  name?: string;
  from_agent?: string;
  message_type?: string;
  content: string;
  tool_calls?: AgentConversation["messages"][number]["toolCalls"];
  tool_call_id?: string;
  metadata?: {
    from_agent?: string;
    to_agent?: string;
    message_type?: string;
    structured_payload?: unknown;
    [key: string]: unknown;
  };
  structured_payload?: unknown;
  created_at: string;
}

export interface BackendPlan {
  id: string;
  goal: string;
  assumptions?: string[];
  steps: Array<{
    id: string;
    agent_name: string;
    tool_name: string;
    description: string;
    status: string;
    risk_level?: string;
    requires_approval?: boolean;
    tool_effects?: string[];
    resource_kinds?: string[];
    trust_tier?: string;
    deferred_tool?: boolean;
  }>;
}

export interface BackendSafetyReview {
  id: string;
  target_type: string;
  verdict: string;
  risk_level: string;
  reasons: string[];
  safe_alternative: string;
  created_at: string;
}

export interface BackendTaskExplainEvidence {
  source?: string;
  id?: string;
  created_at?: string;
  actor?: string;
  event_type?: string;
  step_id?: string;
  summary?: string;
}

export interface BackendTaskExplainReview {
  id?: string;
  step_id?: string | null;
  target_type?: string;
  verdict?: string;
  risk_level?: string;
  reasons?: string[];
  required_changes?: string[];
  user_confirmation_message?: string;
  safe_alternative?: string;
  created_at?: string;
  evidence?: BackendTaskExplainEvidence[];
}

export interface BackendTaskExplainMessage {
  id?: string;
  step_id?: string | null;
  from_agent?: string;
  to_agent?: string | null;
  message_type?: string;
  content?: string;
  created_at?: string;
  evidence?: BackendTaskExplainEvidence[];
  action?: {
    kind?: string;
    tool_name?: string;
    rationale?: string;
    follow_up_question?: string;
  };
}

export interface BackendTaskExplainStep {
  id?: string;
  step_id?: string;
  order?: number;
  agent_name?: string;
  tool_name?: string;
  description?: string;
  status?: string;
  risk_level?: string;
  requires_approval?: boolean;
  expected_observation?: string;
  rollback_strategy?: string;
  planner_reason?: string;
  safety_reviews?: BackendTaskExplainReview[];
  subagent_suggestions?: BackendTaskExplainMessage[];
  observations?: BackendTaskExplainMessage[];
}

export interface BackendTaskExplainChainItem {
  stage?: string;
  title?: string;
  summary?: string;
  evidence?: BackendTaskExplainEvidence[];
}

export interface BackendTaskExplain {
  task_id?: string;
  user_goal?: string;
  status?: string;
  mode?: string;
  generated_at?: string;
  complete?: boolean;
  missing_sections?: string[];
  data_sources?: Record<string, number>;
  user_goal_record?: {
    text?: string;
    evidence?: BackendTaskExplainEvidence[];
  };
  supervisor_judgment?: {
    summary?: string;
    delegate?: boolean;
    agent_hint?: string;
    inferred?: boolean;
    evidence?: BackendTaskExplainEvidence[];
  };
  planner_reasoning?: {
    summary?: string;
    plan_id?: string;
    goal?: string;
    assumptions?: string[];
    step_count?: number;
    global_risk_level?: string;
    requires_user_approval?: boolean;
    evidence?: BackendTaskExplainEvidence[];
  };
  global_safety_reviews?: BackendTaskExplainReview[];
  steps?: BackendTaskExplainStep[];
  subagent_suggestions?: BackendTaskExplainMessage[];
  completion_evidence?: unknown;
  result_quality?: unknown;
  result_verified?: unknown;
  completed_result?: unknown;
  evidence_kind?: string;
  evidence_summary?: string;
  final_result?: {
    status?: string;
    summary?: string;
    safety_reviews?: BackendTaskExplainReview[];
    evidence?: BackendTaskExplainEvidence[];
    completion_evidence?: unknown;
    result_quality?: unknown;
    result_verified?: unknown;
    completed_result?: unknown;
    evidence_kind?: string;
    evidence_summary?: string;
  };
  chain?: BackendTaskExplainChainItem[];
}

export interface BackendApproval {
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

export interface BackendCommandInfo {
  name?: string;
  title?: string;
  description?: string;
  category?: string;
  input_schema?: unknown;
}

export interface BackendCommandsResponse {
  commands?: BackendCommandInfo[];
  count?: number;
}

export interface BackendCommandExecutionResult {
  ok?: boolean;
  command?: string;
  title?: string;
  result?: unknown;
  diagnostics?: unknown[];
  error?: string;
  next_action?: string;
}
