import type {
  AuditLogEntry,
  PrivacyEraseResult
} from "../../../shared/types";
import type {
  ChatMessage,
  ChatRequest,
  ChatResponse,
  InstalledApp,
  IntentSuggestion,
  PerceptionSuggestionLaunchRequest,
  PerceptionSuggestionLaunchResponse,
  SkillImportResult,
  SkillsCatalog
} from "../../../shared/catalogTypes";
import type { AppSettings } from "../../../shared/settingsTypes";
import type {
  FileClusterOptions,
  FileRevealResult,
  FileSearchResponse,
  LocalLibraryResponse
} from "../../../shared/fileLibraryTypes";
import type {
  BrowserAction,
  BrowserActivityEvent,
  BrowserHostActionResult,
  BrowserHostOpenRequest,
  BrowserHostSnapshot,
  BrowserLinkResult,
  BrowserPageSnapshot,
  BrowserReplayExport,
  BrowserSession
} from "../../../shared/browserTypes";
import type { CommerceLicenseStatus, CommercePlanStatus, CommerceQuotaStatus } from "../../../shared/commerceTypes";
import type {
  CleanupExecutionResult,
  CleanupExecuteRequest,
  CleanupPlan,
  CleanupPlanRequest,
  CleanupRollbackRequest,
  CleanupScanRequest
} from "../../../shared/cleanupTypes";
import type {
  ApiRequest,
  ApiResponse,
  BackendStatus,
  DesktopPrivacyEraseRequest
} from "../../../shared/desktopBridgeTypes";
import type {
  DocumentAskRequest,
  DocumentAskResponse,
  DocumentCompareRequest,
  DocumentCompareResponse,
  DocumentIR,
  DocumentParseRequest
} from "../../../shared/documentTypes";
import type {
  AgentConversation,
  ApprovalDecision,
  ApprovalRequest,
  CommandExecutionResult,
  CommandInfo,
  Plan,
  SafetyReview,
  TaskArtifactsSummary,
  TaskEvent,
  TaskExplain
} from "../../../shared/executionTypes";
import type { HardwareAccelerationSmokePayload, HardwareAccelerationStatusPayload } from "../../../shared/hardwareAccelerationTypes";
import type { ContextUsage, LLMCostSummary, LLMHealthStatus, LLMProfile } from "../../../shared/llmContextTypes";
import type { LocalLLMHealth, LocalModelSetupPlan } from "../../../shared/localModelTypes";
import type {
  DiagnosticExportResult,
  LocalMetricsSummary,
  SystemInfo
} from "../../../shared/systemTypes";
import type { JsonRealtimeHandlers } from "./realtimeTransport";
import type { LocalModelInstallRequest, LocalModelInstallResponse, OllamaActionResponse } from "./transport";
import type { BackendPermissionPolicy, BackendPermissionRule } from "./backendTypes";
import type { BackendBrowserSessionStreamEvent } from "./browserBackendTypes";
import type {
  BackendClusterResponse
} from "./fileLibraryBackendTypes";
import type { SensitiveChangeConfirmation } from "./settingsBackendTypes";
import type {
  BackendRunStreamEvent,
  BackendRunTimeline,
  BackendTaskStreamEvent
} from "./executionBackendTypes";
import type { HardwareAccelerationSmokeRequest } from "./hardwareAccelerationBackendTypes";
import { RendererApiRequestSession } from "./apiRequestSession";
import {
  getBackendStatusEndpoint,
  probeBackendHealthEndpoint,
  startBackendEndpoint,
  stopBackendEndpoint
} from "./backendLifecycleClient";
import {
  browserSessionCommandEndpoint,
  exportBrowserReplayEndpoint,
  getBrowserHostSnapshotEndpoint,
  getBrowserLinksEndpoint,
  hideBrowserHostEndpoint,
  listBrowserSessionEventsEndpoint,
  listBrowserSessionsEndpoint,
  observeBrowserSessionEndpoint,
  openBrowserHostEndpoint,
  pauseBrowserHostEndpoint,
  performBrowserHostActionEndpoint,
  readBrowserPageEndpoint,
  releaseBrowserHostEndpoint,
  resumeBrowserHostEndpoint,
  setBrowserHostBoundsEndpoint,
  showBrowserHostEndpoint,
  stopBrowserHostEndpoint,
  subscribeBrowserHostSnapshotsEndpoint,
  subscribeBrowserSessionEndpoint,
  takeoverBrowserHostEndpoint
} from "./browserClient";
import {
  importSkillEndpoint,
  launchPerceptionSuggestionEndpoint,
  listChatMessagesEndpoint,
  listIntentSuggestionsEndpoint,
  listSkillsEndpoint,
  refreshSkillsEndpoint,
  sendChatEndpoint
} from "./catalogClient";
import {
  activateCommerceLicenseEndpoint,
  getCommerceLicenseEndpoint,
  getCommercePlanEndpoint,
  getCommerceQuotaEndpoint,
  installCommerceLicenseEndpoint
} from "./commerceClient";
import {
  executeCleanupEndpoint,
  planCleanupEndpoint,
  rollbackCleanupEndpoint,
  scanCleanupEndpoint
} from "./cleanupClient";
import {
  askDocumentEndpoint,
  compareDocumentsEndpoint,
  parseDocumentEndpoint
} from "./documentClient";
import {
  executeCommandEndpoint,
  executeRollbackEndpoint,
  getCurrentPlanEndpoint,
  getRunTimelineEndpoint,
  getSafetyReviewEndpoint,
  getTaskExplainEndpoint,
  getTaskTimelineEventEndpoint,
  listAgentConversationsEndpoint,
  listCommandsEndpoint,
  listPendingApprovalsEndpoint,
  listRunsEndpoint,
  listTaskArtifactsEndpoint,
  listTaskTimelineEndpoint,
  previewRollbackEndpoint,
  startRunEndpoint,
  submitApprovalDecisionEndpoint,
  subscribeRunEventsEndpoint,
  subscribeTaskMessagesEndpoint,
  taskLifecycleEndpoint
} from "./executionClient";
import {
  getHardwareAccelerationStatusEndpoint,
  runHardwareAccelerationSmokeEndpoint
} from "./hardwareAccelerationClient";
import {
  clusterFilesEndpoint,
  listLocalLibraryEndpoint,
  revealFileEndpoint,
  searchFilesEndpoint,
  showItemInFolderEndpoint
} from "./fileLibraryClient";
import {
  getContextUsageEndpoint,
  getLlmCostSummaryEndpoint,
  getLlmHealthEndpoint,
  getLlmProfileEndpoint
} from "./llmContextClient";
import {
  getLocalLlmHealthEndpoint,
  getLocalModelSetupPlanEndpoint,
  installLocalModelEndpoint,
  installOllamaEndpoint,
  pullOllamaEndpoint,
  startOllamaEndpoint
} from "./localModelClient";
import {
  forgetMemoryEndpoint,
  listMemoriesEndpoint,
  recallMemoryEndpoint,
  saveMemoryEndpoint
} from "./memoryClient";
import type { RecallMemoryOptions, SaveMemoryOptions } from "./memoryClient";
import type { BackendMemory } from "./memoryBackendTypes";
import {
  createMobilePairingCodeEndpoint,
  createRemoteInputGrantEndpoint,
  listMobileDevicesEndpoint,
  revokeMobileDeviceEndpoint,
  revokeRemoteInputGrantEndpoint
} from "./mobilePairingClient";
import type {
  MobileDevice,
  MobileDeviceList,
  MobilePairingCode,
  RemoteInputGrant,
  RemoteInputGrantIssueResult
} from "./mobilePairingBackendTypes";
import {
  confirmPermissionRuleChangeEndpoint,
  confirmPermissionRuleDeleteEndpoint,
  deletePermissionRuleEndpoint,
  getSettingsEndpoint,
  saveSettingsEndpoint,
  upsertPermissionRuleEndpoint
} from "./settingsClient";
import {
  createScheduleEndpoint,
  deleteScheduleEndpoint,
  enableScheduleEndpoint,
  listSchedulesEndpoint
} from "./scheduleClient";
import type { ScheduleInput } from "./scheduleClient";
import type { BackendScheduledTask } from "./scheduleBackendTypes";
import {
  eraseLocalDataEndpoint,
  exportDiagnosticsPackageEndpoint,
  getLocalMetricsEndpoint,
  getSystemInfoEndpoint,
  listAppsEndpoint,
  listAuditLogsEndpoint,
  openWindowsSettingsEndpoint
} from "./systemClient";

