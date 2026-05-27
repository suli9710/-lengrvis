export type ApiMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ApiRequest<TBody = unknown> {
  endpoint: string;
  method?: ApiMethod;
  query?: Record<string, string | number | boolean | null | undefined>;
  body?: TBody;
  timeoutMs?: number;
}

export interface ApiError {
  code?: string;
  message: string;
  details?: unknown;
}

export interface ApiResponse<TData = unknown> {
  ok: boolean;
  status: number;
  data?: TData;
  error?: ApiError;
  receivedAt: string;
}

export interface NotificationPayload {
  title: string;
  body: string;
  taskId?: string;
  severity: "info" | "warning" | "error";
}

export type BackendState = "not_configured" | "starting" | "running" | "stopped" | "error";

export interface BackendStatus {
  state: BackendState;
  baseUrl: string;
  pid?: number;
  message?: string;
  lastCheckedAt: string;
  shellMode?: "foreground" | "background";
  guardianState?: "running" | "starting" | "stopped" | "error" | string;
  fullBackendState?: "running" | "starting" | "stopped" | "error" | string;
  fullBackendPort?: number;
  lastWakeReason?: string;
  health?: {
    ok: boolean;
    latencyMs?: number;
  };
}

export interface LocalLLMBackend {
  kind: string;
  baseUrl: string;
  models: string[];
  model?: string;
}

export interface LocalModelReadinessCheck {
  key: string;
  label: string;
  ok: boolean;
  actual: string;
  required: string;
}

export interface LocalModelReadiness {
  canInstall: boolean;
  recommendedModel: string;
  reason: string;
  checks: LocalModelReadinessCheck[];
  memoryTotalBytes: number;
  diskFreeBytes: number;
  cpuLogicalCores: number;
  gpuSummary?: string;
}

export interface LocalLLMHealth {
  available: boolean;
  selectedBackend: LocalLLMBackend | null;
  probeOrder: string[];
  error?: string;
  readiness?: LocalModelReadiness;
}

export interface LLMCapabilities {
  tools: boolean;
  structuredJson: boolean;
  vision: boolean;
  embeddings: boolean;
  promptCache: boolean;
  responsesApi: boolean;
  reasoningEffort: boolean;
  usageBreakdown: boolean;
  local: boolean;
  cloud: boolean;
}

export interface LLMProfile {
  providerName: string;
  model: string;
  baseUrl: string;
  wireApi: string;
  location: "local" | "cloud" | string;
  activeBackend: string;
  capabilities: LLMCapabilities;
  modelProfile: {
    model: string;
    contextWindow: number;
    maxOutputTokens: number;
    known: boolean;
    family: string;
  };
}

export interface LLMRetryStatus {
  maxRetries: number;
  backoffSeconds: number;
  circuitFailureThreshold: number;
  circuitCooldownSeconds: number;
  circuit: {
    state: "open" | "closed" | string;
    failures: number;
    retryAfterSeconds: number;
  };
}

export interface LLMHealthStatus {
  active: {
    available: boolean;
    degraded: boolean;
    provider: string;
    model: string;
    profile: LLMProfile;
    error: string;
  };
  retry: LLMRetryStatus;
}

export interface LLMCostSummary {
  windowHours: number;
  calls: number;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  totalCostUsd: number | null;
  estimated: boolean;
  lastEventAt: string;
  byModel: Array<{
    provider: string;
    model: string;
    calls: number;
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
    totalCostUsd: number;
    estimated: boolean;
  }>;
}

export interface ContextUsageHealth {
  status: "healthy" | "managed" | "watch" | "critical" | "blocked" | "unknown";
  severity: "ok" | "warning" | "error" | "unknown";
  reason: string;
  usedPercent: number;
  freePercent: number;
  freeTokens: number;
  projectedTokens: number;
  projectedPercent: number;
  projectedFreeTokens: number;
  isHealthy: boolean;
}

