import type { AcceptConsentRequest, ConsentRecord, ConsentStatusResult, LegalDocId } from "./consent";
import type { BackendApprovalPayload } from "./executionTypes";
import type { FileRevealResult } from "./fileLibraryTypes";
import type {
  BrowserHostActionRequest,
  BrowserHostActionResult,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot
} from "./browserTypes";
import type {
  ApiRequest,
  ApiResponse,
  BackendStatus,
  DesktopBrowserSessionRequest,
  DesktopCommerceLicenseActivateRequest,
  DesktopCommerceLicenseInstallRequest,
  DesktopCommercePolicyImportRequest,
  DesktopHardwareAccelerationSmokeRequest,
  DesktopMemoryRecallRequest,
  DesktopMemorySaveRequest,
  DesktopOpenSettingsRequest,
  DesktopPerceptionSuggestionLaunchRequest,
  DesktopPermissionPolicyRelaxationRequest,
  DesktopPermissionRuleDeleteRequest,
  DesktopPermissionRuleUpsertRequest,
  DesktopPrivacyEraseRequest,
  DesktopPrivacyEraseResponse,
  DesktopRunStartRequest,
  DesktopScheduleCreateRequest,
  DesktopScheduleEnableRequest,
  DesktopSensitiveChangeConfirmation,
  DesktopSettingsPatch,
  DesktopWebSocketSubscribeHandlers,
  DesktopWebSocketSubscribeRequest,
  MobilePairingRemoteInputGrantRequest,
  MobilePairingRevokeRemoteInputGrantRequest,
  NotificationPayload
} from "./desktopBridgeTypes";
import type { DocumentAskRequest, DocumentCompareRequest, DocumentParseRequest } from "./documentTypes";
import type {
  CredentialBrokerResult,
  CredentialFillRequest,
  CredentialRef,
  CredentialRefRequest,
  CredentialSessionRequest,
  CredentialUseTicketRequest
} from "./credentialTypes";