export class LengrvisApiClient {
  private lastLoadedSettings: AppSettings | null = null;
  private readonly requestSession = new RendererApiRequestSession();
  private readonly requestEndpoint = <TResponse, TBody = unknown>(
    request: ApiRequest<TBody>
  ) => this.request<TResponse, TBody>(request);
  private readonly settingsEndpointState = {
    getLastLoadedSettings: () => this.lastLoadedSettings,
    setLastLoadedSettings: (settings: AppSettings) => {
      this.lastLoadedSettings = settings;
    }
  };

  async abortInflight(abortGroup: string): Promise<void> {
    return this.requestSession.abortInflight(abortGroup);
  }

  async beginBatch(abortGroup: string): Promise<void> {
    return this.requestSession.beginBatch(abortGroup);
  }

  endBatch(abortGroup: string): void {
    this.requestSession.endBatch(abortGroup);
  }

  async request<TResponse, TBody = unknown>(request: ApiRequest<TBody>): Promise<ApiResponse<TResponse>> {
    return this.requestSession.request<TResponse, TBody>(request);
  }

  async getBackendStatus(): Promise<BackendStatus> {
    return getBackendStatusEndpoint(this.requestEndpoint);
  }

  async getCommercePlan(): Promise<ApiResponse<CommercePlanStatus>> {
    return getCommercePlanEndpoint(this.requestEndpoint);
  }

