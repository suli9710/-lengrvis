import type {
  AgentConversation,
  AppSettings,
  ApprovalRequest,
  AuditLogEntry,
  BackendStatus,
  BrowserActivityEvent,
  BrowserHostSnapshot,
  BrowserSession,
  ChatMessage,
  ContextUsage,
  FileSearchResult,
  IntentSuggestion,
  LLMCostSummary,
  LLMHealthStatus,
  LocalLLMHealth,
  Plan,
  SafetyReview,
  SystemInfo,
  TaskEvent
} from "../../shared/types";

export type AssistantMode = "privacy" | "efficiency" | "hybrid";
export type ViewKey =
  | "home"
  | "chat"
  | "apps"
  | "documents"
  | "documentOcr"
  | "papers"
  | "courseware"
  | "reports"
  | "gallery"
  | "imageOcr"
  | "people"
  | "places"
  | "timeline"
  | "files"
  | "computer"
  | "agents"
  | "agentOps"
  | "browser"
  | "memories"
  | "safety"
  | "settings";
export type ConnectionState = "online" | "checking" | "offline";

export interface BackendSettingsSlice {
  backendStatus: BackendStatus;
  settings: AppSettings;
  localLlmHealth: LocalLLMHealth | null;
  llmHealth: LLMHealthStatus | null;
  llmCostSummary: LLMCostSummary | null;
  contextUsage: ContextUsage | null;
  mode: AssistantMode;
  isLoading: boolean;
  setBackendStatus: (backendStatus: BackendStatus) => void;
  setSettings: (settings: AppSettings) => void;
  setLocalLlmHealth: (localLlmHealth: LocalLLMHealth | null) => void;
  setLlmHealth: (llmHealth: LLMHealthStatus | null) => void;
  setLlmCostSummary: (llmCostSummary: LLMCostSummary | null) => void;
  setContextUsage: (contextUsage: ContextUsage | null) => void;
  setMode: (mode: AssistantMode) => void;
  setIsLoading: (isLoading: boolean) => void;
}

export interface ChatRunsSlice {
  messages: ChatMessage[];
  tasks: TaskEvent[];
  plan: Plan;
  agentConversations: AgentConversation[];
  fileResults: FileSearchResult[];
  intentSuggestions: IntentSuggestion[];
  isSearching: boolean;
  focusedTaskId: string | null;
  setMessages: (messages: ChatMessage[] | ((current: ChatMessage[]) => ChatMessage[])) => void;
  setTasks: (tasks: TaskEvent[] | ((current: TaskEvent[]) => TaskEvent[])) => void;
  setPlan: (plan: Plan) => void;
  setAgentConversations: (
    agentConversations: AgentConversation[] | ((current: AgentConversation[]) => AgentConversation[])
  ) => void;
  setFileResults: (fileResults: FileSearchResult[]) => void;
  setIntentSuggestions: (intentSuggestions: IntentSuggestion[]) => void;
  setIsSearching: (isSearching: boolean) => void;
  setFocusedTaskId: (focusedTaskId: string | null) => void;
}

export interface BrowserActivitySlice {
  browserSessions: BrowserSession[];
  browserEvents: BrowserActivityEvent[];
  browserHostSnapshot: BrowserHostSnapshot | null;
  activeBrowserSessionId: string | null;
  browserError: string | null;
  setBrowserSessions: (browserSessions: BrowserSession[] | ((current: BrowserSession[]) => BrowserSession[])) => void;
  setBrowserEvents: (
    browserEvents: BrowserActivityEvent[] | ((current: BrowserActivityEvent[]) => BrowserActivityEvent[])
  ) => void;
  setBrowserHostSnapshot: (browserHostSnapshot: BrowserHostSnapshot | null) => void;
  setActiveBrowserSessionId: (activeBrowserSessionId: string | null) => void;
  setBrowserError: (browserError: string | null) => void;
}

export interface OfficeUiSlice {
  activeView: ViewKey;
  setActiveView: (activeView: ViewKey) => void;
}

export interface ApprovalsSlice {
  safetyReview: SafetyReview;
  approvalRequests: ApprovalRequest[];
  auditEntries: AuditLogEntry[];
  systemInfo: SystemInfo;
  isApprovalOpen: boolean;
  approvalError: string | null;
  setSafetyReview: (safetyReview: SafetyReview) => void;
  setApprovalRequests: (approvalRequests: ApprovalRequest[] | ((current: ApprovalRequest[]) => ApprovalRequest[])) => void;
  setAuditEntries: (auditEntries: AuditLogEntry[] | ((current: AuditLogEntry[]) => AuditLogEntry[])) => void;
  setSystemInfo: (systemInfo: SystemInfo) => void;
  setIsApprovalOpen: (isApprovalOpen: boolean) => void;
  setApprovalError: (approvalError: string | null) => void;
}

export type MavrisStore = BackendSettingsSlice & ChatRunsSlice & BrowserActivitySlice & OfficeUiSlice & ApprovalsSlice;