export type {
  ChatMessage,
  ChatMessageContent,
  ChatMessagePart,
  ChatMessageStatus,
  ChatRequest,
  ChatResponse,
  ChatRole,
  InstalledApp,
  InstalledSkill,
  IntentSuggestion,
  PerceptionSuggestionLaunchRequest,
  PerceptionSuggestionLaunchResponse,
  SkillImportResult,
  SkillSafetyIssue,
  SkillsCatalog,
  SkillToolInfo
} from "./catalogTypes";
export type {
  BrowserAction,
  BrowserActionKind,
  BrowserActivityEvent,
  BrowserHostActionRequest,
  BrowserHostActionResult,
  BrowserHostBounds,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  BrowserLinkResult,
  BrowserPageSnapshot,
  BrowserReplayExport,
  BrowserSession
} from "./browserTypes";
export type {
  CredentialBrokerResult,
  CredentialFillRequest,
  CredentialPurpose,
  CredentialRef,
  CredentialRefRequest,
  CredentialSessionRequest,
  CredentialUseTicket,
  CredentialUseTicketRequest
} from "./credentialTypes";
export type {
  CommerceFeature,
  CommerceLicenseState,
  CommerceLicenseStatus,
  CommercePlan,
  CommercePlanStatus,
  CommerceQuotaStatus,
  CommerceQuotaWindow
} from "./commerceTypes";
export type {
  CleanupDisposition,
  CleanupExecuteRequest,
  CleanupExecutionResult,
  CleanupItem,
  CleanupPlan,
  CleanupPlanRequest,
  CleanupRollbackRequest,
  CleanupScanRequest
} from "./cleanupTypes";
export type {
  DocumentAskRequest,
  DocumentAskResponse,
  DocumentBlock,
  DocumentBlockType,
  DocumentCitation,
  DocumentCompareRequest,
  DocumentCompareResponse,
  DocumentDifference,
  DocumentIR,
  DocumentParseRequest,
  DocumentTable
} from "./documentTypes";
export type {
  AgentConversation,
  AgentMessage,
  ApprovalDecision,
  ApprovalRequest,
  BackendApprovalPayload,
  CommandExecutionResult,
  CommandInfo,
  OpenAIToolCall,
  PermissionMode,
  Plan,
  PlanStep,
  PlanStepState,
  RunEventPayload,
  SafetyFinding,
  SafetyReview,
  SafetySeverity,
  TaskArtifact,
  TaskArtifactsSummary,
  TaskBoundaryEvent,
  TaskCompletionArtifact,
  TaskCompletionEvidence,
  TaskCompletionEvidenceLevel,
  TaskCompletionEvidenceStatus,
  TaskEvent,
  TaskExplain,
  TaskExplainChainItem,
  TaskExplainEvidence,
  TaskExplainMessage,
  TaskExplainReview,
  TaskExplainStep,
  TaskState,
  TaskStepRecording,
  TaskStepRecordingFrame
} from "./executionTypes";
export type {
  HardwareAccelerationCheck,
  HardwareAccelerationComponentStatus,
  HardwareAccelerationOperation,
  HardwareAccelerationRuntime,
  HardwareAccelerationSmokePayload,
  HardwareAccelerationStatus,
  HardwareAccelerationStatusPayload
} from "./hardwareAccelerationTypes";
export type {
  FileClusterOptions,
  FileRevealResult,
  FileSearchMeta,
  FileSearchResponse,
  FileSearchResult,
  IndexStatus,
  LocalLibraryItem,
  LocalLibraryResponse,
  LocalLibraryScopeSummary,
  LocalLibraryStats
} from "./fileLibraryTypes";
export type {
  ContextProjectionSummary,
  ContextUsage,
  ContextUsageHealth,
  ContextUsageLineage,
  LLMCapabilities,
  LLMCostSummary,
  LLMHealthStatus,
  LLMProfile,
  LLMRetryStatus
} from "./llmContextTypes";
export type {
  LocalLLMBackend,
  LocalLLMHealth,
  LocalModelEvidenceItem,
  LocalModelReadiness,
  LocalModelReadinessCheck,
  LocalModelRepairAction,
  LocalModelSetupPlan,
  LocalModelSetupStep,
  LocalModelSetupStepState,
  LocalModelVerificationSummary
} from "./localModelTypes";
export type {
  AppSettings,
  McpServerConfig
} from "./settingsTypes";
export type {
  DiagnosticExportResult,
  DiskInfo,
  DiskUsage,
  LocalMetricsSummary,
  StartupItem,
  SystemDiagnostic,
  SystemDiagnosticAudit,
  SystemDiagnosticCurrentResponseReview,
  SystemDiagnosticExternalReview,
  SystemDiagnosticLocalPaths,
  SystemDiagnosticProduct,
  SystemDiagnosticReleaseNotes,
  SystemDiagnosticSupportPackageRedaction,
  SystemDiagnosticUpdateChannel,
  SystemInfo,
  SystemProcess
} from "./systemTypes";
export type {
  ApiError,
  ApiMethod,
  ApiQueryValue,
  ApiRequest,
  ApiResponse,
  BackendState,
  BackendStatus,
  DesktopBrowserSessionRequest,
  DesktopCommerceLicenseActivateRequest,
  DesktopCommerceLicenseInstallRequest,
  DesktopCommercePolicyImportRequest,
  DesktopHardwareAccelerationSmokeRequest,
  DesktopMemoryRecallRequest,
  DesktopMemorySaveRequest,
  DesktopOpenSettingsRequest,
  DesktopPerceptionSuggestionLaunchRequest,
  DesktopPermissionPolicyRelaxationRequest,
  DesktopPermissionRule,
  DesktopPermissionRuleDeleteRequest,
  DesktopPermissionRuleUpsertRequest,
  DesktopPermissionTimeWindow,
  DesktopPrivacyEraseRequest,
  DesktopPrivacyEraseResponse,
  DesktopRunEngine,
  DesktopRunMode,
  DesktopRunStartRequest,
  DesktopScheduleCreateRequest,
  DesktopScheduleEnableRequest,
  DesktopSensitiveChangeConfirmation,
  DesktopSettingsPatch,
  DesktopWebSocketBridgeEvent,
  DesktopWebSocketOpenRequest,
  DesktopWebSocketOpenResult,
  DesktopWebSocketSubscribeHandlers,
  DesktopWebSocketSubscribeRequest,
  MobilePairingRemoteInputGrantRequest,
  MobilePairingRevokeRemoteInputGrantRequest,
  NotificationPayload
} from "./desktopBridgeTypes";