export interface ContextProjectionSummary {
  enabled: boolean;
  strategy: string;
  compacted: boolean;
  originalTokens: number;
  projectedTokens: number;
  tokensSaved: number;
  messagesRemoved: number;
  adjustments: string[];
  description: string;
}

export interface ContextUsageLineage {
  taskId: string;
  historySource: string;
  messageCount: number;
  systemMessageCount: number;
  agentMessageCount: number;
  messageRoles: Record<string, number>;
  localToolCount: number;
  mcpToolCount: number;
  sessionMemoryItemCount: number;
  includeRegisteredTools: boolean;
  includeSessionMemory: boolean;
  includeProjection: boolean;
  projection: {
    source: string;
    strategy: string;
    boundaryId: string;
    retainedTailCount: number;
  };
}

export interface ContextUsage {
  totalTokens: number;
  usedTokens: number;
  freeTokens: number;
  effectiveContextWindow: number;
  modelContextWindow: number;
  autoCompactThreshold: number;
  manualCompactLimit: number;
  reservedOutputTokens: number;
  warning: {
    tokenCount: number;
    threshold: number;
    percentLeft: number;
    isAboveWarningThreshold: boolean;
    isAboveErrorThreshold: boolean;
    isAboveAutoCompactThreshold: boolean;
    isAtBlockingLimit: boolean;
  };
  health: ContextUsageHealth;
  projection: ContextProjectionSummary;
  lineage: ContextUsageLineage;
}

export type ChatRole = "system" | "developer" | "user" | "assistant" | "tool";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  author: string;
  content: string;
  createdAt: string;
  status?: "sent" | "streaming" | "failed";
}

export interface ChatRequest {
  content: string;
  contextTaskId?: string;
  mode?: "privacy" | "efficiency" | "hybrid";
}

export interface ChatResponse {
  message: ChatMessage;
  taskUpdates?: TaskEvent[];
  runId?: string;
  engine?: "auto" | "os" | "developer" | string;
}

export interface CleanupPlan {
  id: string;
  contentHash?: string;
  title: string;
  summary?: string;
  status?: "draft" | "needs_approval" | "approved" | "executed" | "rolled_back" | string;
  createdAt?: string;
  updatedAt?: string;
  totalBytes?: number;
  reclaimableBytes?: number;
  permanentDeleteBytes?: number;
  trashBytes?: number;
  riskWarnings: string[];
  items: CleanupItem[];
}

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

export interface IntentSuggestion {
  id: string;
  title: string;
  prompt: string;
  confidence: number;
  agentHint?: string;
  reason?: string;
}

export interface PerceptionSuggestionLaunchRequest {
  suggestionId: string;
  prompt?: string;
  mode?: "privacy" | "efficiency" | "hybrid";
}

export interface PerceptionSuggestionLaunchResponse {
  message: ChatMessage;
  taskUpdates?: TaskEvent[];
  runId?: string;
  engine?: "auto" | "os" | "developer" | string;
}

export type TaskState = "queued" | "running" | "blocked" | "completed" | "failed";

export interface TaskEvent {
  id: string;
  runId?: string;
  title: string;
  description: string;
  state: TaskState;
  agent: string;
  createdAt: string;
  updatedAt: string;
  recordings?: TaskStepRecording[];
  cleanupPlan?: CleanupPlan;
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
  finalResult: {
    status: string;
    summary: string;
    safetyReviews: TaskExplainReview[];
    evidence: TaskExplainEvidence[];
  };
  chain: TaskExplainChainItem[];
}

export type PlanStepState = "pending" | "active" | "done" | "blocked";

export interface PlanStep {
  id: string;
  title: string;
  detail: string;
  state: PlanStepState;
  owner: string;
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
}

export interface ApprovalRequest {
  id: string;
  title: string;
  reason: string;
  requester: string;
  riskLevel: SafetySeverity;
  createdAt: string;
  proposedAction: string;
  status: "pending" | "approved" | "denied";
  rawPayload?: unknown;
  cleanupPlan?: CleanupPlan;
}