  async getCommerceLicense(): Promise<ApiResponse<CommerceLicenseStatus>> {
    return getCommerceLicenseEndpoint(this.requestEndpoint);
  }

  async getCommerceQuota(): Promise<ApiResponse<CommerceQuotaStatus>> {
    return getCommerceQuotaEndpoint(this.requestEndpoint);
  }

  async installCommerceLicense(token: string): Promise<ApiResponse<CommerceLicenseStatus>> {
    return installCommerceLicenseEndpoint(this.requestEndpoint, token);
  }

  async activateCommerceLicense(activationKey: string, appVersion = "desktop"): Promise<ApiResponse<CommerceLicenseStatus>> {
    return activateCommerceLicenseEndpoint(this.requestEndpoint, activationKey, appVersion);
  }

  async probeBackendHealth(baseUrl?: string): Promise<BackendStatus | null> {
    return probeBackendHealthEndpoint(baseUrl);
  }

  startBackend(): Promise<BackendStatus> {
    return startBackendEndpoint(() => this.getBackendStatus());
  }

  stopBackend(): Promise<BackendStatus> {
    return stopBackendEndpoint(() => this.getBackendStatus());
  }

  listChatMessages(): Promise<ApiResponse<ChatMessage[]>> {
    return listChatMessagesEndpoint(this.requestEndpoint);
  }

  sendChat(body: ChatRequest): Promise<ApiResponse<ChatResponse>> {
    return sendChatEndpoint(this.requestEndpoint, body);
  }

  listIntentSuggestions(): Promise<ApiResponse<IntentSuggestion[]>> {
    return listIntentSuggestionsEndpoint(this.requestEndpoint);
  }

  async launchPerceptionSuggestion(
    body: PerceptionSuggestionLaunchRequest
  ): Promise<ApiResponse<PerceptionSuggestionLaunchResponse>> {
    return launchPerceptionSuggestionEndpoint(this.requestEndpoint, body);
  }

  startRun(body: ChatRequest): Promise<ApiResponse<ChatResponse>> {
    return startRunEndpoint(this.requestEndpoint, body);
  }

  listRuns(): Promise<ApiResponse<TaskEvent[]>> {
    return listRunsEndpoint(this.requestEndpoint);
  }

  getRunTimeline(runId: string): Promise<ApiResponse<BackendRunTimeline>> {
    return getRunTimelineEndpoint(this.requestEndpoint, runId);
  }

  async listTaskTimeline(): Promise<ApiResponse<TaskEvent[]>> {
    return listTaskTimelineEndpoint(this.requestEndpoint);
  }

  async getTaskTimelineEvent(taskId: string): Promise<ApiResponse<TaskEvent>> {
    return getTaskTimelineEventEndpoint(this.requestEndpoint, taskId);
  }

  getVoiceHealth(): Promise<ApiResponse<{ available: boolean; provider: string; detail: string }>> {
    return this.request<{ available: boolean; provider: string; detail: string }>({
      endpoint: "/api/perception/voice/health",
      timeoutMs: 2500
    });
  }

