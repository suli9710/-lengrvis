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
  severity?: "info" | "warning" | "error";
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

export interface AppSettings {
  apiBaseUrl: string;
  autoStartBackend: boolean;
  telemetryEnabled: boolean;
  compactMode: boolean;
  theme: "system" | "light" | "dark";
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
    show: (title: string, body: string) => Promise<{ shown: boolean }>;
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