export interface ApprovalDecision {
  approvalId: string;
  decision: "approved" | "denied";
  note?: string;
}

export type DocumentBlockType =
  | "title"
  | "heading"
  | "paragraph"
  | "list"
  | "table"
  | "image"
  | "code"
  | "metadata"
  | string;

export interface DocumentTable {
  id: string;
  title?: string;
  columns: string[];
  rows: string[][];
  page?: number;
  sourceBlockId?: string;
}

export interface DocumentBlock {
  id: string;
  type: DocumentBlockType;
  text?: string;
  level?: number;
  page?: number;
  order?: number;
  columns?: string[];
  rows?: string[][];
  metadata?: Record<string, unknown>;
}

export interface DocumentCitation {
  id: string;
  label: string;
  text: string;
  path?: string;
  blockId?: string;
  page?: number;
  score?: number;
}

export interface DocumentIR {
  id: string;
  path: string;
  title: string;
  mimeType?: string;
  language?: string;
  summary?: string;
  text?: string;
  truncated?: boolean;
  blocks: DocumentBlock[];
  tables: DocumentTable[];
  citations?: DocumentCitation[];
  metadata?: Record<string, unknown>;
  createdAt?: string;
}

export interface DocumentParseRequest {
  path: string;
  includeText?: boolean;
}

export interface DocumentAskRequest {
  path?: string;
  documentId?: string;
  question: string;
  topK?: number;
}

export interface DocumentAskResponse {
  answer: string;
  citations: DocumentCitation[];
  sourceChunks?: DocumentCitation[];
  note?: string;
}

export interface DocumentCompareRequest {
  paths: string[];
  focus?: string;
}

export interface DocumentDifference {
  id: string;
  title: string;
  detail: string;
  severity?: "info" | "warning" | "critical" | string;
  citations?: DocumentCitation[];
}

export interface DocumentCompareResponse {
  summary: string;
  documents: DocumentIR[];
  differences: DocumentDifference[];
  tables?: DocumentTable[];
  note?: string;
}

export type CleanupDisposition = "permanent_delete" | "trash" | "suggestion_only" | "skip" | string;

export interface CleanupItem {
  id: string;
  path: string;
  name?: string;
  action: string;
  disposition: CleanupDisposition;
  bucket?: "direct_delete" | "recycle_bin" | "suggestion_only" | "immediate" | "approval" | "info_only" | string;
  sizeBytes?: number;
  sizeMb?: number;
  category?: string;
  detail?: string;
  reason?: string;
  riskLevel?: SafetySeverity | string;
  canRollback?: boolean;
  selected?: boolean;
  modifiedAt?: string;
  metadata?: Record<string, unknown>;
}

export interface CleanupScanRequest {
  roots?: string[];
  thresholdMb?: number;
  includeCaches?: boolean;
}

export interface CleanupPlanRequest extends CleanupScanRequest {
  itemIds?: string[];
  preferTrash?: boolean;
}

export interface CleanupExecuteRequest {
  planId?: string;
  contentHash?: string;
  selectedItemIds?: string[];
  roots?: string[];
  items?: CleanupItem[];
  dryRun?: boolean;
  approved?: boolean;
  approvalId?: string;
}

export interface CleanupRollbackRequest {
  planId?: string;
  executionId?: string;
}

export interface CleanupExecutionResult {
  ok: boolean;
  planId?: string;
  executionId?: string;
  freedBytes?: number;
  executed: CleanupItem[];
  rolledBack?: CleanupItem[];
  errors?: string[];
}

export interface FileSearchResult {
  id: string;
  path: string;
  match: string;
  line: number;
  score: number;
}

export interface InstalledApp {
  id: string;
  name: string;
  path?: string;
  command?: string;
  source: "builtin" | "start_menu" | "registry" | string;
  allowlisted: boolean;
}

export interface SystemProcess {
  pid: number;
  name: string;
  username?: string;
  cpuPercent: number;
  memoryBytes: number;
  status?: string;
}

