import type { LocalModelInstallRequest } from "./transport";

export function compactLocalModelRequest(request: LocalModelInstallRequest): LocalModelInstallRequest {
  const model = String(request.model ?? "").trim();
  return model ? { model } : {};
}

export {
  mapCommandExecutionResult,
  mapCommandInfo
} from "./commandMappers";

export {
  fileClusterRequestFor,
  mapFileSearchResponse,
  mapIndexStatus,
  mapLocalLibraryItem,
  mapLocalLibraryResponse
} from "./libraryMappers";

export {
  hasRunTimelineEvents,
  latestRunState,
  mapBoundaryEvents,
  mapRunConversation,
  mapRunCreateResponse,
  mapRunEventKind,
  mapRunPlan,
  mapRunTaskEvent,
  mapTaskState,
  runEngineAgentName,
  zhRunEngine
} from "./runMappers";

export {
  mapDocumentAskResponse,
  mapDocumentBlock,
  mapDocumentCitation,
  mapDocumentCompareResponse,
  mapDocumentIR,
  mapDocumentTable
} from "./documentMappers";

export {
  absoluteBackendUrl,
  agentNameFor,
  cleanupPlanFromTimeline,
  dedupeFrames,
  mapAgentKind,
  mapRecordingFrame,
  mapTaskEvent,
  mapTaskRecordings,
  mergeRecording,
  metadataPayloadFor
} from "./taskTimelineMappers";

export {
  mapExplainChainItem,
  mapExplainEvidence,
  mapExplainMessage,
  mapExplainReview,
  mapExplainStep,
  mapTaskExplain
} from "./taskExplainMappers";

export {
  booleanOrUndefined,
  firstNonEmptyString,
  hasCompletedResultEvidence,
  hasTaskCompletionEvidenceInput,
  mapOptionalTaskCompletionEvidence,
  mapTaskCompletionEvidence,
  normalizeCompletionEvidenceKind,
  normalizeTaskCompletionEvidenceStatus,
  taskCompletionEvidenceArtifacts,
  taskCompletionEvidenceLevelFromValue,
  taskCompletionEvidenceMissing,
  taskCompletionEvidenceSummary
} from "./completionEvidenceMappers";

export {
  formatDiffPreview,
  localizeDiffPreview,
  mapApproval,
  mapRiskSeverity
} from "./approvalMappers";

export {
  cleanupDispositionFor,
  cleanupItemsForPlan,
  cleanupPlanFromApprovalPayload,
  cleanupScanRequestFor,
  findCleanupPayload,
  looksLikeCleanupItem,
  looksLikeCleanupPlan,
  mapCleanupExecutionResult,
  mapCleanupItem,
  mapCleanupPlan,
  normalizeCleanupPlan
} from "./cleanupMappers";

export {
  allowedDirectoriesForSettings,
  hasPersistableMcpServerTarget,
  mapMcpServerForBackend,
  mapSettings,
  mergeDesktopOnlySettings,
  normalizePermissionMode,
  sameStringArray,
  settingsPatchFor
} from "./settingsMappers";

export {
  contextHealthFallbackReason,
  contextHealthSeverity,
  contextHealthStatus,
  mapContextUsage,
  mapLlmCostSummary,
  mapLlmHealth,
  mapLlmProfile,
  objectRecord
} from "./llmContextMappers";

export {
  localModelEvidenceValueLabel,
  mapLocalLlmHealth,
  mapLocalModelBundleManifest,
  mapLocalModelEvidenceItem,
  mapLocalModelReadiness,
  mapLocalModelRepairAction,
  mapLocalModelSetupPlan,
  mapLocalModelSetupStepState,
  mapLocalModelVerification
} from "./localModelMappers";

export {
  mapChatMessage,
  mapFileRevealResult,
  mapInstalledApp,
  mapInstalledSkill,
  mapIntentSuggestion,
  mapSkillImportResult,
  mapSkillsCatalog,
  mapSuggestionLaunchResponse,
  normalizeTimestamp
} from "./catalogMappers";

export {
  isBrowserAction,
  mapBrowserActivityEnvelope,
  mapBrowserActivityEvent,
  mapBrowserLink,
  mapBrowserPage,
  mapBrowserReplayExport,
  mapBrowserSession
} from "./browserMappers";

export {
  arrayOfObjects,
  fileNameFromPath,
  numberOrUndefined,
  numberOrZero,
  optionalObjectRecord,
  optionalString,
  recordOrUndefined,
  stringArray,
  tableRowsFromUnknown
} from "./mapperPrimitives";

export {
  emptyBrowserHostSnapshot,
  emptyPlan,
  emptySafetyReview,
  mergeBrowserSessionArrays
} from "./emptyStateMappers";

export {
  allBooleanSignalsMatch,
  externalReviewStatusAllowsSharing,
  mapDiagnostic,
  mapDiagnosticExportResult,
  mapLocalMetrics,
  mapProcess,
  mapStartupItem,
  mapSupportPackageRedaction,
  numberRecord,
  plainRecord
} from "./diagnosticMappers";

export {
  mapHardwareAccelerationComponent,
  mapHardwareAccelerationLlm,
  mapHardwareAccelerationOperation,
  mapHardwareAccelerationSmoke,
  mapHardwareAccelerationStatus,
  mapRuntimePackages,
  mapWinmlStatus,
  normalizeExecutionProvider,
  normalizeHardwareRuntime,
  objectStringRecord
} from "./hardwareAccelerationMappers";
