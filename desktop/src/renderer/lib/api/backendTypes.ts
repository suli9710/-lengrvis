import type {
  AgentConversation,
  ApiMethod,
  ApiRequest,
  ApiQueryValue,
  ApiResponse,
  AppSettings,
  ApprovalDecision,
  ApprovalRequest,
  AuditLogEntry,
  BackendStatus,
  BrowserActivityEvent,
  BrowserAction,
  BrowserHostActionResult,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  BrowserLinkResult,
  BrowserPageSnapshot,
  BrowserSession,
  CommandExecutionResult,
  CommandInfo,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  CleanupExecutionResult,
  CleanupExecuteRequest,
  CleanupItem,
  CleanupPlan,
  CleanupPlanRequest,
  CleanupRollbackRequest,
  CleanupScanRequest,
  ContextUsage,
  DesktopWebSocketSubscribeRequest,
  DiagnosticExportResult,
  DocumentAskRequest,
  DocumentAskResponse,
  DocumentCitation,
  DocumentCompareRequest,
  DocumentCompareResponse,
  DocumentIR,
  DocumentParseRequest,
  DocumentTable,
  FileSearchResponse,
  FileSearchResult,
  FileRevealResult,
  HardwareAccelerationSmokePayload,
  HardwareAccelerationStatusPayload,
  IndexStatus,
  InstalledApp,
  InstalledSkill,
  IntentSuggestion,
  LocalLibraryItem,
  LocalLibraryResponse,
  LLMCostSummary,
  LLMHealthStatus,
  LLMProfile,
  LocalLLMHealth,
  LocalMetricsSummary,
  LocalModelReadiness,
  LocalModelSetupPlan,
  PerceptionSuggestionLaunchRequest,
  PerceptionSuggestionLaunchResponse,
  Plan,
  SafetyReview,
  SkillImportResult,
  SkillsCatalog,
  StartupItem,
  SystemDiagnostic,
  SystemInfo,
  SystemProcess,
  TaskArtifactsSummary,
  TaskCompletionEvidence,
  TaskEvent,
  TaskBoundaryEvent,
  RunEventPayload,
  TaskExplain,
  TaskExplainChainItem,
  TaskExplainEvidence,
  TaskExplainMessage,
  TaskExplainReview,
  TaskExplainStep
} from "../../../shared/types";
import type { DesktopMobilePairingCode } from "../../../shared/mobilePairingPayload";
import {
  API_REQUEST_DENIED_EXACT_PATHS,
  API_REQUEST_DENIED_METHOD_PATHS,
  API_REQUEST_DENIED_PATH_PREFIXES
} from "../../../shared/ipc";
import {
  zhApprovalType,
  zhBackendTaskStatus,
  zhBackendText,
  zhRiskLevel,
  zhSafetyVerdict,
  zhToolName,
  zhUserFacingError
} from "../zh";


export interface BackendScheduledTask {
  id: string;
  cron: string;
  goal: string;
  mode: string;
  enabled: boolean;
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;
  last_task_id?: string;
  note?: string;
  created_at?: string;
  updated_at?: string;
}

export interface BackendMemory {
  id: string;
  kind: string;
  content: string;
  tags: string[];
  task_id?: string;
  source?: string;
  use_count?: number;
  last_used_at?: string;
  created_at?: string;
}

export interface BackendChatRequest {
  message: string;
  mode: string;
}

export interface BackendChatMessage {
  id: string;
  role: "system" | "developer" | "user" | "assistant" | "tool";
  author: string;
  content: string;
  created_at?: string;
  createdAt?: string;
  status?: string;
}

export interface BackendChatResponse {
  task_id?: string | null;
  status?: string | null;
  message: string;
  delegated?: boolean;
  agent?: string;
}

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

export interface BackendIntentSuggestion {
  id: string;
  title: string;
  prompt: string;
  confidence?: number;
  agent_hint?: string;
  reason?: string;
}

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