export interface StartupItem {
  name: string;
  path?: string;
  command?: string;
  source: string;
}

export interface DiskUsage {
  total?: number;
  used?: number;
  free?: number;
  percent?: number;
}

export interface DiskInfo {
  device: string;
  mountpoint: string;
  fstype?: string;
  usage?: DiskUsage;
}

export interface SystemDiagnostic {
  info: Record<string, unknown>;
  disks: DiskInfo[];
  network: Record<string, unknown>;
  battery?: Record<string, unknown> | null;
  topProcesses: SystemProcess[];
  startupItems?: StartupItem[];
  suggestions: string[];
}

export interface BrowserLinkResult {
  title: string;
  url: string;
}

export interface BrowserPageSnapshot {
  ok: boolean;
  url: string;
  title: string;
  text: string;
  links: BrowserLinkResult[];
  truncated?: boolean;
  adapter?: string;
  error?: string;
}

export type BrowserActionKind =
  | "open"
  | "navigate"
  | "click"
  | "fill"
  | "submit"
  | "scroll"
  | "wait"
  | "screenshot"
  | "observe"
  | "cua";

export interface BrowserAction {
  kind: BrowserActionKind;
  url?: string;
  selector?: string;
  text?: string;
  fields?: Record<string, string>;
  dry_run?: boolean;
  approved?: boolean;
  approval_id?: string;
  [key: string]: unknown;
}

export interface BrowserSession {
  id: string;
  task_id?: string;
  current_url: string;
  title: string;
  status: "idle" | "loading" | "running" | "paused" | "stopped" | "error" | "awaiting_approval" | string;
  mode: "watch" | "agent" | "takeover" | string;
  created_at: string;
  updated_at: string;
  paused: boolean;
  takeover: boolean;
  last_observation?: string | Record<string, unknown> | null;
}

export interface BrowserActivityEvent {
  id: string;
  session_id: string;
  task_id?: string;
  step_id?: string;
  type: string;
  action?: BrowserAction;
  url?: string;
  title?: string;
  risk_level?: "low" | "medium" | "high" | "critical" | string;
  verdict?: string;
  ok: boolean;
  error?: string;
  screenshot_url?: string;
  created_at: string;
}

export interface BrowserHostBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BrowserHostOpenRequest {
  sessionId?: string;
  taskId?: string;
  url?: string;
  title?: string;
  mode?: string;
}

export interface BrowserHostActionRequest {
  sessionId: string;
  action: BrowserAction;
}

export interface BrowserHostSnapshot {
  sessions: BrowserSession[];
  events: BrowserActivityEvent[];
  activeSessionId?: string | null;
  visible: boolean;
  hostAvailable: boolean;
  error?: string;
}

export interface BrowserHostActionResult {
  ok: boolean;
  session?: BrowserSession;
  event?: BrowserActivityEvent;
  snapshot?: BrowserHostSnapshot;
  error?: string;
}

export interface ToolExecutionPreview {
  ok: boolean;
  dryRun: boolean;
  toolName: string;
  args: Record<string, unknown>;
  diffPreview?: unknown;
  riskLevel?: string;
  approvalRequired?: boolean;
}