  transcribeVoice(body: { audioBase64: string; sampleRate: number; language?: string }): Promise<
    ApiResponse<{ transcript: string; confidence: number | null; language: string; provider: string }>
  > {
    return this.request<
      { transcript: string; confidence: number | null; language: string; provider: string },
      { audio_base64: string; sample_rate: number; language?: string }
    >({
      endpoint: "/api/perception/voice/transcribe",
      method: "POST",
      timeoutMs: 30_000,
      body: {
        audio_base64: body.audioBase64,
        sample_rate: body.sampleRate,
        ...(body.language ? { language: body.language } : {})
      }
    });
  }

  listTaskArtifacts(taskId: string): Promise<ApiResponse<TaskArtifactsSummary>> {
    return listTaskArtifactsEndpoint(this.requestEndpoint, taskId);
  }

  getLocalMetrics(days = 7): Promise<ApiResponse<LocalMetricsSummary>> {
    return getLocalMetricsEndpoint(this.requestEndpoint, days);
  }

  async getCurrentPlan(): Promise<ApiResponse<Plan>> {
    return getCurrentPlanEndpoint(this.requestEndpoint);
  }

  async listAgentConversations(): Promise<ApiResponse<AgentConversation[]>> {
    return listAgentConversationsEndpoint(this.requestEndpoint);
  }

  subscribeTaskMessages(
    taskId: string,
    handlers: JsonRealtimeHandlers<BackendTaskStreamEvent>
  ): () => void {
    return subscribeTaskMessagesEndpoint(taskId, handlers);
  }

  getSafetyReview(): Promise<ApiResponse<SafetyReview>> {
    return getSafetyReviewEndpoint(this.requestEndpoint);
  }

  listPendingApprovals(): Promise<ApiResponse<ApprovalRequest[]>> {
    return listPendingApprovalsEndpoint(this.requestEndpoint);
  }

  submitApprovalDecision(decision: ApprovalDecision): Promise<ApiResponse<ApprovalRequest>> {
    return submitApprovalDecisionEndpoint(this.requestEndpoint, decision);
  }

  listCommands(): Promise<ApiResponse<CommandInfo[]>> {
    return listCommandsEndpoint(this.requestEndpoint);
  }

  executeCommand(name: string, args: Record<string, unknown> = {}): Promise<ApiResponse<CommandExecutionResult>> {
    return executeCommandEndpoint(this.requestEndpoint, name, args);
  }

  createMobilePairingCode(): Promise<ApiResponse<MobilePairingCode>> {
    return createMobilePairingCodeEndpoint(this.requestEndpoint);
  }

  listMobileDevices(): Promise<ApiResponse<MobileDeviceList>> {
    return listMobileDevicesEndpoint(this.requestEndpoint);
  }

  revokeMobileDevice(deviceId: string): Promise<ApiResponse<MobileDevice>> {
    return revokeMobileDeviceEndpoint(this.requestEndpoint, deviceId);
  }

  createRemoteInputGrant(deviceId: string, expiresInSeconds = 300): Promise<ApiResponse<RemoteInputGrantIssueResult>> {
    return createRemoteInputGrantEndpoint(this.requestEndpoint, deviceId, expiresInSeconds);
  }

  revokeRemoteInputGrant(deviceId: string, grantId: string): Promise<ApiResponse<RemoteInputGrant>> {
    return revokeRemoteInputGrantEndpoint(this.requestEndpoint, deviceId, grantId);
  }

  searchFiles(query: string): Promise<ApiResponse<FileSearchResponse>> {
    return searchFilesEndpoint(this.requestEndpoint, query);
  }

  listLocalLibrary(section: string, query = "", limit = 240): Promise<ApiResponse<LocalLibraryResponse>> {
    return listLocalLibraryEndpoint(this.requestEndpoint, section, query, limit);
  }

  getSettings(): Promise<ApiResponse<AppSettings>> {
    return getSettingsEndpoint(this.requestEndpoint, this.settingsEndpointState);
  }

  getLocalLlmHealth(): Promise<ApiResponse<LocalLLMHealth>> {
    return getLocalLlmHealthEndpoint(this.requestEndpoint);
  }

  getLocalModelSetupPlan(model?: string): Promise<ApiResponse<LocalModelSetupPlan>> {
    return getLocalModelSetupPlanEndpoint(this.requestEndpoint, model);
  }