export interface BackendLocalMetrics {
  window_days?: number;
  generated_at?: string;
  tasks?: {
    total?: number;
    terminal?: number;
    succeeded?: number;
    success_rate?: number | null;
    by_status?: Record<string, number>;
  };
  runs?: {
    total?: number;
    by_phase?: Record<string, number>;
  };
  recovery?: {
    reflections_started?: number;
    runs_with_reflection?: number;
    recovery_trigger_rate?: number | null;
    decided_actions?: Record<string, number>;
    ask_user_share?: number | null;
  };
  llm?: {
    calls?: number;
    anomalies?: number;
    anomaly_rate?: number | null;
    estimated_calls?: number;
    by_finish_reason?: Record<string, number>;
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

export interface BackendDocumentParseRequest {
  path: string;
  include_text?: boolean;
}

export interface BackendDocumentAskRequest {
  path: string;
  question: string;
  top_k?: number;
}

export interface BackendDocumentCompareRequest {
  paths: string[];
  focus?: string;
}

export interface BackendDocumentBlock {
  id?: string;
  block_id?: string;
  type?: string;
  kind?: string;
  text?: string;
  content?: string;
  level?: number | string;
  page?: number | string;
  order?: number | string;
  index?: number | string;
  columns?: unknown;
  rows?: unknown;
  metadata?: unknown;
}

export interface BackendDocumentTable {
  id?: string;
  table_id?: string;
  title?: string;
  name?: string;
  columns?: unknown;
  rows?: unknown;
  page?: number | string;
  source_block_id?: string;
  sourceBlockId?: string;
}

export interface BackendDocumentCitation {
  id?: string;
  label?: string;
  text?: string;
  snippet?: string;
  content?: string;
  path?: string;
  block_id?: string;
  blockId?: string;
  page?: number | string;
  score?: number | string;
}

export interface BackendDocumentIR {
  id?: string;
  document_id?: string;
  path?: string;
  title?: string;
  name?: string;
  mime_type?: string;
  mimeType?: string;
  language?: string;
  summary?: string;
  text?: string;
  truncated?: boolean;
  blocks?: BackendDocumentBlock[];
  tables?: BackendDocumentTable[];
  citations?: BackendDocumentCitation[];
  metadata?: unknown;
  created_at?: string;
  createdAt?: string;
}

export interface BackendDocumentAskResponse {
  answer?: string;
  summary?: string;
  citations?: unknown;
  citation_items?: BackendDocumentCitation[];
  citations_detail?: BackendDocumentCitation[];
  source_chunks?: BackendDocumentCitation[];
  sources?: BackendDocumentCitation[];
  note?: string;
}

export interface BackendDocumentCompareDifference {
  id?: string;
  title?: string;
  field?: string;
  detail?: string;
  summary?: string;
  text?: string;
  severity?: string;
  citations?: BackendDocumentCitation[];
}

export interface BackendDocumentCompareResponse {
  summary?: string;
  documents?: BackendDocumentIR[];
  differences?: BackendDocumentCompareDifference[];
  items?: BackendDocumentCompareDifference[];
  tables?: BackendDocumentTable[];
  note?: string;
}

export interface BackendCleanupScanRequest {
  roots?: string[];
  threshold_mb?: number;
  include_caches?: boolean;
}

export interface BackendCleanupPlanRequest extends BackendCleanupScanRequest {
  item_ids?: string[];
  prefer_trash?: boolean;
}

export interface BackendCleanupExecuteRequest {
  roots?: string[];
  plan_id?: string;
  content_hash?: string;
  selected_item_ids?: string[];
  dry_run?: boolean;
  approved?: boolean;
  approval_id?: string;
}

export interface BackendCleanupRollbackRequest {
  plan_id?: string;
  execution_id?: string;
}

export interface BackendCleanupItem {
  id?: string;
  path?: string;
  name?: string;
  action?: string;
  disposition?: string;
  mode?: string;
  delete_mode?: string;
  bucket?: string;
  size_bytes?: number;
  sizeBytes?: number;
  bytes?: number;
  size_mb?: number;
  sizeMb?: number;
  category?: string;
  detail?: string;
  description?: string;
  reason?: string;
  risk_level?: string;
  riskLevel?: string;
  can_rollback?: boolean;
  canRollback?: boolean;
  selected?: boolean;
  modified_at?: string;
  modifiedAt?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface BackendCleanupPlan {
  id?: string;
  plan_id?: string;
  content_hash?: string;
  contentHash?: string;
  title?: string;
  summary?: string;
  detail?: string;
  status?: string;
  created_at?: string;
  createdAt?: string;
  updated_at?: string;
  updatedAt?: string;
  total_bytes?: number;
  totalBytes?: number;
  reclaimable_bytes?: number;
  reclaimableBytes?: number;
  freed_bytes?: number;
  freedBytes?: number;
  permanent_delete_bytes?: number;
  permanentDeleteBytes?: number;
  trash_bytes?: number;
  trashBytes?: number;
  risk_warnings?: unknown;
  riskWarnings?: unknown;
  warnings?: unknown;
  items?: BackendCleanupItem[];
  buckets?: Record<string, unknown>;
  cleanup_plan?: unknown;
  plan?: unknown;
}

export interface BackendCleanupExecutionResult {
  ok?: boolean;
  plan_id?: string;
  planId?: string;
  execution_id?: string;
  executionId?: string;
  freed_bytes?: number;
  freedBytes?: number;
  executed?: unknown;
  rolled_back?: unknown;
  rolledBack?: unknown;
  errors?: unknown;
}

export interface MobilePairingCode extends DesktopMobilePairingCode {}

export interface MobileDevice {
  device_id: string;
  device_name: string;
  status?: string;
  created_at: string;
  updated_at: string;
  revoked_at?: string;
  remote_input_grants?: RemoteInputGrant[];
}

export interface MobileDeviceList {
  devices: MobileDevice[];
}

export interface RemoteInputGrant {
  id: string;
  status?: string;
  scope?: "remote:input" | string;
  created_at?: string;
  expires_at?: string;
  revoked_at?: string;
}

export interface RemoteInputGrantIssueResult {
  grant_id: string;
  device_id: string;
  expires_at: string;
  expires_in: number;
  device?: MobileDevice;
}

export interface BackendIndexStatus {
  status?: string;
  files_indexed?: number | string;
  chunks_indexed?: number | string;
  embeddings_indexed?: number | string;
  bytes_indexed?: number | string;
  last_indexed_at?: string;
  last_modified_at?: string;
  retry_hint?: string;
  latest_failure?: {
    at?: string;
    path_label?: string;
    path?: string;
    message?: string;
  } | null;
}

export interface BackendFileSearchResponse {
  index_results?: Array<{ file_id?: string; path: string; snippet?: string }>;
  name_results?: Array<{ path: string; name?: string }>;
  index_status?: BackendIndexStatus;
  name_search?: {
    count?: number | string;
    scanned?: number | string;
    truncated?: boolean;
    status?: string;
  };
}

export interface BackendLocalLibraryItem {
  id: string;
  path: string;
  path_label?: string;
  name: string;
  parent: string;
  parent_label?: string;
  kind: string;
  extension: string;
  mime_type?: string;
  size?: number;
  created_at?: number;
  modified_at?: number;
  preview_url?: string;
  group_label?: string;
  icon_url?: string;
  width?: number;
  height?: number;
}

export interface BackendLocalLibraryResponse {
  section: string;
  roots?: string[];
  scope_summary?: {
    root_count?: number | string;
    root_labels?: string[];
    has_authorized_roots?: boolean;
    display_label?: string;
    raw_paths_available_for_local_actions?: boolean;
    shareable_summary_has_raw_paths?: boolean;
  };
  items?: BackendLocalLibraryItem[];
  count?: number;
  total?: number;
  scanned?: number;
  truncated?: boolean;
  stats?: {
    size?: number;
    by_extension?: Record<string, number>;
  };
  index_status?: BackendIndexStatus;
}

export interface BackendClusterEntry {
  cluster_id: number | string;
  size: number;
  preview: string[];
  suggested_name?: string;
  group_by?: string;
  group_value?: string;
}

export interface BackendClusterResponse {
  ok: boolean;
  clusters: BackendClusterEntry[];
  count?: number;
  total?: number;
  method?: string;
  group_by?: string;
  cluster_by?: string;
  error?: string;
}

export interface FileClusterOptions {
  k?: number;
  groupBy?: string;
  group_by?: string;
  clusterBy?: string;
  cluster_by?: string;
  paths?: string[];
  imagePaths?: string[];
  image_paths?: string[];
  images?: string[];
  limit?: number;
  metadataWeight?: number;
  metadata_weight?: number;
}

export interface BackendClusterRequest {
  k?: number;
  group_by?: string;
  cluster_by?: string;
  paths?: string[];
  image_paths?: string[];
  images?: string[];
  limit?: number;
  metadata_weight?: number;
}

export interface BackendSettings {
  provider_name?: string;
  base_url?: string;
  model?: string;
  review_model?: string;
  wire_api?: string;
  requires_openai_auth?: boolean;
  model_reasoning_effort?: string;
  disable_response_storage?: boolean;
  temperature?: number;
  max_tokens?: number;
  timeout?: number;
  llm_api_max_retries?: number;
  llm_api_retry_backoff_seconds?: number;
  llm_api_circuit_failure_threshold?: number;
  llm_api_circuit_cooldown_seconds?: number;
  model_context_window?: number;
  model_auto_compact_token_limit?: number;
  allowed_directories?: string[];
  allow_browser_network?: boolean;
  remote_desktop_enabled?: boolean;
  app_allowlist?: string[];
  browser_max_page_bytes?: number;
  browser_screenshot_dir?: string;
  onnx_model_path?: string;
  onnx_execution_provider?: string;
  onnx_provider_preference?: string;
  onnx_directml_device_id?: string;
  onnx_openvino_device?: string;
  onnx_openvino_cache_dir?: string;
  onnx_warm_on_startup?: boolean;
  onnx_model_family?: string;
  embedding_backend?: string;
  onnx_embedding_model_path?: string;
  onnx_embedding_execution_provider?: string;
  onnx_embedding_model_id?: string;
  onnx_embedding_max_batch_size?: number;
  image_embedding_backend?: string;
  onnx_image_embedding_model_path?: string;
  onnx_image_embedding_execution_provider?: string;
  onnx_image_embedding_model_id?: string;
  onnx_image_embedding_max_batch_size?: number;
  ocr_backend?: string;
  ocr_execution_provider?: string;
  ocr_openvino_model_dir?: string;
  ocr_openvino_device?: string;
  ocr_lang?: string;
  ocr_min_confidence?: number;
  ocr_batch_size?: number;
  mode?: string;
  permission_mode?: string;
  allow_cloud_context?: boolean;
  allow_file_content_upload?: boolean;
  confirmation_nonce?: string;
  mcp_servers?: Array<{
    id?: string;
    name?: string;
    url?: string;
    command?: string;
    args?: string[];
    enabled?: boolean;
    transport?: string;
    auth?: Record<string, unknown>;
    [key: string]: unknown;
  }>;
}

export interface BackendCommercePlanStatus {
  plan: "free" | "pro" | "max" | "team";
  remote_desktop_enabled: boolean;
  features: Record<string, boolean>;
  high_risk_features: string[];
}

export interface BackendCommerceLicenseStatus {
  state:
    | "absent"
    | "active"
    | "expired"
    | "revoked"
    | "device_mismatch"
    | "device_unverified"
    | "subscription_inactive"
    | "invalid"
    | "revocation_data_invalid"
    | "verifier_unconfigured";
  present: boolean;
  active: boolean;
  expired: boolean;
  revoked?: boolean;
  verifier_configured: boolean;
  managed_by?: "environment" | "file" | null;
  requested_env_plan?: "free" | "pro" | "max" | "team";
  plan_env_ignored?: boolean;
  license_id?: string | null;
  issuer?: string | null;
  replaces?: string | null;
  revocation_capable?: boolean;
  revocation_source?: "environment" | "file" | null;
  revocation_generated_at?: string | null;
  plan?: "free" | "pro" | "max" | "team";
  subject?: string;
  seats?: number;
  subscription_id?: string | null;
  subscription_status?: "active" | "trialing" | "past_due" | "canceled" | "expired" | "revoked" | null;
  renews_at?: string | null;
  cancel_at_period_end?: boolean;
  device_id?: string | null;
  order_ref?: string | null;
  issued_at?: string | null;
  expires_at?: string | null;
  error_code?: string;
}

export interface BackendCommerceQuotaStatus {
  plan: "free" | "pro" | "max" | "team";
  enforced: boolean;
  unlimited: boolean;
  window_hours: number;
  limits: {
    total_tokens: number | null;
    calls: number | null;
    total_cost_usd: number | null;
  };
  usage?: {
    calls: number;
    total_tokens: number;
    total_cost_usd: number;
    window_hours: number;
    last_event_at?: string;
  } | null;
  exceeded: string[];
  windows?: Array<{
    key?: string;
    window_hours: number;
    limits: {
      total_tokens: number | null;
      calls: number | null;
      total_cost_usd: number | null;
    };
    usage?: {
      calls: number;
      total_tokens: number;
      total_cost_usd: number;
      window_hours: number;
      last_event_at?: string;
    } | null;
    exceeded?: string[];
  }>;
}

export interface SensitiveChangeConfirmation {
  required?: boolean;
  nonce?: string;
  expires_at?: string;
  changes?: Array<Record<string, unknown>>;
}

export interface BackendPermissionPolicy {
  rules?: BackendPermissionRule[];
  updated_at?: string;
}

export interface BackendPermissionRule {
  id?: string;
  name?: string;
  effect?: "allow" | "deny";
  tools?: string[];
  path_patterns?: string[];
  time_windows?: Array<{
    days?: number[];
    start?: string;
    end?: string;
    timezone?: string;
  }>;
  reason?: string;
  enabled?: boolean;
}

export interface BackendLlmCapabilities {
  tools?: boolean;
  structured_json?: boolean;
  vision?: boolean;
  embeddings?: boolean;
  prompt_cache?: boolean;
  responses_api?: boolean;
  reasoning_effort?: boolean;
  usage_breakdown?: boolean;
  local?: boolean;
  cloud?: boolean;
}

export interface BackendLlmProfile {
  provider_name?: string;
  model?: string;
  base_url?: string;
  wire_api?: string;
  location?: string;
  active_backend?: string;
  capabilities?: BackendLlmCapabilities;
  model_profile?: {
    model?: string;
    context_window?: number;
    max_output_tokens?: number;
    known?: boolean;
    family?: string;
  };
}

export interface BackendLlmProfileResponse {
  mode?: string;
  task?: string;
  profile?: BackendLlmProfile;
  degraded?: boolean;
  error?: string;
}

export interface BackendLlmHealth {
  active?: {
    available?: boolean;
    degraded?: boolean;
    provider?: string;
    model?: string;
    profile?: BackendLlmProfile;
    error?: string;
  };
  retry?: {
    max_retries?: number;
    backoff_seconds?: number;
    circuit_failure_threshold?: number;
    circuit_cooldown_seconds?: number;
    circuit?: {
      state?: string;
      failures?: number;
      retry_after_seconds?: number;
    };
  };
}

export interface BackendLlmCostSummary {
  window_hours?: number;
  calls?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  total_cost_usd?: number | null;
  estimated?: boolean;
  last_event_at?: string;
  by_model?: Array<{
    provider?: string;
    model?: string;
    calls?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    total_cost_usd?: number;
    estimated?: boolean;
  }>;
}

export interface BackendContextUsageWarning {
  token_count?: number;
  threshold?: number;
  percent_left?: number;
  is_above_warning_threshold?: boolean;
  is_above_error_threshold?: boolean;
  is_above_auto_compact_threshold?: boolean;
  is_at_blocking_limit?: boolean;
}

export interface BackendContextProjectionSummary {
  enabled?: boolean;
  strategy?: string;
  compacted?: boolean;
  original_tokens?: number;
  projected_tokens?: number;
  tokens_saved?: number;
  messages_removed?: number;
  adjustments?: unknown[];
  description?: string;
}

export interface BackendContextUsageProjection {
  enabled?: boolean;
  original_count?: number;
  projected_count?: number;
  original_tokens?: number;
  projected_tokens?: number;
  compacted?: boolean;
  micro_compacted?: boolean;
  history_snipped?: boolean;
  session_summary_added?: boolean;
  strategy?: string;
  source?: string;
  boundary_id?: string;
  retained_tail_message_ids?: string[];
  summary?: BackendContextProjectionSummary;
}

export interface BackendContextUsageHealth {
  status?: string;
  severity?: string;
  reason?: string;
  used_percent?: number;
  free_percent?: number;
  free_tokens?: number;
  projected_tokens?: number;
  projected_percent?: number;
  projected_free_tokens?: number;
  is_healthy?: boolean;
}

export interface BackendContextUsageLineage {
  task_id?: string;
  history_source?: string;
  message_count?: number;
  system_message_count?: number;
  agent_message_count?: number;
  message_roles?: Record<string, unknown>;
  local_tool_count?: number;
  mcp_tool_count?: number;
  session_memory_item_count?: number;
  include_registered_tools?: boolean;
  include_session_memory?: boolean;
  include_projection?: boolean;
  projection?: {
    source?: string;
    strategy?: string;
    boundary_id?: string;
    retained_tail_count?: number;
  };
}

export interface BackendContextUsage {
  total_tokens?: number;
  used_tokens?: number;
  free_tokens?: number;
  effective_context_window?: number;
  model_context_window?: number;
  auto_compact_threshold?: number;
  manual_compact_limit?: number;
  reserved_output_tokens?: number;
  warning?: BackendContextUsageWarning;
  projection?: BackendContextUsageProjection;
  health?: BackendContextUsageHealth;
  lineage?: BackendContextUsageLineage;
}

export interface BackendLocalLlmBackend {
  kind?: string;
  base_url?: string;
  models?: string[];
  model?: string;
}

export interface BackendLocalModelReadinessCheck {
  key?: string;
  label?: string;
  ok?: boolean;
  actual?: string;
  required?: string;
}

export interface BackendLocalModelReadiness {
  can_install?: boolean;
  recommended_model?: string;
  reason?: string;
  checks?: BackendLocalModelReadinessCheck[];
  memory_total_bytes?: number;
  disk_free_bytes?: number;
  cpu_logical_cores?: number;
  gpu_summary?: string;
}

export interface BackendLocalLlmHealth {
  available?: boolean;
  selected_backend?: BackendLocalLlmBackend | null;
  probe_order?: string[];
  error?: string;
  kind?: string;
  base_url?: string;
  models?: string[];
  model?: string;
  readiness?: BackendLocalModelReadiness;
}

export interface BackendLocalModelSetupStep {
  key?: string;
  label?: string;
  state?: string;
  detail?: string;
}

export interface BackendLocalModelRepairAction {
  code?: string;
  label?: string;
  detail?: string;
}

export interface BackendLocalModelVerification {
  ready?: boolean;
  next_action?: string;
  paths_redacted?: boolean;
  privacy_fallback?: string;
}

export interface BackendLocalModelEvidenceItem {
  key?: string;
  ok?: boolean;
  detail?: string;
  value?: unknown;
  failed_checks?: unknown[];
  configured?: boolean;
}

export interface BackendLocalModelSetupPlan {
  ready?: boolean;
  can_install?: boolean;
  model?: string;
  readiness?: BackendLocalModelReadiness;
  installed?: boolean;
  running?: boolean;
  models?: string[];
  has_model?: boolean;
  runtime_source?: string;
  bundled_runtime_available?: boolean;
  bundled_runtime_path?: string;
  bundled_models_available?: boolean;
  bundled_models_path?: string;
  bundled_model_available?: boolean;
  bundled_model_configured?: boolean;
  bundle_manifest?: BackendLocalModelBundleManifest;
  steps?: BackendLocalModelSetupStep[];
  next_action?: string;
  repair_action?: BackendLocalModelRepairAction;
  verification?: BackendLocalModelVerification;
  evidence?: BackendLocalModelEvidenceItem[];
}

export interface BackendLocalModelBundleManifest {
  present?: boolean;
  valid?: boolean;
  path?: string;
  model?: string;
  accepted_licenses?: boolean;
  runtime_sha256?: string;
  models_sha256?: string;
  runtime_files?: number;
  models_files?: number;
  error?: string;
}

export interface BackendHardwareAccelerationStatus {
  available?: boolean;
  kind?: string;
  model_path?: string;
  execution_provider?: string;
  available_providers?: string[];
  generation_runtime?: string;
  runtime_package?: string;
  configured_provider?: string;
  selected_provider?: string;
  runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: {
    available?: boolean;
    provider?: string;
    provider_available?: boolean;
    packages?: string[];
    errors?: Record<string, string>;
  };
  errors?: string[];
  error?: string;
  llm?: {
    runtime?: string;
    available?: boolean;
    model_path?: string;
    configured_provider?: string;
    selected_provider?: string;
    runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
    winml?: {
      available?: boolean;
      provider?: string;
      provider_available?: boolean;
      packages?: string[];
      errors?: Record<string, string>;
    };
    errors?: string[];
  };
  text_embedding?: BackendHardwareAccelerationComponentStatus;
  image_embedding?: BackendHardwareAccelerationComponentStatus;
  ocr?: BackendHardwareAccelerationComponentStatus;
}

export interface BackendHardwareAccelerationComponentStatus {
  available?: boolean;
  component?: string;
  kind?: string;
  model_path?: string;
  execution_provider?: string;
  available_providers?: string[];
  runtime_package?: string;
  configured_provider?: string;
  selected_provider?: string;
  runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: BackendHardwareAccelerationStatus["winml"];
  selected_backend?: string;
  runtime?: string;
  model?: string;
  errors?: string[];
  error?: string;
}

export interface BackendHardwareAccelerationSmoke {
  ok?: boolean;
  available?: boolean;
  status?: "ready" | "unavailable";
  operation?: "warmup" | "test_generate" | "test_embedding" | "test_ocr" | "test_image_embedding";
  error?: string;
  errors?: string[];
  message?: string;
  count?: number;
  dim?: number;
  source?: string;
  backend?: {
    kind?: string;
    model_path?: string;
    execution_provider?: string;
    available_providers?: string[];
    generation_runtime?: string;
    runtime_package?: string;
    model_family?: string;
    provider_options?: Record<string, string>;
  };
  llm?: BackendHardwareAccelerationStatus["llm"];
  text_embedding?: BackendHardwareAccelerationComponentStatus;
  image_embedding?: BackendHardwareAccelerationComponentStatus;
  ocr?: BackendHardwareAccelerationComponentStatus;
}

export interface HardwareAccelerationSmokeRequest {
  operation?: "warmup" | "test_generate" | "test_embedding" | "test_ocr" | "test_image_embedding";
  prompt?: string;
  maxTokens?: number;
  modelPath?: string;
  texts?: string[];
  imagePath?: string;
}

export type HardwareAccelerationSmokeRequestBody = {
  model_path?: string;
  prompt?: string;
  max_tokens?: number;
  texts?: string[];
  image_path?: string;
};


export interface BackendAuditEvent {
  id: string;
  task_id?: string;
  event_type: string;
  actor: string;
  created_at: string;
}

export interface BackendSystemInfo {
  platform?: string;
  system?: string;
  machine?: string;
}

export interface BackendInstalledApp {
  id?: string;
  name?: string;
  path?: string;
  command?: string;
  source?: string;
  allowlisted?: boolean;
}

export interface BackendAppsResponse {
  apps: BackendInstalledApp[];
}

export interface BackendFileRevealResult {
  ok?: boolean;
  path?: string;
  revealed?: boolean;
  shown?: boolean;
  error?: string;
}

export interface BackendProcess {
  pid?: number;
  name?: string;
  username?: string;
  cpu_percent?: number;
  memory_bytes?: number;
  status?: string;
}

export interface BackendProcessesResponse {
  processes: BackendProcess[];
  count?: number;
}

export interface BackendStartupItem {
  name?: string;
  path?: string;
  command?: string;
  source?: string;
}

export interface BackendStartupResponse {
  startup_items: BackendStartupItem[];
  count?: number;
}

export interface BackendDisk {
  device?: string;
  mountpoint?: string;
  fstype?: string;
  usage?: {
    total?: number;
    used?: number;
    free?: number;
    percent?: number;
  };
}

export interface BackendSystemDiagnostics {
  info?: Record<string, unknown>;
  disks?: BackendDisk[];
  network?: Record<string, unknown>;
  battery?: Record<string, unknown> | null;
  top_processes?: BackendProcess[];
  suggestions?: string[];
  product?: {
    name?: string;
    version?: string;
  };
  update_channel?: {
    configured?: boolean;
    status?: string;
    label?: string;
    detail?: string;
    check_action?: string;
    offline_only?: boolean;
    user_action_label?: string;
    next_steps?: unknown[];
    release_notes?: {
      available?: boolean;
      label?: string;
      detail?: string;
      path?: string;
      source?: string;
    };
  };
  local_paths?: {
    data_dir?: string;
    database?: string;
    log_dirs?: string[];
  };
  audit?: {
    verification?: Record<string, unknown>;
    latest_event?: Record<string, unknown> | null;
  };
  lan_transport?: Record<string, unknown>;
  recent_counts?: Record<string, unknown>;
  recent_failure_counts?: Record<string, unknown>;
  diagnostic_hints?: string[];
  diagnostic_scope?: string;
  support_package_redaction?: BackendSupportPackageRedaction;
}

export interface BackendSupportPackageRedaction {
  applies_to?: string;
  scope?: string;
  intended_audience?: string;
  public_safe?: boolean;
  review_before_external_sharing?: boolean;
  external_sharing_allowed?: boolean;
  fail_closed?: boolean;
  current_response?: {
    public_safe?: boolean;
    contains_local_paths?: boolean;
    external_review_required?: boolean;
  };
  guidance?: string;
  external_review?: {
    status?: string;
    required_before_external_sharing?: boolean;
    public_safe?: boolean;
    external_sharing_allowed?: boolean;
    fail_closed?: boolean;
    checklist?: unknown[];
  };
}

export interface BackendDiagnosticExportResult {
  ok?: boolean;
  path?: string;
  filename?: string;
  created_at?: string;
  bytes?: number;
  scope?: string;
  error?: string;
}

export interface BackendBrowserLink {
  title?: string;
  url?: string;
}

export interface BackendBrowserPage {
  ok?: boolean;
  url?: string;
  title?: string;
  text?: string;
  links?: BackendBrowserLink[];
  truncated?: boolean;
  adapter?: string;
  error?: string;
}

export interface BackendBrowserSession {
  id?: string;
  task_id?: string | null;
  current_url?: string;
  url?: string;
  title?: string;
  status?: string;
  mode?: string;
  created_at?: string;
  updated_at?: string;
  paused?: boolean;
  takeover?: boolean;
  last_observation?: string | Record<string, unknown> | null;
}

export interface BackendBrowserActivityEvent {
  id?: string;
  session_id?: string;
  task_id?: string | null;
  step_id?: string | null;
  type?: string;
  action?: unknown;
  url?: string;
  title?: string;
  risk_level?: string;
  verdict?: string;
  ok?: boolean;
  error?: string;
  screenshot_url?: string;
  created_at?: string;
}

export interface BackendBrowserActivityEnvelope extends BackendBrowserActivityEvent {
  ok?: boolean;
  event?: BackendBrowserActivityEvent;
  session?: BackendBrowserSession;
}

export interface BackendBrowserSessions {
  ok?: boolean;
  sessions?: BackendBrowserSession[];
  error?: string;
}

export interface BackendBrowserEvents {
  ok?: boolean;
  events?: BackendBrowserActivityEvent[];
  error?: string;
}

export interface BackendBrowserReplayExport {
  ok?: boolean;
  url?: string;
  path?: string;
  events?: BackendBrowserActivityEvent[];
  session?: BackendBrowserSession;
  error?: string;
}

export type BackendBrowserSessionStreamEvent =
  | { type: "connected"; session_id: string }
  | { type: "heartbeat"; session_id?: string }
  | { type: "session"; session: BackendBrowserSession }
  | { type: "event"; event: BackendBrowserActivityEvent }
  | BackendBrowserActivityEvent;

export interface BrowserReplayExport {
  ok?: boolean;
  url?: string;
  path?: string;
  events?: BrowserActivityEvent[];
  session?: BrowserSession;
  error?: string;
}

export interface BackendBrowserLinks {
  ok?: boolean;
  url?: string;
  title?: string;
  links: BackendBrowserLink[];
  error?: string;
}

export interface BackendSkillTool {
  name?: string;
  description?: string;
  agent_owner?: string;
  risk?: string;
  permissions?: unknown[];
  input_schema?: unknown;
  execution_type?: string;
  entry?: string;
  supports_dry_run?: boolean;
  requires_authorized_path?: boolean;
  rollback_hint?: string;
}

export interface BackendSkillSafetyIssue {
  severity?: string;
  location?: string;
  message?: string;
}

export interface BackendInstalledSkill {
  name?: string;
  version?: string;
  agent_owner?: string;
  risk?: string;
  root?: string;
  manifest_path?: string;
  status?: string;
  tools?: BackendSkillTool[];
  safety?: {
    ok?: boolean;
    issues?: BackendSkillSafetyIssue[];
  };
  error?: string;
}

export interface BackendSkillsCatalog {
  skills?: BackendInstalledSkill[];
  count?: number;
  directories?: string[];
  install_directory?: string;
}

export interface BackendSkillImportResult {
  skill: BackendInstalledSkill;
  refresh?: BackendSkillRefresh;
}

export interface BackendSkillRefresh {
  ok?: boolean;
  tool_count?: number;
  skill_count?: number;
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