export interface McpServerConfig {
  name: string;
  url: string;
  enabled: boolean;
  id?: string;
  command?: string;
  args?: string[];
  transport?: string;
  auth?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SkillToolInfo {
  name: string;
  description: string;
  agentOwner: string;
  risk: string;
  executionType: "python" | "shell" | "http" | string;
  entry: string;
}

export interface SkillSafetyIssue {
  severity: "error" | "warning";
  location: string;
  message: string;
}

export interface InstalledSkill {
  name: string;
  version: string;
  agentOwner: string;
  risk: string;
  root: string;
  manifestPath: string;
  status: "ready" | "error" | string;
  tools: SkillToolInfo[];
  safety: {
    ok: boolean;
    issues: SkillSafetyIssue[];
  };
  error?: string;
}

export interface SkillsCatalog {
  skills: InstalledSkill[];
  count: number;
  directories: string[];
  installDirectory: string;
}

export interface SkillImportResult {
  skill: InstalledSkill;
  refresh: {
    ok: boolean;
    toolCount: number;
    skillCount: number;
  };
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

export interface AppSettings {
  apiBaseUrl: string;
  autoStartBackend: boolean;
  telemetryEnabled: boolean;
  compactMode: boolean;
  theme: "system" | "light" | "dark";
  providerName: string;
  model: string;
  reviewModel: string;
  wireApi: "chat_completions" | "responses";
  requiresOpenAiAuth: boolean;
  modelReasoningEffort: string;
  disableResponseStorage: boolean;
  temperature: number;
  maxTokens: number;
  timeout: number;
  llmApiMaxRetries: number;
  llmApiRetryBackoffSeconds: number;
  llmApiCircuitFailureThreshold: number;
  llmApiCircuitCooldownSeconds: number;
  modelContextWindow: number;
  modelAutoCompactTokenLimit: number;
  workspaceRoot: string;
  allowedDirectories?: string[];
  allowBrowserNetwork: boolean;
  remoteDesktopEnabled: boolean;
  appAllowlist: string[];
  browserMaxPageBytes: number;
  browserScreenshotDir: string;
  onnxModelPath: string;
  onnxExecutionProvider: "Auto" | "WinML" | "DirectML" | "OpenVINO" | "CPU" | string;
  onnxProviderPreference: string;
  onnxDirectmlDeviceId: string;
  onnxOpenvinoDevice: string;
  onnxOpenvinoCacheDir: string;
  onnxWarmOnStartup: boolean;
  onnxModelFamily: string;
  onnxEmbeddingBackend: string;
  onnxEmbeddingModelPath: string;
  onnxEmbeddingExecutionProvider: string;
  onnxEmbeddingModelId: string;
  onnxEmbeddingMaxBatchSize: number;
  imageEmbeddingBackend: string;
  onnxImageEmbeddingModelPath: string;
  onnxImageEmbeddingExecutionProvider: string;
  onnxImageEmbeddingModelId: string;
  onnxImageEmbeddingMaxBatchSize: number;
  ocrBackend: string;
  ocrExecutionProvider: string;
  ocrOpenvinoModelDir: string;
  ocrOpenvinoDevice: string;
  ocrLang: string;
  ocrMinConfidence: number;
  ocrBatchSize: number;
  mode: "privacy" | "efficiency" | "hybrid";
  allowCloudContext: boolean;
  allowFileContentUpload: boolean;
  mcpServers: McpServerConfig[];
}

export type HardwareAccelerationRuntime = "auto" | "winml" | "directml" | "openvino" | "cpu";

export type HardwareAccelerationStatus = "ready" | "missing" | "error";

export interface HardwareAccelerationCheck {
  key: string;
  label: string;
  status: HardwareAccelerationStatus;
  details?: string;
  required?: string;
  actual?: string;
}

export interface HardwareAccelerationStatusPayload {
  available: boolean;
  kind: string;
  modelPath: string;
  executionProvider: string;
  availableProviders: string[];
  generationRuntime: string;
  runtimePackage?: string;
  configuredProvider?: string;
  selectedProvider?: string;
  runtimePackages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: {
    available?: boolean;
    provider?: string;
    providerAvailable?: boolean;
    packages?: string[];
    errors?: Record<string, string>;
  };
  errors?: string[];
  error?: string;
  llm?: {
    runtime?: string;
    available?: boolean;
    modelPath?: string;
    configuredProvider?: string;
    selectedProvider?: string;
    runtimePackages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
    winml?: {
      available?: boolean;
      provider?: string;
      providerAvailable?: boolean;
      packages?: string[];
      errors?: Record<string, string>;
    };
    errors?: string[];
  };
  textEmbedding?: HardwareAccelerationComponentStatus;
  imageEmbedding?: HardwareAccelerationComponentStatus;
  ocr?: HardwareAccelerationComponentStatus;
}

export interface HardwareAccelerationComponentStatus {
  available: boolean;
  component?: string;
  kind?: string;
  modelPath?: string;
  executionProvider?: string;
  availableProviders?: string[];
  runtimePackage?: string;
  configuredProvider?: string;
  selectedProvider?: string;
  runtimePackages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: HardwareAccelerationStatusPayload["winml"];
  selectedBackend?: string;
  runtime?: string;
  model?: string;
  errors?: string[];
  error?: string;
}

export interface HardwareAccelerationSmokePayload {
  ok: boolean;
  available: boolean;
  status: "ready" | "unavailable";
  operation: "warmup" | "test_generate" | "test_embedding" | "test_ocr" | "test_image_embedding";
  error?: string;
  errors?: string[];
  message?: string;
  count?: number;
  dim?: number;
  source?: string;
  backend?: {
    kind: string;
    model_path: string;
    execution_provider: string;
    available_providers: string[];
    generation_runtime: string;
    runtime_package?: string;
    model_family?: string;
    provider_options?: Record<string, string>;
  };
  llm?: HardwareAccelerationStatusPayload["llm"];
}

export interface AuditLogEntry {
  id: string;
  actor: string;
  action: string;
  target: string;
  level: "info" | "warning" | "error";
  createdAt: string;
}

export interface SystemInfo {
  appVersion: string;
  electronVersion: string;
  chromeVersion: string;
  nodeVersion: string;
  platform: string;
  arch: string;
  backendBaseUrl: string;
  diagnostics?: SystemDiagnostic;
  processes?: SystemProcess[];
  startupItems?: StartupItem[];
  installedApps?: InstalledApp[];
}

export interface MavrisDesktopBridge {
  api: {
    request: <TResponse = unknown, TBody = unknown>(
      request: ApiRequest<TBody>
    ) => Promise<ApiResponse<TResponse>>;
  };
  backendBaseUrl?: string;
  backend: {
    getStatus: () => Promise<BackendStatus>;
    start: () => Promise<BackendStatus>;
    stop: () => Promise<BackendStatus>;
    foreground: () => Promise<BackendStatus>;
    background: () => Promise<BackendStatus>;
  };
  dialog: {
    chooseSkillDirectory: () => Promise<string | null>;
    chooseSkillZip: () => Promise<string | null>;
  };
  browserHost: {
    getSnapshot: () => Promise<BrowserHostSnapshot>;
    open: (request: BrowserHostOpenRequest) => Promise<BrowserHostActionResult>;
    show: (sessionId: string) => Promise<BrowserHostActionResult>;
    hide: () => Promise<BrowserHostActionResult>;
    setBounds: (bounds: BrowserHostBounds) => Promise<BrowserHostActionResult>;
    pause: (sessionId: string) => Promise<BrowserHostActionResult>;
    resume: (sessionId: string) => Promise<BrowserHostActionResult>;
    takeover: (sessionId: string) => Promise<BrowserHostActionResult>;
    release: (sessionId: string) => Promise<BrowserHostActionResult>;
    stop: (sessionId: string) => Promise<BrowserHostActionResult>;
    performAction: (request: BrowserHostActionRequest) => Promise<BrowserHostActionResult>;
    onSnapshot: (handler: (snapshot: BrowserHostSnapshot) => void) => () => void;
  };
  shell: {
    openExternal: (url: string) => Promise<void>;
  };
  notifications: {
    show: (payload: NotificationPayload) => Promise<{ shown: boolean; reason?: string }>;
    onOpenTask: (handler: (taskId: string) => void) => () => void;
  };
  platform:
    | "aix"
    | "android"
    | "darwin"
    | "freebsd"
    | "haiku"
    | "linux"
    | "openbsd"
    | "sunos"
    | "win32"
    | "cygwin"
    | "netbsd";
  versions: {
    app: string;
    electron: string;
    chrome: string;
    node: string;
  };
}