  installLocalModel(request: LocalModelInstallRequest = {}): Promise<ApiResponse<LocalModelInstallResponse>> {
    return installLocalModelEndpoint(this.requestEndpoint, request);
  }

  installOllama(): Promise<ApiResponse<OllamaActionResponse>> {
    return installOllamaEndpoint(this.requestEndpoint);
  }

  startOllama(): Promise<ApiResponse<OllamaActionResponse>> {
    return startOllamaEndpoint(this.requestEndpoint);
  }

  pullOllama(model?: string): Promise<ApiResponse<OllamaActionResponse>> {
    return pullOllamaEndpoint(this.requestEndpoint, model);
  }

  getLlmHealth(): Promise<ApiResponse<LLMHealthStatus>> {
    return getLlmHealthEndpoint(this.requestEndpoint);
  }

  getLlmProfile(): Promise<ApiResponse<LLMProfile>> {
    return getLlmProfileEndpoint(this.requestEndpoint);
  }

  getLlmCostSummary(): Promise<ApiResponse<LLMCostSummary>> {
    return getLlmCostSummaryEndpoint(this.requestEndpoint);
  }

  getHardwareAccelerationStatus(): Promise<ApiResponse<HardwareAccelerationStatusPayload>> {
    return getHardwareAccelerationStatusEndpoint(this.requestEndpoint);
  }

  async runHardwareAccelerationSmoke(
    payload: HardwareAccelerationSmokeRequest = {}
  ): Promise<ApiResponse<HardwareAccelerationSmokePayload>> {
    return runHardwareAccelerationSmokeEndpoint(this.requestEndpoint, payload);
  }

  getContextUsage(taskId?: string): Promise<ApiResponse<ContextUsage>> {
    return getContextUsageEndpoint(this.requestEndpoint, taskId);
  }

  async saveSettings(settings: AppSettings): Promise<ApiResponse<AppSettings>> {
    return saveSettingsEndpoint(this.requestEndpoint, this.settingsEndpointState, settings);
  }

  async confirmPermissionRuleChange(rule: BackendPermissionRule): Promise<ApiResponse<SensitiveChangeConfirmation>> {
    return confirmPermissionRuleChangeEndpoint(this.requestEndpoint, rule);
  }

  async confirmPermissionRuleDelete(ruleId: string): Promise<ApiResponse<SensitiveChangeConfirmation>> {
    return confirmPermissionRuleDeleteEndpoint(this.requestEndpoint, ruleId);
  }

  upsertPermissionRule(rule: BackendPermissionRule, confirmationNonce?: string): Promise<ApiResponse<BackendPermissionPolicy>> {
    return upsertPermissionRuleEndpoint(this.requestEndpoint, rule, confirmationNonce);
  }

  deletePermissionRule(ruleId: string, confirmationNonce?: string): Promise<ApiResponse<{ ok: boolean; policy: BackendPermissionPolicy }>> {
    return deletePermissionRuleEndpoint(this.requestEndpoint, ruleId, confirmationNonce);
  }

  listAuditLogs(): Promise<ApiResponse<AuditLogEntry[]>> {
    return listAuditLogsEndpoint(this.requestEndpoint);
  }

  getSystemInfo(): Promise<ApiResponse<SystemInfo>> {
    return getSystemInfoEndpoint(this.requestEndpoint);
  }

  exportDiagnosticsPackage(): Promise<ApiResponse<DiagnosticExportResult>> {
    return exportDiagnosticsPackageEndpoint(this.requestEndpoint);
  }

  eraseLocalData(request: DesktopPrivacyEraseRequest): Promise<ApiResponse<PrivacyEraseResult>> {
    return eraseLocalDataEndpoint(request);
  }

  listApps(): Promise<ApiResponse<InstalledApp[]>> {
    return listAppsEndpoint(this.requestEndpoint);
  }

  revealFile(path: string): Promise<ApiResponse<FileRevealResult>> {
    return revealFileEndpoint(this.requestEndpoint, path);
  }

  async showItemInFolder(path: string): Promise<ApiResponse<FileRevealResult>> {
    return showItemInFolderEndpoint(this.requestEndpoint, path);
  }

  parseDocument(body: DocumentParseRequest): Promise<ApiResponse<DocumentIR>> {
    return parseDocumentEndpoint(this.requestEndpoint, body);
  }

  askDocument(body: DocumentAskRequest): Promise<ApiResponse<DocumentAskResponse>> {
    return askDocumentEndpoint(this.requestEndpoint, body);
  }

