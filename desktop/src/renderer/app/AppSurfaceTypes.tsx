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
  DiagnosticExportResult,
  FileSearchMeta,
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
import type { DocumentIntentAction, FileToolTab } from "../components/FileSearchPanel";
import type { HomeReadinessItem, HomeTrustItem, OfficeQuickSkill } from "../features/office";
import type { LengrvisApiClient, RealtimeConnectionStatus } from "../lib/apiClient";
import type { ConnectionState, ViewKey } from "../store";

export type SendResult = { ok: boolean; error?: string };
export type ApprovalSelectionContext = "task" | "queue";
export type DocumentIntent = {
  path: string;
  action: DocumentIntentAction;
  nonce: number;
} | null;

export interface AppSurfaceProps {
  api: LengrvisApiClient;
  activeView: ViewKey;
  connectionState: ConnectionState;
  isLoading: boolean;
  messages: ChatMessage[];
  recentReadableMessages: ChatMessage[];
  tasks: TaskEvent[];
  systemInfo: SystemInfo;
  plan: Plan;
  agentConversations: AgentConversation[];
  safetyReview: SafetyReview;
  auditEntries: AuditLogEntry[];
  settings: AppSettings;
  backendStatus: BackendStatus;
  realtimeStatus: RealtimeConnectionStatus | null;
  localLlmHealth: LocalLLMHealth | null;
  llmHealth: LLMHealthStatus | null;
  llmCostSummary: LLMCostSummary | null;
  intentSuggestions: IntentSuggestion[];
  fileResults: FileSearchResult[];
  fileSearchMeta: FileSearchMeta | null;
  fileSearchError: string | null;
  isSearching: boolean;
  fileToolTab: FileToolTab;
  documentIntent: DocumentIntent;
  focusedTaskId: string | null;
  browserSessions: BrowserSession[];
  browserEvents: BrowserActivityEvent[];
  browserHostSnapshot: BrowserHostSnapshot | null;
  activeBrowserSessionId: string | null;
  browserError: string | null;
  draft: string;
  heroSubmitting: boolean;
  heroSubmitError: string | null;
  homeReadinessItems: HomeReadinessItem[];
  homeTrustItems: HomeTrustItem[];
  pendingApproval: ApprovalRequest | null;
  pendingApprovals: ApprovalRequest[];
  approvalSelectionContext: ApprovalSelectionContext;
  approvalQueueCursor: number;
  isApprovalOpen: boolean;
  approvalError: string | null;
  isCheckingComputer: boolean;
  settingsIntent?: { section: "privacy"; nonce: number } | null;
  onViewChange: (view: ViewKey) => void;
  onRefreshWorkspace: () => void;
  onOpenApprovals: () => void;
  onDraftChange: (draft: string) => void;
  onSubmitHeroPrompt: () => Promise<void>;
  onQuickSkill: (skill: OfficeQuickSkill) => void;
  onReadinessAction: (item: HomeReadinessItem) => void;
  onTaskPilotAction: (task: TaskEvent | null, action: "open" | "approve" | "compose") => void;
  onSendMessage: (content: string) => Promise<SendResult>;
  onExecuteSuggestion: (suggestion: IntentSuggestion) => Promise<void>;
  onSearchFiles: (query: string) => Promise<void>;
  onClearFileResults: () => void;
  onSaveSettings: (settings: AppSettings) => Promise<void>;
  onFileToolChange: (tab: FileToolTab) => void;
  onUseDocument: (path?: string, action?: DocumentIntentAction) => void;
  onDocumentIntentHandled: () => void;
  onRequestCleanupApproval: (scope: string) => Promise<void>;
  onRefreshSystemInfo: () => Promise<void>;
  onExportDiagnostics: () => Promise<DiagnosticExportResult>;
  onRevealPath: (path: string) => Promise<void>;
  onOpenWindowsSettings: (uri: string) => Promise<void>;
  onStartBackend: () => Promise<void>;
  onStopBackend: () => Promise<void>;
  onLocalLlmHealthChange: (health: LocalLLMHealth | null) => void;
  onBrowserSessionsChange: (sessions: BrowserSession[] | ((current: BrowserSession[]) => BrowserSession[])) => void;
  onBrowserEventsChange: (events: BrowserActivityEvent[] | ((current: BrowserActivityEvent[]) => BrowserActivityEvent[])) => void;
  onBrowserHostSnapshotChange: (snapshot: BrowserHostSnapshot | null) => void;
  onActiveBrowserSessionChange: (sessionId: string | null) => void;
  onBrowserErrorChange: (error: string | null) => void;
  onOpenTaskApproval: () => void;
  onCloseApproval: () => void;
  onPreviousApproval: () => void;
  onNextApproval: () => void;
  onApprovalDecision: (approvalId: string, decision: "approved" | "denied", note?: string) => Promise<void>;
}