export interface PrivacyEraseResult {
  scope: "local_only";
  deletedRowsByTable: Record<string, number>;
  deletedRowsTotal: number;
  deletedDiagnosticPackages: number;
  preserved: string[];
  settingsReset: boolean;
  manualLogCleanupRequired: boolean;
  auditRecorded: boolean;
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

export interface AuditLogEntry {
  id: string;
  actor: string;
  action: string;
  target: string;
  level: "info" | "warning" | "error";
  createdAt: string;
}

export interface LengrvisDesktopBridge {
  api: {
    request: <TResponse = unknown, TBody = unknown>(
      request: ApiRequest<TBody>
    ) => Promise<ApiResponse<TResponse>>;
    abortInflight: (abortGroup: string) => Promise<void>;
  };
  realtime: {
    subscribe: (
      request: DesktopWebSocketSubscribeRequest,
      handlers: DesktopWebSocketSubscribeHandlers
    ) => () => void;
  };
  backendBaseUrl?: string;
  backend: {
    getStatus: () => Promise<BackendStatus>;
    start: () => Promise<BackendStatus>;
    stop: () => Promise<BackendStatus>;
    foreground: () => Promise<BackendStatus>;
    background: () => Promise<BackendStatus>;
  };
  commands: {
    execute: (request: { name: string; args?: Record<string, unknown> }) => Promise<ApiResponse<unknown>>;
  };
  approvals: {
    approve: (approvalId: string) => Promise<ApiResponse<BackendApprovalPayload>>;
    reject: (approvalId: string) => Promise<ApiResponse<BackendApprovalPayload>>;
  };
  tasks: {
    pause: (taskId: string) => Promise<ApiResponse<unknown>>;
    resume: (taskId: string) => Promise<ApiResponse<unknown>>;
    cancel: (taskId: string) => Promise<ApiResponse<unknown>>;
    rollback: (taskId: string) => Promise<ApiResponse<unknown>>;
  };
  cleanup: {
    execute: (body: Record<string, unknown>) => Promise<ApiResponse<unknown>>;
    rollback: (body: Record<string, unknown>) => Promise<ApiResponse<unknown>>;
  };
  skills: {
    importPackage: (path: string) => Promise<ApiResponse<unknown>>;
    refresh: () => Promise<ApiResponse<unknown>>;
  };
  localModel: {
    install: (request: { model?: string }) => Promise<ApiResponse<unknown>>;
  };
  ollama: {
    install: () => Promise<ApiResponse<unknown>>;
    pull: (request?: { model?: string }) => Promise<ApiResponse<unknown>>;
    start: () => Promise<ApiResponse<unknown>>;
  };
  runs: {
    start: (request: DesktopRunStartRequest) => Promise<ApiResponse<unknown>>;
  };
  perception: {
    launchSuggestion: (request: DesktopPerceptionSuggestionLaunchRequest) => Promise<ApiResponse<unknown>>;
  };
  hardwareAcceleration: {
    smoke: (request: DesktopHardwareAccelerationSmokeRequest) => Promise<ApiResponse<unknown>>;
  };
  browserBackend: {
    observe: (request: DesktopBrowserSessionRequest) => Promise<ApiResponse<unknown>>;
    replayExport: (request: DesktopBrowserSessionRequest) => Promise<ApiResponse<unknown>>;
  };
  commerce: {
    installLicense: (request: DesktopCommerceLicenseInstallRequest) => Promise<ApiResponse<unknown>>;
    activateLicense: (request: DesktopCommerceLicenseActivateRequest) => Promise<ApiResponse<unknown>>;
    importPolicy: (request: DesktopCommercePolicyImportRequest) => Promise<ApiResponse<unknown>>;
  };
  memories: {
    save: (request: DesktopMemorySaveRequest) => Promise<ApiResponse<unknown>>;
    recall: (request: DesktopMemoryRecallRequest) => Promise<ApiResponse<unknown>>;
    forget: (memoryId: string) => Promise<ApiResponse<unknown>>;
  };
  schedules: {
    list: () => Promise<ApiResponse<unknown>>;
    create: (request: DesktopScheduleCreateRequest) => Promise<ApiResponse<unknown>>;
    delete: (scheduleId: string) => Promise<ApiResponse<unknown>>;
    enable: (request: DesktopScheduleEnableRequest) => Promise<ApiResponse<unknown>>;
  };
  system: {
    openSettings: (request: DesktopOpenSettingsRequest) => Promise<ApiResponse<unknown>>;
    exportDiagnosticsPackage: () => Promise<ApiResponse<unknown>>;
  };
  privacy: {
    eraseLocalData: (
      request: DesktopPrivacyEraseRequest
    ) => Promise<ApiResponse<DesktopPrivacyEraseResponse>>;
  };
  documents: {
    parse: (request: DocumentParseRequest) => Promise<ApiResponse<unknown>>;
    ask: (request: DocumentAskRequest) => Promise<ApiResponse<unknown>>;
    compare: (request: DocumentCompareRequest) => Promise<ApiResponse<unknown>>;
  };
  settings: {
    confirmSensitiveChange: (patch: DesktopSettingsPatch) => Promise<ApiResponse<DesktopSensitiveChangeConfirmation>>;
    testLlmProvider: () => Promise<ApiResponse<unknown>>;
    save: (patch: DesktopSettingsPatch) => Promise<ApiResponse<unknown>>;
  };
  permissionPolicy: {
    confirmRelaxation: (
      request: DesktopPermissionPolicyRelaxationRequest
    ) => Promise<ApiResponse<DesktopSensitiveChangeConfirmation>>;
    upsertRule: (request: DesktopPermissionRuleUpsertRequest) => Promise<ApiResponse<unknown>>;
    deleteRule: (request: DesktopPermissionRuleDeleteRequest) => Promise<ApiResponse<unknown>>;
  };
  mobilePairing: {
    createCode: () => Promise<ApiResponse<unknown>>;
    listDevices: () => Promise<ApiResponse<unknown>>;
    revokeDevice: (deviceId: string) => Promise<ApiResponse<unknown>>;
    createRemoteInputGrant: (request: MobilePairingRemoteInputGrantRequest) => Promise<ApiResponse<unknown>>;
    revokeRemoteInputGrant: (request: MobilePairingRevokeRemoteInputGrantRequest) => Promise<ApiResponse<unknown>>;
  };
  consent: {
    getStatus: () => Promise<ConsentStatusResult>;
    accept: (request: AcceptConsentRequest) => Promise<ConsentRecord>;
    readDoc: (docId: LegalDocId) => Promise<{ content: string; docId: LegalDocId }>;
  };
  dialog: {
    chooseDirectory: () => Promise<string | null>;
    chooseDocument: () => Promise<string | null>;
    knownFolders: () => Promise<Record<"desktop" | "downloads" | "documents" | "pictures", string | null>>;
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
  credentials: {
    listForSession: (request: CredentialSessionRequest) => Promise<CredentialRef[]>;
    captureFromPage: (request: CredentialSessionRequest) => Promise<CredentialBrokerResult>;
    issueUseTicket: (request: CredentialUseTicketRequest) => Promise<CredentialBrokerResult>;
    fill: (request: CredentialFillRequest) => Promise<CredentialBrokerResult>;
    delete: (request: CredentialRefRequest) => Promise<CredentialBrokerResult>;
  };
  shell: {
    openExternal: (url: string) => Promise<void>;
    getFileIcon: (path: string) => Promise<string | null>;
    showItemInFolder: (path: string) => Promise<FileRevealResult>;
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