  compareDocuments(body: DocumentCompareRequest): Promise<ApiResponse<DocumentCompareResponse>> {
    return compareDocumentsEndpoint(this.requestEndpoint, body);
  }

  scanCleanup(body: CleanupScanRequest = {}): Promise<ApiResponse<CleanupPlan>> {
    return scanCleanupEndpoint(this.requestEndpoint, body);
  }

  planCleanup(body: CleanupPlanRequest = {}): Promise<ApiResponse<CleanupPlan>> {
    return planCleanupEndpoint(this.requestEndpoint, body);
  }

  executeCleanup(body: CleanupExecuteRequest): Promise<ApiResponse<CleanupExecutionResult>> {
    return executeCleanupEndpoint(this.requestEndpoint, body);
  }

  rollbackCleanup(body: CleanupRollbackRequest): Promise<ApiResponse<CleanupExecutionResult>> {
    return rollbackCleanupEndpoint(this.requestEndpoint, body);
  }

  readBrowserPage(url: string): Promise<ApiResponse<BrowserPageSnapshot>> {
    return readBrowserPageEndpoint(this.requestEndpoint, url);
  }

  getBrowserLinks(url: string): Promise<ApiResponse<BrowserLinkResult[]>> {
    return getBrowserLinksEndpoint(this.requestEndpoint, url);
  }

  async listBrowserSessions(): Promise<ApiResponse<BrowserSession[]>> {
    return listBrowserSessionsEndpoint(this.requestEndpoint);
  }

  async listBrowserSessionEvents(sessionId: string, limit = 200): Promise<ApiResponse<BrowserActivityEvent[]>> {
    return listBrowserSessionEventsEndpoint(this.requestEndpoint, sessionId, limit);
  }

  async observeBrowserSession(sessionId: string): Promise<ApiResponse<BrowserActivityEvent>> {
    return observeBrowserSessionEndpoint(this.requestEndpoint, sessionId);
  }

  pauseBrowserSession(sessionId: string): Promise<ApiResponse<BrowserSession>> {
    return this.browserSessionCommand(sessionId, "pause");
  }

  resumeBrowserSession(sessionId: string): Promise<ApiResponse<BrowserSession>> {
    return this.browserSessionCommand(sessionId, "resume");
  }

  takeoverBrowserSession(sessionId: string): Promise<ApiResponse<BrowserSession>> {
    return this.browserSessionCommand(sessionId, "takeover");
  }

  releaseBrowserSession(sessionId: string): Promise<ApiResponse<BrowserSession>> {
    return this.browserSessionCommand(sessionId, "release");
  }

  private browserSessionCommand(
    sessionId: string,
    command: "pause" | "resume" | "takeover" | "release"
  ): Promise<ApiResponse<BrowserSession>> {
    return browserSessionCommandEndpoint(sessionId, command);
  }

  async exportBrowserReplay(sessionId: string): Promise<ApiResponse<BrowserReplayExport>> {
    return exportBrowserReplayEndpoint(this.requestEndpoint, sessionId);
  }

  subscribeBrowserSession(
    sessionId: string,
    handlers: {
      onMessage: (message: BackendBrowserSessionStreamEvent) => void;
      onError?: (error: Event) => void;
      onOpen?: () => void;
    }
  ): () => void {
    return subscribeBrowserSessionEndpoint(sessionId, handlers);
  }

  getBrowserHostSnapshot(): Promise<BrowserHostSnapshot> {
    return getBrowserHostSnapshotEndpoint();
  }

  openBrowserHost(request: BrowserHostOpenRequest): Promise<BrowserHostActionResult> {
    return openBrowserHostEndpoint(request);
  }

  showBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return showBrowserHostEndpoint(sessionId);
  }

  hideBrowserHost(): Promise<BrowserHostActionResult> {
    return hideBrowserHostEndpoint();
  }

  setBrowserHostBounds(bounds: { x: number; y: number; width: number; height: number }): Promise<BrowserHostActionResult> {
    return setBrowserHostBoundsEndpoint(bounds);
  }

  pauseBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return pauseBrowserHostEndpoint(sessionId);
  }

  resumeBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return resumeBrowserHostEndpoint(sessionId);
  }

  takeoverBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return takeoverBrowserHostEndpoint(sessionId);
  }

  releaseBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return releaseBrowserHostEndpoint(sessionId);
  }

  stopBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return stopBrowserHostEndpoint(sessionId);
  }

  performBrowserHostAction(sessionId: string, action: BrowserAction): Promise<BrowserHostActionResult> {
    return performBrowserHostActionEndpoint(sessionId, action);
  }

  subscribeBrowserHostSnapshots(handler: (snapshot: BrowserHostSnapshot) => void): () => void {
    return subscribeBrowserHostSnapshotsEndpoint(handler);
  }

  openWindowsSettings(uri: string): Promise<ApiResponse<{ ok: boolean; uri: string; opened?: boolean; error?: string }>> {
    return openWindowsSettingsEndpoint(this.requestEndpoint, uri);
  }

  listSchedules(): Promise<ApiResponse<BackendScheduledTask[]>> {
    return listSchedulesEndpoint(this.requestEndpoint);
  }

  createSchedule(input: ScheduleInput): Promise<ApiResponse<BackendScheduledTask>> {
    return createScheduleEndpoint(this.requestEndpoint, input);
  }

  deleteSchedule(scheduleId: string): Promise<ApiResponse<{ ok: boolean; id: string }>> {
    return deleteScheduleEndpoint(this.requestEndpoint, scheduleId);
  }

  enableSchedule(scheduleId: string, enabled: boolean): Promise<ApiResponse<BackendScheduledTask>> {
    return enableScheduleEndpoint(this.requestEndpoint, scheduleId, enabled);
  }

  listMemories(): Promise<ApiResponse<BackendMemory[]>> {
    return listMemoriesEndpoint(this.requestEndpoint);
  }

  saveMemory(content: string, options: SaveMemoryOptions = {}): Promise<ApiResponse<BackendMemory>> {
    return saveMemoryEndpoint(this.requestEndpoint, content, options);
  }

  recallMemory(query: string, options: RecallMemoryOptions = {}): Promise<ApiResponse<BackendMemory[]>> {
    return recallMemoryEndpoint(this.requestEndpoint, query, options);
  }

  forgetMemory(memoryId: string): Promise<ApiResponse<{ ok: boolean; id: string }>> {
    return forgetMemoryEndpoint(this.requestEndpoint, memoryId);
  }

  previewRollback(taskId: string): Promise<ApiResponse<{ task_id: string; steps: unknown[]; count: number }>> {
    return previewRollbackEndpoint(this.requestEndpoint, taskId);
  }

  executeRollback(taskId: string): Promise<ApiResponse<{
    executed: unknown[];
    count: number;
    state: string;
    attempted: number;
    succeeded: number;
    verified: number;
    verification_failed: number;
    failed: number;
    manual_required: number;
    unrecoverable: number;
  }>> {
    return executeRollbackEndpoint(this.requestEndpoint, taskId);
  }

  pauseTask(taskId: string): Promise<ApiResponse<unknown>> {
    return taskLifecycleEndpoint(this.requestEndpoint, taskId, "pause");
  }

  resumeTask(taskId: string): Promise<ApiResponse<unknown>> {
    return taskLifecycleEndpoint(this.requestEndpoint, taskId, "resume");
  }

  cancelTask(taskId: string): Promise<ApiResponse<unknown>> {
    return taskLifecycleEndpoint(this.requestEndpoint, taskId, "cancel");
  }

  subscribeRunEvents(
    runId: string,
    handlers: JsonRealtimeHandlers<BackendRunStreamEvent>
  ): () => void {
    return subscribeRunEventsEndpoint(runId, handlers);
  }

  getTaskExplain(taskId: string): Promise<ApiResponse<TaskExplain>> {
    return getTaskExplainEndpoint(this.requestEndpoint, taskId);
  }

  clusterFiles(options: FileClusterOptions = {}): Promise<ApiResponse<BackendClusterResponse>> {
    return clusterFilesEndpoint(this.requestEndpoint, options);
  }

  listSkills(): Promise<ApiResponse<SkillsCatalog>> {
    return listSkillsEndpoint(this.requestEndpoint);
  }

  importSkill(path: string): Promise<ApiResponse<SkillImportResult>> {
    return importSkillEndpoint(this.requestEndpoint, path);
  }

  refreshSkills(): Promise<ApiResponse<{ ok: boolean; toolCount: number; skillCount: number }>> {
    return refreshSkillsEndpoint(this.requestEndpoint);
  }
}
