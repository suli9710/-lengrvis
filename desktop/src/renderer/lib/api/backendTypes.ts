import type {
  ApiMethod,
  ApiRequest,
  ApiQueryValue,
  ApiResponse,
  AuditLogEntry,
  BackendStatus,
  DesktopWebSocketSubscribeRequest
} from "../../../shared/types";
import {
  zhApprovalType,
  zhBackendTaskStatus,
  zhBackendText,
  zhRiskLevel,
  zhSafetyVerdict,
  zhToolName,
  zhUserFacingError
} from "../zh";

export type {
  BackendAppsResponse,
  BackendChatMessage,
  BackendChatRequest,
  BackendChatResponse,
  BackendInstalledApp,
  BackendInstalledSkill,
  BackendIntentSuggestion,
  BackendSkillImportResult,
  BackendSkillRefresh,
  BackendSkillsCatalog,
  BackendSkillSafetyIssue,
  BackendSkillTool
} from "./catalogBackendTypes";
export type {
  BackendAgentMessage,
  BackendApproval,
  BackendBoundaryEvent,
  BackendCommandExecutionResult,
  BackendCommandInfo,
  BackendCommandsResponse,
  BackendEngineCapabilities,
  BackendPlan,
  BackendRealtimeStatusEvent,
  BackendRunCreateRequest,
  BackendRunCreateResponse,
  BackendRunEvent,
  BackendRunState,
  BackendRunStreamEvent,
  BackendRunTimeline,
  BackendSafetyReview,
  BackendStepRecording,
  BackendStepRecordingFrame,
  BackendStepRecordingPayload,
  BackendSuggestionLaunchRequest,
  BackendSuggestionLaunchResponse,
  BackendTask,
  BackendTaskArtifactItem,
  BackendTaskArtifacts,
  BackendTaskCompletionEvidenceFallback,
  BackendTaskExplain,
  BackendTaskExplainChainItem,
  BackendTaskExplainEvidence,
  BackendTaskExplainMessage,
  BackendTaskExplainReview,
  BackendTaskExplainStep,
  BackendTaskStreamEvent,
  BackendTimeline
} from "./executionBackendTypes";
export type {
  BackendCleanupExecuteRequest,
  BackendCleanupExecutionResult,
  BackendCleanupItem,
  BackendCleanupPlan,
  BackendCleanupPlanRequest,
  BackendCleanupRollbackRequest,
  BackendCleanupScanRequest
} from "./cleanupBackendTypes";
export type {
  BackendClusterEntry,
  BackendClusterRequest,
  BackendClusterResponse,
  BackendFileRevealResult,
  BackendFileSearchResponse,
  BackendIndexStatus,
  BackendLocalLibraryItem,
  BackendLocalLibraryResponse
} from "./fileLibraryBackendTypes";
export type {
  BackendSettings,
  SensitiveChangeConfirmation
} from "./settingsBackendTypes";
export type {
  BackendDiagnosticExportResult,
  BackendDisk,
  BackendLocalMetrics,
  BackendProcess,
  BackendProcessesResponse,
  BackendStartupItem,
  BackendStartupResponse,
  BackendSupportPackageRedaction,
  BackendSystemDiagnostics,
  BackendSystemInfo
} from "./systemBackendTypes";
export type {
  BackendContextProjectionSummary,
  BackendContextUsage,
  BackendContextUsageHealth,
  BackendContextUsageLineage,
  BackendContextUsageProjection,
  BackendContextUsageWarning,
  BackendLlmCapabilities,
  BackendLlmCostSummary,
  BackendLlmHealth,
  BackendLlmProfile,
  BackendLlmProfileResponse
} from "./llmContextBackendTypes";
export type {
  BackendLocalLlmBackend,
  BackendLocalLlmHealth,
  BackendLocalModelBundleManifest,
  BackendLocalModelEvidenceItem,
  BackendLocalModelReadiness,
  BackendLocalModelReadinessCheck,
  BackendLocalModelRepairAction,
  BackendLocalModelSetupPlan,
  BackendLocalModelSetupStep,
  BackendLocalModelVerification
} from "./localModelBackendTypes";


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

export interface BackendAuditEvent {
  id: string;
  task_id?: string;
  event_type: string;
  actor: string;
  created_at: string;
}
