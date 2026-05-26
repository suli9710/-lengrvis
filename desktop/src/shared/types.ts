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

export interface LocalLLMHealth {
  available: boolean;
  selectedBackend: LocalLLMBackend | null;
  probeOrder: string[];
  error?: string;
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

export type TaskState = "queued" | "running" | "blocked" | "completed" | "failed";

export interface TaskEvent {
  id: string;
  title: string;
  description: string;
  state: TaskState;
  agent: string;
  createdAt: string;
  updatedAt: string;
  recordings?: TaskStepRecording[];
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
}

export interface ApprovalDecision {
  approvalId: string;
  decision: "approved" | "denied";
  note?: string;
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
  allowBrowserNetwork: boolean;
  remoteDesktopEnabled: boolean;
  appAllowlist: string[];
  browserMaxPageBytes: number;
  browserScreenshotDir: string;
  onnxModelPath: string;
  onnxExecutionProvider: string;
  mode: "privacy" | "efficiency" | "hybrid";
  allowCloudContext: boolean;
  allowFileContentUpload: boolean;
  mcpServers: McpServerConfig[];
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
  };
  dialog: {
    chooseSkillDirectory: () => Promise<string | null>;
    chooseSkillZip: () => Promise<string | null>;
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
