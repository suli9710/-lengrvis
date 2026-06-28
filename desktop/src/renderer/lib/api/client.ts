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
  CommerceFeature,
  CommerceLicenseStatus,
  CommercePlan,
  CommercePlanStatus,
  CommerceQuotaStatus,
  ContextUsage,
  DesktopWebSocketSubscribeRequest,
  DesktopPrivacyEraseRequest,
  DesktopPrivacyEraseResponse,
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
  PrivacyEraseResult,
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
import { FALLBACK_BACKEND_URL, emitRendererApiRequestEvent, getBackendBaseUrl, mapResponse, rendererBatchControllers, requestBackendDirect, subscribeJsonRealtime } from "./transport";
import type { JsonRealtimeHandlers, LocalModelInstallRequest, LocalModelInstallResponse, OllamaActionResponse } from "./transport";
import type { BackendAgentMessage, BackendApproval, BackendAppsResponse, BackendAuditEvent, BackendBrowserActivityEnvelope, BackendBrowserEvents, BackendBrowserLinks, BackendBrowserPage, BackendBrowserReplayExport, BackendBrowserSessionStreamEvent, BackendBrowserSessions, BackendChatMessage, BackendChatRequest, BackendChatResponse, BackendCleanupExecuteRequest, BackendCleanupExecutionResult, BackendCleanupPlan, BackendCleanupPlanRequest, BackendCleanupRollbackRequest, BackendCleanupScanRequest, BackendClusterRequest, BackendClusterResponse, BackendCommandExecutionResult, BackendCommandsResponse, BackendCommerceLicenseStatus, BackendCommercePlanStatus, BackendCommerceQuotaStatus, BackendContextUsage, BackendDiagnosticExportResult, BackendDocumentAskRequest, BackendDocumentAskResponse, BackendDocumentCompareRequest, BackendDocumentCompareResponse, BackendDocumentIR, BackendDocumentParseRequest, BackendFileRevealResult, BackendFileSearchResponse, BackendHardwareAccelerationSmoke, BackendHardwareAccelerationStatus, BackendIntentSuggestion, BackendLlmCostSummary, BackendLlmHealth, BackendLlmProfileResponse, BackendLocalLibraryResponse, BackendLocalLlmHealth, BackendLocalMetrics, BackendLocalModelSetupPlan, BackendMemory, BackendPermissionPolicy, BackendPermissionRule, BackendPlan, BackendProcessesResponse, BackendRunCreateRequest, BackendRunCreateResponse, BackendRunState, BackendRunStreamEvent, BackendRunTimeline, BackendSafetyReview, BackendScheduledTask, BackendSettings, BackendSkillImportResult, BackendSkillRefresh, BackendSkillsCatalog, BackendStartupResponse, BackendSuggestionLaunchRequest, BackendSuggestionLaunchResponse, BackendSystemDiagnostics, BackendSystemInfo, BackendTask, BackendTaskArtifacts, BackendTaskExplain, BackendTaskStreamEvent, BackendTimeline, BrowserReplayExport, FileClusterOptions, HardwareAccelerationSmokeRequest, HardwareAccelerationSmokeRequestBody, MobileDevice, MobileDeviceList, MobilePairingCode, RemoteInputGrant, RemoteInputGrantIssueResult, SensitiveChangeConfirmation } from "./backendTypes";
import { agentNameFor, cleanupPlanFromTimeline, cleanupScanRequestFor, compactLocalModelRequest, emptyBrowserHostSnapshot, emptyPlan, emptySafetyReview, hasRunTimelineEvents, latestRunState, mapAgentKind, mapApproval, mapBoundaryEvents, mapBrowserActivityEnvelope, mapBrowserActivityEvent, mapBrowserLink, mapBrowserPage, mapBrowserReplayExport, mapBrowserSession, mapChatMessage, mapCleanupExecutionResult, mapCleanupPlan, mapCommandExecutionResult, mapCommandInfo, mapContextUsage, mapDiagnostic, mapDiagnosticExportResult, mapDocumentAskResponse, mapDocumentCompareResponse, mapDocumentIR, mapFileRevealResult, mapHardwareAccelerationSmoke, mapHardwareAccelerationStatus, mapIndexStatus, mapInstalledApp, mapIntentSuggestion, mapLlmCostSummary, mapLlmHealth, mapLlmProfile, mapLocalLibraryResponse, mapLocalLlmHealth, mapLocalModelSetupPlan, mapProcess, mapRiskSeverity, mapRunConversation, mapRunPlan, mapRunTaskEvent, mapSettings, mapSkillImportResult, mapSkillsCatalog, mapStartupItem, mapSuggestionLaunchResponse, mapTaskEvent, mapTaskExplain, mapTaskRecordings, mapTaskState, mergeBrowserSessionArrays, mergeDesktopOnlySettings, metadataPayloadFor, numberOrZero, runEngineAgentName, settingsPatchFor } from "./mappers";

export class LengrvisApiClient {
  private lastLoadedSettings: AppSettings | null = null;
  private activeAbortGroup: string | null = null;
  private activeBatchStack: string[] = [];

  async abortInflight(abortGroup: string): Promise<void> {
    rendererBatchControllers.get(abortGroup)?.abort();
    rendererBatchControllers.delete(abortGroup);
    await window.lengrvis?.api.abortInflight(abortGroup);
  }

  async beginBatch(abortGroup: string): Promise<void> {
    await this.abortInflight(abortGroup);
    this.activeBatchStack.push(abortGroup);
    this.activeAbortGroup = abortGroup;
    rendererBatchControllers.set(abortGroup, new AbortController());
  }

  endBatch(abortGroup: string): void {
    const index = this.activeBatchStack.lastIndexOf(abortGroup);
    if (index >= 0) {
      this.activeBatchStack.splice(index, 1);
    }
    this.activeAbortGroup = this.activeBatchStack[this.activeBatchStack.length - 1] ?? null;
  }

  async request<TResponse, TBody = unknown>(request: ApiRequest<TBody>): Promise<ApiResponse<TResponse>> {
    const abortGroup = request.abortGroup ?? this.activeAbortGroup ?? undefined;
    const enrichedRequest = abortGroup ? { ...request, abortGroup } : request;
    emitRendererApiRequestEvent(enrichedRequest);
    if (!window.lengrvis) {
      return requestBackendDirect<TResponse, TBody>(FALLBACK_BACKEND_URL, enrichedRequest);
    }

    return window.lengrvis.api.request<TResponse, TBody>(enrichedRequest);
  }

  async getBackendStatus(): Promise<BackendStatus> {
    if (!window.lengrvis) {
      const startedAt = Date.now();
      const health = await this.request<{ status: string }>({ endpoint: "/api/health", timeoutMs: 1500 });
      return {
        state: health.ok ? "running" : "stopped",
        baseUrl: FALLBACK_BACKEND_URL,
        message: health.ok ? "后端已连接" : "等待后端连接",
        lastCheckedAt: new Date().toISOString(),
        health: {
          ok: health.ok,
          latencyMs: Date.now() - startedAt
        }
      };
    }
    return window.lengrvis.backend.getStatus();
  }

  async getCommercePlan(): Promise<ApiResponse<CommercePlanStatus>> {
    return this.request<BackendCommercePlanStatus>({ endpoint: "/api/commerce/plan" }).then((response) =>
      mapResponse(response, mapCommercePlanStatus)
    );
  }

  async getCommerceLicense(): Promise<ApiResponse<CommerceLicenseStatus>> {
    return this.request<BackendCommerceLicenseStatus>({ endpoint: "/api/commerce/license" }).then((response) =>
      mapResponse(response, mapCommerceLicenseStatus)
    );
  }

  async getCommerceQuota(): Promise<ApiResponse<CommerceQuotaStatus>> {
    return this.request<BackendCommerceQuotaStatus>({ endpoint: "/api/commerce/usage/quota" }).then((response) =>
      mapResponse(response, mapCommerceQuotaStatus)
    );
  }

  async installCommerceLicense(token: string): Promise<ApiResponse<CommerceLicenseStatus>> {
    return this.request<BackendCommerceLicenseStatus, { token: string }>({
      endpoint: "/api/commerce/license/install",
      method: "POST",
      body: { token }
    }).then((response) => mapResponse(response, mapCommerceLicenseStatus));
  }

  async activateCommerceLicense(activationKey: string, appVersion = "desktop"): Promise<ApiResponse<CommerceLicenseStatus>> {
    return this.request<BackendCommerceLicenseStatus, { activation_key: string; app_version: string }>({
      endpoint: "/api/commerce/license/activate",
      method: "POST",
      body: { activation_key: activationKey, app_version: appVersion }
    }).then((response) => mapResponse(response, mapCommerceLicenseStatus));
  }

  async probeBackendHealth(baseUrl?: string): Promise<BackendStatus | null> {
    const startedAt = Date.now();
    const backendBaseUrl = getBackendBaseUrl(baseUrl);
    const health = await requestBackendDirect<{ status?: string }>(backendBaseUrl, {
      endpoint: "/api/health",
      timeoutMs: 1500
    });
    if (!health.ok) return null;
    return {
      state: "running",
      baseUrl: backendBaseUrl,
      message: "后端已响应任务请求",
      lastCheckedAt: new Date().toISOString(),
      health: {
        ok: true,
        latencyMs: Date.now() - startedAt
      }
    };
  }

  startBackend(): Promise<BackendStatus> {
    if (!window.lengrvis) {
      return this.getBackendStatus();
    }
    return window.lengrvis.backend.start();
  }

  stopBackend(): Promise<BackendStatus> {
    if (!window.lengrvis) {
      return this.getBackendStatus();
    }
    return window.lengrvis.backend.stop();
  }

  listChatMessages(): Promise<ApiResponse<ChatMessage[]>> {
    return this.request<BackendChatMessage[]>({ endpoint: "/api/chat/messages" }).then((response) =>
      mapResponse(response, (messages) => messages.map(mapChatMessage))
    );
  }

  sendChat(body: ChatRequest): Promise<ApiResponse<ChatResponse>> {
    return this.request<BackendChatResponse, BackendChatRequest>({
      endpoint: "/api/chat",
      method: "POST",
      body: {
        message: body.content,
        mode: body.mode ?? "efficiency"
      }
    }).then((response) =>
      mapResponse(response, (data) => ({
        message: {
          id: `${data.task_id ?? crypto.randomUUID()}-supervisor`,
          role: "assistant" as const,
          author: data.delegated ? "主管 Agent" : "主管 Agent",
          content: zhBackendText(data.message),
          createdAt: new Date().toISOString(),
          status: "sent" as const
        },
        taskUpdates: data.delegated && data.task_id && data.status
          ? [
              {
                id: data.task_id,
                title: "主管已分配任务",
                description: `状态：${zhBackendTaskStatus(data.status)}`,
                state: mapTaskState(data.status),
                agent: data.agent ?? "主管 Agent",
                createdAt: new Date().toISOString(),
                updatedAt: new Date().toISOString()
              }
            ]
          : []
      }))
    );
  }

  listIntentSuggestions(): Promise<ApiResponse<IntentSuggestion[]>> {
    return this.request<BackendIntentSuggestion[]>({ endpoint: "/api/chat/proactive-suggestions", timeoutMs: 2500 }).then(
      (response) => mapResponse(response, (suggestions) => suggestions.map(mapIntentSuggestion))
    );
  }

  async launchPerceptionSuggestion(
    body: PerceptionSuggestionLaunchRequest
  ): Promise<ApiResponse<PerceptionSuggestionLaunchResponse>> {
    const response = await this.request<BackendSuggestionLaunchResponse, BackendSuggestionLaunchRequest>({
      endpoint: `/api/perception/suggestions/${encodeURIComponent(body.suggestionId)}/launch`,
      method: "POST",
      body: {
        suggestion_id: body.suggestionId,
        prompt: body.prompt,
        mode: body.mode ?? "efficiency"
      },
      timeoutMs: 10_000
    });

    return mapResponse(response, (data) => mapSuggestionLaunchResponse(data, body.prompt ?? body.suggestionId));
  }

  startRun(body: ChatRequest): Promise<ApiResponse<ChatResponse>> {
    const requestBody: BackendRunCreateRequest = {
      message: body.content,
      mode: body.mode ?? "efficiency",
      engine: "auto"
    };
    const responsePromise = window.lengrvis?.runs
      ? window.lengrvis.runs.start(requestBody) as Promise<ApiResponse<BackendRunCreateResponse>>
      : this.request<BackendRunCreateResponse, BackendRunCreateRequest>({
          endpoint: "/api/runs",
          method: "POST",
          body: requestBody
        });
    return responsePromise.then((response) =>
      mapResponse(response, (data) => ({
        runId: data.run_id,
        engine: data.engine,
        message: {
          id: `${data.run_id}-run-started`,
          role: "assistant" as const,
          author: "Lengrvis",
          content: `已开始处理任务，当前状态：${zhBackendTaskStatus(data.phase)}。`,
          createdAt: new Date().toISOString(),
          status: "sent" as const
        },
        taskUpdates: [
          {
            id: data.run_id,
            runId: data.run_id,
            title: body.content,
            description: `状态：${zhBackendTaskStatus(data.phase)}`,
            state: mapTaskState(data.phase),
            agent: runEngineAgentName(data.engine),
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString()
          }
        ]
      }))
    );
  }

  listRuns(): Promise<ApiResponse<TaskEvent[]>> {
    return this.request<BackendRunState[]>({ endpoint: "/api/runs" }).then((response) =>
      mapResponse(response, (runs) => runs.map(mapRunTaskEvent))
    );
  }

  getRunTimeline(runId: string): Promise<ApiResponse<BackendRunTimeline>> {
    return this.request<BackendRunTimeline>({ endpoint: `/api/runs/${runId}/timeline`, timeoutMs: 10_000 });
  }

  async listTaskTimeline(): Promise<ApiResponse<TaskEvent[]>> {
    const response = await this.request<BackendTask[]>({ endpoint: "/api/tasks" });
    if (!response.ok || !response.data) {
      return mapResponse(response, () => []);
    }
    const tasks = response.data.map(mapTaskEvent);
    return {
      ok: true,
      status: response.status,
      data: tasks,
      receivedAt: response.receivedAt
    };
  }

  async getTaskTimelineEvent(taskId: string): Promise<ApiResponse<TaskEvent>> {
    const taskResponse = await this.request<BackendTask>({ endpoint: `/api/tasks/${taskId}` });
    if (!taskResponse.ok || !taskResponse.data) {
      return mapResponse(taskResponse, () => {
        throw new Error("Task not found");
      });
    }
    const event = await this.mapTaskEventWithRecordings(taskResponse.data);
    return {
      ok: true,
      status: taskResponse.status,
      data: event,
      receivedAt: taskResponse.receivedAt
    };
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
    return this.request<BackendTaskArtifacts>({ endpoint: `/api/tasks/${taskId}/artifacts`, timeoutMs: 10_000 }).then(
      (response) =>
        mapResponse(response, (data) => ({
          taskId: data.task_id,
          artifacts: (data.artifacts ?? []).map((item) => ({
            path: item.path,
            kind: item.kind,
            toolName: item.tool_name,
            stepId: item.step_id,
            createdAt: item.created_at,
            exists: Boolean(item.exists),
            isDir: Boolean(item.is_dir),
            sizeBytes: Number(item.size_bytes ?? 0)
          })),
          counts: {
            total: Number(data.counts?.total ?? 0),
            existing: Number(data.counts?.existing ?? 0),
            missing: Number(data.counts?.missing ?? 0),
            changed: Number(data.counts?.changed ?? 0),
            generated: Number(data.counts?.generated ?? 0)
          }
        }))
    );
  }

  getLocalMetrics(days = 7): Promise<ApiResponse<LocalMetricsSummary>> {
    return this.request<BackendLocalMetrics>({ endpoint: `/api/metrics/local?days=${days}`, timeoutMs: 10_000 }).then(
      (response) =>
        mapResponse(response, (data) => ({
          windowDays: Number(data.window_days ?? days),
          generatedAt: data.generated_at ?? "",
          tasks: {
            total: Number(data.tasks?.total ?? 0),
            terminal: Number(data.tasks?.terminal ?? 0),
            succeeded: Number(data.tasks?.succeeded ?? 0),
            successRate: data.tasks?.success_rate ?? null,
            byStatus: data.tasks?.by_status ?? {}
          },
          runs: {
            total: Number(data.runs?.total ?? 0),
            byPhase: data.runs?.by_phase ?? {}
          },
          recovery: {
            reflectionsStarted: Number(data.recovery?.reflections_started ?? 0),
            runsWithReflection: Number(data.recovery?.runs_with_reflection ?? 0),
            recoveryTriggerRate: data.recovery?.recovery_trigger_rate ?? null,
            decidedActions: data.recovery?.decided_actions ?? {},
            askUserShare: data.recovery?.ask_user_share ?? null
          },
          llm: {
            calls: Number(data.llm?.calls ?? 0),
            anomalies: Number(data.llm?.anomalies ?? 0),
            anomalyRate: data.llm?.anomaly_rate ?? null,
            estimatedCalls: Number(data.llm?.estimated_calls ?? 0),
            byFinishReason: data.llm?.by_finish_reason ?? {}
          }
        }))
    );
  }

  async getCurrentPlan(): Promise<ApiResponse<Plan>> {
    const runsResponse = await this.request<BackendRunState[]>({ endpoint: "/api/runs" });
    const latestRun = runsResponse.ok && runsResponse.data?.length ? latestRunState(runsResponse.data) : null;
    if (latestRun) {
      const timeline = await this.getRunTimeline(latestRun.run_id);
      if (timeline.ok && timeline.data && hasRunTimelineEvents(timeline.data)) {
        return mapResponse(timeline, (data) => mapRunPlan(latestRun, data));
      }
    }

    return this.request<BackendTask[]>({ endpoint: "/api/tasks" }).then(async (tasksResponse) => {
      if (!tasksResponse.ok || !tasksResponse.data?.[0]) {
        return mapResponse(tasksResponse, () => emptyPlan());
      }

      const task = tasksResponse.data[0];
      const timeline = await this.request<BackendTimeline>({ endpoint: `/api/tasks/${task.id}/timeline` });
      return mapResponse(timeline, (data) => {
        const plannerMessage = [...data.messages].reverse().find((message) => agentNameFor(message) === "PlannerAgent");
        const rawPlan = metadataPayloadFor<BackendPlan>(plannerMessage);
        if (!rawPlan?.steps?.length) {
          return {
            ...emptyPlan(),
            id: task.id,
            title: task.user_goal,
            objective: task.final_summary || task.user_goal,
            updatedAt: task.updated_at
          };
        }
        return {
          id: rawPlan.id,
          title: rawPlan.goal,
          objective: rawPlan.assumptions?.join(" ") || task.user_goal,
          updatedAt: task.updated_at,
          steps: rawPlan.steps.map((step) => ({
            id: step.id,
            title: zhToolName(step.tool_name),
            detail: zhBackendText(step.description),
            state: step.status === "succeeded" ? "done" : step.status === "waiting_user_approval" ? "blocked" : "pending",
            owner: step.agent_name,
            toolName: step.tool_name,
            riskLevel: step.risk_level,
            effects: step.tool_effects ?? [],
            resourceKinds: step.resource_kinds ?? [],
            trustTier: step.trust_tier,
            approvalState: step.requires_approval ? "required" : "not_required",
            deferredTool: Boolean(step.deferred_tool)
          }))
        };
      });
    });
  }

  async listAgentConversations(): Promise<ApiResponse<AgentConversation[]>> {
    const runsResponse = await this.request<BackendRunState[]>({ endpoint: "/api/runs" });
    const latestRun = runsResponse.ok && runsResponse.data?.length ? latestRunState(runsResponse.data) : null;
    if (latestRun) {
      const timeline = await this.getRunTimeline(latestRun.run_id);
      if (timeline.ok && timeline.data && hasRunTimelineEvents(timeline.data)) {
        return mapResponse(timeline, (data) => [mapRunConversation(latestRun, data.events)]);
      }
    }

    return this.request<BackendTask[]>({ endpoint: "/api/tasks" }).then(async (tasksResponse) => {
      if (!tasksResponse.ok || !tasksResponse.data?.[0]) {
        return mapResponse(tasksResponse, () => []);
      }
      const task = tasksResponse.data[0];
      const response = await this.request<BackendAgentMessage[]>({
        endpoint: `/api/tasks/${task.id}/agent-messages`
      });
      return mapResponse(response, (messages) => [
        {
          id: `${task.id}-agents`,
          title: task.user_goal,
          status: task.status === "completed" ? "done" : task.status === "waiting_user_approval" ? "waiting" : "running",
          messages: messages.map((message) => ({
            id: message.id,
            role: message.role ?? "assistant",
            name: agentNameFor(message),
            agent: agentNameFor(message),
            content: zhBackendText(message.content),
            createdAt: message.created_at,
            toolCalls: message.tool_calls,
            toolCallId: message.tool_call_id,
            metadata: message.metadata,
            kind: mapAgentKind(message.metadata?.message_type ?? message.message_type)
          }))
        }
      ]);
    });
  }

  subscribeTaskMessages(
    taskId: string,
    handlers: JsonRealtimeHandlers<BackendTaskStreamEvent>
  ): () => void {
    if (!taskId) {
      return () => undefined;
    }

    const request = { endpoint: `/ws/tasks/${encodeURIComponent(taskId)}` };
    return subscribeJsonRealtime<BackendTaskStreamEvent>(request, handlers);
  }

  getSafetyReview(): Promise<ApiResponse<SafetyReview>> {
    return this.request<BackendTask[]>({ endpoint: "/api/tasks" }).then(async (tasksResponse) => {
      if (!tasksResponse.ok || !tasksResponse.data?.[0]) {
        return mapResponse(tasksResponse, () => emptySafetyReview());
      }
      const task = tasksResponse.data[0];
      const response = await this.request<BackendSafetyReview[]>({
        endpoint: `/api/tasks/${task.id}/safety-reviews`
      });
      return mapResponse(response, (reviews) => ({
        id: `${task.id}-safety`,
        status: reviews.some((review) => review.verdict === "deny")
          ? "blocked"
          : reviews.some((review) => review.verdict === "needs_user_approval")
            ? "needs_review"
            : "clear",
        updatedAt: reviews[0]?.created_at ?? task.updated_at,
        boundaryEvents: mapBoundaryEvents(task.boundary_events),
        findings: reviews.map((review) => ({
          id: review.id,
          severity: mapRiskSeverity(review.risk_level),
          title: `${review.target_type}：${zhSafetyVerdict(review.verdict)} · ${zhRiskLevel(review.risk_level)}`,
          detail: review.reasons.map(zhBackendText).join(" ") || zhBackendText(review.safe_alternative) || "无安全发现。",
          status: review.verdict === "allow" ? "accepted" : "open"
        }))
      }));
    });
  }

  listPendingApprovals(): Promise<ApiResponse<ApprovalRequest[]>> {
    return this.request<BackendApproval[]>({ endpoint: "/api/approvals/pending" }).then((response) =>
      mapResponse(response, (approvals) => approvals.map(mapApproval))
    );
  }

  submitApprovalDecision(decision: ApprovalDecision): Promise<ApiResponse<ApprovalRequest>> {
    const action = decision.decision === "approved" ? "approve" : "reject";
    if (window.lengrvis?.approvals) {
      const request = action === "approve"
        ? window.lengrvis.approvals.approve(decision.approvalId)
        : window.lengrvis.approvals.reject(decision.approvalId);
      return request.then((response) => mapResponse(response, mapApproval));
    }
    return this.request<BackendApproval>({
      endpoint: `/api/approvals/${decision.approvalId}/${action}`,
      method: "POST"
    }).then((response) => mapResponse(response, mapApproval));
  }

  listCommands(): Promise<ApiResponse<CommandInfo[]>> {
    return this.request<BackendCommandsResponse>({ endpoint: "/api/commands" }).then((response) =>
      mapResponse(response, (data) => (data.commands ?? []).map(mapCommandInfo))
    );
  }

  executeCommand(name: string, args: Record<string, unknown> = {}): Promise<ApiResponse<CommandExecutionResult>> {
    if (window.lengrvis?.commands) {
      return window.lengrvis.commands.execute({ name, args }).then((response) =>
        mapResponse(response as ApiResponse<BackendCommandExecutionResult>, mapCommandExecutionResult)
      );
    }
    return this.request<BackendCommandExecutionResult, { name: string; args: Record<string, unknown> }>({
      endpoint: "/api/commands/execute",
      method: "POST",
      body: { name, args }
    }).then((response) => mapResponse(response, mapCommandExecutionResult));
  }

  createMobilePairingCode(): Promise<ApiResponse<MobilePairingCode>> {
    if (window.lengrvis?.mobilePairing) {
      return window.lengrvis.mobilePairing.createCode() as Promise<ApiResponse<MobilePairingCode>>;
    }
    return this.request<MobilePairingCode>({
      endpoint: "/api/pair/request",
      method: "POST"
    });
  }

  listMobileDevices(): Promise<ApiResponse<MobileDeviceList>> {
    if (window.lengrvis?.mobilePairing) {
      return window.lengrvis.mobilePairing.listDevices() as Promise<ApiResponse<MobileDeviceList>>;
    }
    return this.request<MobileDeviceList>({
      endpoint: "/api/pair/devices"
    });
  }

  revokeMobileDevice(deviceId: string): Promise<ApiResponse<MobileDevice>> {
    if (window.lengrvis?.mobilePairing) {
      return window.lengrvis.mobilePairing.revokeDevice(deviceId) as Promise<ApiResponse<MobileDevice>>;
    }
    return this.request<MobileDevice>({
      endpoint: `/api/pair/devices/${encodeURIComponent(deviceId)}`,
      method: "DELETE"
    });
  }

  createRemoteInputGrant(deviceId: string, expiresInSeconds = 300): Promise<ApiResponse<RemoteInputGrantIssueResult>> {
    if (window.lengrvis?.mobilePairing) {
      return window.lengrvis.mobilePairing.createRemoteInputGrant({ deviceId, expiresInSeconds }) as Promise<
        ApiResponse<RemoteInputGrantIssueResult>
      >;
    }
    return this.request<RemoteInputGrantIssueResult, { expires_in: number }>({
      endpoint: `/api/pair/devices/${encodeURIComponent(deviceId)}/remote-input-grants`,
      method: "POST",
      body: { expires_in: expiresInSeconds }
    });
  }

  revokeRemoteInputGrant(deviceId: string, grantId: string): Promise<ApiResponse<RemoteInputGrant>> {
    if (window.lengrvis?.mobilePairing) {
      return window.lengrvis.mobilePairing.revokeRemoteInputGrant({ deviceId, grantId }) as Promise<ApiResponse<RemoteInputGrant>>;
    }
    return this.request<RemoteInputGrant>({
      endpoint: `/api/pair/devices/${encodeURIComponent(deviceId)}/remote-input-grants/${encodeURIComponent(grantId)}`,
      method: "DELETE"
    });
  }

  searchFiles(query: string): Promise<ApiResponse<FileSearchResponse>> {
    return this.request<BackendFileSearchResponse>({
      endpoint: "/api/files/search",
      query: { q: query },
      timeoutMs: 10_000
    }).then((response) =>
      mapResponse(response, (data) => {
        const results: FileSearchResult[] = [
          ...(data.index_results ?? []).map((item, index) => ({
            id: item.file_id ?? `index-${index}`,
            path: item.path,
            match: item.snippet ?? "",
            line: 1,
            score: 0.9
          })),
          ...(data.name_results ?? []).map((item, index) => ({
            id: item.path ?? `name-${index}`,
            path: item.path,
            match: item.name ?? item.path,
            line: 1,
            score: 0.75
          }))
        ];
        const meta = data.name_search ?? {};
        return {
          results,
          meta: {
            count: numberOrZero(meta.count),
            scanned: numberOrZero(meta.scanned),
            truncated: Boolean(meta.truncated),
            status: meta.status ?? "ok",
            indexStatus: mapIndexStatus(data.index_status)
          }
        };
      })
    );
  }

  listLocalLibrary(section: string, query = "", limit = 240): Promise<ApiResponse<LocalLibraryResponse>> {
    return this.request<BackendLocalLibraryResponse>({
      endpoint: "/api/library",
      query: { section, q: query, limit },
      timeoutMs: 20_000
    }).then((response) => mapResponse(response, mapLocalLibraryResponse));
  }

  getSettings(): Promise<ApiResponse<AppSettings>> {
    return this.request<BackendSettings>({ endpoint: "/api/settings" }).then((response) => {
      const mapped = mapResponse(response, mapSettings);
      if (mapped.ok && mapped.data) {
        mapped.data = mergeDesktopOnlySettings(mapped.data, this.lastLoadedSettings);
        this.lastLoadedSettings = mapped.data;
      }
      return mapped;
    });
  }

  getLocalLlmHealth(): Promise<ApiResponse<LocalLLMHealth>> {
    return this.request<BackendLocalLlmHealth>({
      endpoint: "/api/settings/local-llm/health",
      timeoutMs: 2500
    }).then((response) => mapResponse(response, mapLocalLlmHealth));
  }

  getLocalModelSetupPlan(model?: string): Promise<ApiResponse<LocalModelSetupPlan>> {
    return this.request<BackendLocalModelSetupPlan>({
      endpoint: "/api/settings/local-model/setup-plan",
      query: model ? { model } : undefined,
      timeoutMs: 10_000
    }).then((response) => mapResponse(response, mapLocalModelSetupPlan));
  }

  installLocalModel(request: LocalModelInstallRequest = {}): Promise<ApiResponse<LocalModelInstallResponse>> {
    const body = compactLocalModelRequest(request);
    if (window.lengrvis?.localModel) {
      return window.lengrvis.localModel.install(body) as Promise<ApiResponse<LocalModelInstallResponse>>;
    }
    return this.request<LocalModelInstallResponse, LocalModelInstallRequest>({
      endpoint: "/api/settings/install-local-model",
      method: "POST",
      body,
      timeoutMs: 120_000
    });
  }

  installOllama(): Promise<ApiResponse<OllamaActionResponse>> {
    if (window.lengrvis?.ollama) {
      return window.lengrvis.ollama.install() as Promise<ApiResponse<OllamaActionResponse>>;
    }
    return this.request<OllamaActionResponse>({
      endpoint: "/api/settings/ollama/install",
      method: "POST",
      timeoutMs: 120_000
    });
  }

  startOllama(): Promise<ApiResponse<OllamaActionResponse>> {
    if (window.lengrvis?.ollama) {
      return window.lengrvis.ollama.start() as Promise<ApiResponse<OllamaActionResponse>>;
    }
    return this.request<OllamaActionResponse>({
      endpoint: "/api/settings/ollama/start",
      method: "POST",
      timeoutMs: 30_000
    });
  }

  pullOllama(model?: string): Promise<ApiResponse<OllamaActionResponse>> {
    const body = compactLocalModelRequest({ model });
    if (window.lengrvis?.ollama) {
      return window.lengrvis.ollama.pull(body) as Promise<ApiResponse<OllamaActionResponse>>;
    }
    return this.request<OllamaActionResponse, LocalModelInstallRequest>({
      endpoint: "/api/settings/ollama/pull",
      method: "POST",
      body,
      timeoutMs: 120_000
    });
  }

  getLlmHealth(): Promise<ApiResponse<LLMHealthStatus>> {
    return this.request<BackendLlmHealth>({
      endpoint: "/api/settings/llm/health",
      timeoutMs: 2500
    }).then((response) => mapResponse(response, mapLlmHealth));
  }

  getLlmProfile(): Promise<ApiResponse<LLMProfile>> {
    return this.request<BackendLlmProfileResponse>({
      endpoint: "/api/settings/llm/profile",
      timeoutMs: 2500
    }).then((response) => mapResponse(response, (data) => mapLlmProfile(data.profile)));
  }

  getLlmCostSummary(): Promise<ApiResponse<LLMCostSummary>> {
    return this.request<BackendLlmCostSummary>({
      endpoint: "/api/settings/llm/cost-summary",
      timeoutMs: 2500
    }).then((response) => mapResponse(response, mapLlmCostSummary));
  }

  getHardwareAccelerationStatus(): Promise<ApiResponse<HardwareAccelerationStatusPayload>> {
    return this.request<BackendHardwareAccelerationStatus>({
      endpoint: "/api/settings/onnx/status",
      timeoutMs: 2500
    }).then((response) => mapResponse(response, mapHardwareAccelerationStatus));
  }

  async runHardwareAccelerationSmoke(
    payload: HardwareAccelerationSmokeRequest = {}
  ): Promise<ApiResponse<HardwareAccelerationSmokePayload>> {
    const endpointByOperation: Record<NonNullable<HardwareAccelerationSmokeRequest["operation"]>, string> = {
      warmup: "/api/settings/onnx/warmup",
      test_generate: "/api/settings/onnx/test-generate",
      test_embedding: "/api/settings/onnx/test-embedding",
      test_ocr: "/api/settings/onnx/test-ocr",
      test_image_embedding: "/api/settings/onnx/test-image-embedding"
    };
    const operation = payload.operation ?? "warmup";
    const body = operation === "test_generate"
      ? {
          prompt: payload.prompt,
          max_tokens: payload.maxTokens,
          model_path: payload.modelPath
        }
      : operation === "test_embedding"
        ? {
            texts: payload.texts,
            model_path: payload.modelPath
          }
        : operation === "test_image_embedding"
          ? {
              image_path: payload.imagePath,
              model_path: payload.modelPath
            }
      : {
          model_path: payload.modelPath
        };
    const method = "POST";
    const endpoint = endpointByOperation[operation];
    const response = await this.request<BackendHardwareAccelerationSmoke, HardwareAccelerationSmokeRequestBody>({
      endpoint,
      method,
      body,
      timeoutMs: 10_000
    });
    return mapResponse(response, mapHardwareAccelerationSmoke);
  }

  getContextUsage(taskId?: string): Promise<ApiResponse<ContextUsage>> {
    return this.request<BackendContextUsage>({
      endpoint: "/api/context/usage",
      query: taskId ? { task_id: taskId } : undefined,
      timeoutMs: 2500
    }).then((response) => mapResponse(response, mapContextUsage));
  }

  async saveSettings(settings: AppSettings): Promise<ApiResponse<AppSettings>> {
    const body = settingsPatchFor(settings, this.lastLoadedSettings);
    const confirmation = window.lengrvis?.settings
      ? await window.lengrvis.settings.confirmSensitiveChange(body as Record<string, unknown>) as ApiResponse<SensitiveChangeConfirmation>
      : await this.request<SensitiveChangeConfirmation, Partial<BackendSettings>>({
          endpoint: "/api/settings/confirm-sensitive-change",
          method: "POST",
          body
        });
    if (confirmation.ok && confirmation.data?.required && confirmation.data.nonce) {
      body.confirmation_nonce = confirmation.data.nonce;
    }
    const responsePromise = window.lengrvis?.settings
      ? window.lengrvis.settings.save(body as Record<string, unknown>) as Promise<ApiResponse<BackendSettings>>
      : this.request<BackendSettings, Partial<BackendSettings>>({
          endpoint: "/api/settings",
          method: "POST",
          body
        });
    return responsePromise.then((response) => {
      const mapped = mapResponse(response, mapSettings);
      if (mapped.ok && mapped.data) {
        mapped.data = mergeDesktopOnlySettings(mapped.data, settings);
        this.lastLoadedSettings = mapped.data;
      }
      return mapped;
    });
  }

  async confirmPermissionRuleChange(rule: BackendPermissionRule): Promise<ApiResponse<SensitiveChangeConfirmation>> {
    if (window.lengrvis?.permissionPolicy) {
      return window.lengrvis.permissionPolicy.confirmRelaxation({ action: "upsert_rule", rule }) as Promise<
        ApiResponse<SensitiveChangeConfirmation>
      >;
    }
    return this.request<SensitiveChangeConfirmation, { action: string; rule: BackendPermissionRule }>({
      endpoint: "/api/settings/permission-policy/confirm-relaxation",
      method: "POST",
      body: { action: "upsert_rule", rule }
    });
  }

  async confirmPermissionRuleDelete(ruleId: string): Promise<ApiResponse<SensitiveChangeConfirmation>> {
    if (window.lengrvis?.permissionPolicy) {
      return window.lengrvis.permissionPolicy.confirmRelaxation({ action: "delete_rule", ruleId }) as Promise<
        ApiResponse<SensitiveChangeConfirmation>
      >;
    }
    return this.request<SensitiveChangeConfirmation, { action: string; rule_id: string }>({
      endpoint: "/api/settings/permission-policy/confirm-relaxation",
      method: "POST",
      body: { action: "delete_rule", rule_id: ruleId }
    });
  }

  upsertPermissionRule(rule: BackendPermissionRule, confirmationNonce?: string): Promise<ApiResponse<BackendPermissionPolicy>> {
    if (window.lengrvis?.permissionPolicy) {
      return window.lengrvis.permissionPolicy.upsertRule({ rule, confirmationNonce }) as Promise<ApiResponse<BackendPermissionPolicy>>;
    }
    return this.request<BackendPermissionPolicy, BackendPermissionRule>({
      endpoint: "/api/settings/permission-policy/rules",
      method: "POST",
      query: confirmationNonce ? { confirmation_nonce: confirmationNonce } : undefined,
      body: rule
    });
  }

  deletePermissionRule(ruleId: string, confirmationNonce?: string): Promise<ApiResponse<{ ok: boolean; policy: BackendPermissionPolicy }>> {
    if (window.lengrvis?.permissionPolicy) {
      return window.lengrvis.permissionPolicy.deleteRule({ ruleId, confirmationNonce }) as Promise<
        ApiResponse<{ ok: boolean; policy: BackendPermissionPolicy }>
      >;
    }
    return this.request<{ ok: boolean; policy: BackendPermissionPolicy }>({
      endpoint: `/api/settings/permission-policy/rules/${encodeURIComponent(ruleId)}`,
      method: "DELETE",
      query: confirmationNonce ? { confirmation_nonce: confirmationNonce } : undefined
    });
  }

  listAuditLogs(): Promise<ApiResponse<AuditLogEntry[]>> {
    return this.request<BackendAuditEvent[]>({ endpoint: "/api/audit" }).then((response) =>
      mapResponse(response, (events) =>
        events.map((event) => ({
          id: event.id,
          actor: event.actor,
          action: event.event_type,
          target: event.task_id ?? "系统",
          level: event.event_type.includes("failed") ? "error" : event.event_type.includes("review") ? "warning" : "info",
          createdAt: event.created_at
        }))
      )
    );
  }

  getSystemInfo(): Promise<ApiResponse<SystemInfo>> {
    return Promise.all([
      this.request<BackendSystemInfo>({ endpoint: "/api/system/info" }),
      this.request<BackendSystemDiagnostics>({ endpoint: "/api/system/diagnostics" }),
      this.request<BackendProcessesResponse>({ endpoint: "/api/system/processes", query: { limit: 8 } }),
      this.request<BackendStartupResponse>({ endpoint: "/api/system/startup-items" }),
      this.request<BackendAppsResponse>({ endpoint: "/api/apps" })
    ]).then(([infoResponse, diagnosticsResponse, processesResponse, startupResponse, appsResponse]) =>
      mapResponse(infoResponse, (info) => ({
        appVersion: window.lengrvis?.versions.app ?? "0.1.1",
        electronVersion: window.lengrvis?.versions.electron ?? "未知",
        chromeVersion: window.lengrvis?.versions.chrome ?? "未知",
        nodeVersion: window.lengrvis?.versions.node ?? "未知",
        platform: info.system ?? info.platform ?? window.lengrvis?.platform ?? "未知",
        arch: info.machine ?? "未知",
        backendBaseUrl: window.lengrvis?.backendBaseUrl ?? FALLBACK_BACKEND_URL,
        diagnostics: diagnosticsResponse.ok && diagnosticsResponse.data
          ? mapDiagnostic(diagnosticsResponse.data, startupResponse.data?.startup_items)
          : undefined,
        processes: processesResponse.ok && processesResponse.data
          ? processesResponse.data.processes.map(mapProcess)
          : undefined,
        startupItems: startupResponse.ok && startupResponse.data
          ? startupResponse.data.startup_items.map(mapStartupItem)
          : undefined,
        installedApps: appsResponse.ok && appsResponse.data
          ? appsResponse.data.apps.map(mapInstalledApp)
          : undefined
      }))
    );
  }

  exportDiagnosticsPackage(): Promise<ApiResponse<DiagnosticExportResult>> {
    const request = window.lengrvis?.system
      ? window.lengrvis.system.exportDiagnosticsPackage() as Promise<ApiResponse<BackendDiagnosticExportResult>>
      : this.request<BackendDiagnosticExportResult>({
          endpoint: "/api/system/diagnostics/export",
          method: "POST",
          timeoutMs: 10_000
        });
    return request.then((response) => mapResponse(response, mapDiagnosticExportResult));
  }

  eraseLocalData(request: DesktopPrivacyEraseRequest): Promise<ApiResponse<PrivacyEraseResult>> {
    if (!window.lengrvis?.privacy) {
      return Promise.resolve({
        ok: false,
        status: 0,
        error: {
          code: "DESKTOP_REQUIRED",
          message: "删除本机数据需要在 Electron 桌面应用中完成"
        },
        receivedAt: new Date().toISOString()
      });
    }
    return window.lengrvis.privacy
      .eraseLocalData(request)
      .then((response: ApiResponse<DesktopPrivacyEraseResponse>) =>
        mapResponse(response, (data) => ({
          scope: data.scope,
          deletedRowsByTable: data.deleted.rows_by_table,
          deletedRowsTotal: Number(data.deleted.rows_total || 0),
          deletedDiagnosticPackages: Number(data.deleted.diagnostic_packages || 0),
          preserved: Array.isArray(data.preserved) ? data.preserved.map(String) : [],
          settingsReset: !data.preserved.includes("app_settings"),
          manualLogCleanupRequired: data.manual_cleanup?.log_dirs === "not_deleted_at_runtime_see_settings_system_info",
          auditRecorded: data.audit === "erase_event_appended_to_local_audit_chain"
        }))
      );
  }

  listApps(): Promise<ApiResponse<InstalledApp[]>> {
    return this.request<BackendAppsResponse>({ endpoint: "/api/apps" }).then((response) =>
      mapResponse(response, (data) => data.apps.map(mapInstalledApp))
    );
  }

  revealFile(path: string): Promise<ApiResponse<FileRevealResult>> {
    return this.request<BackendFileRevealResult, { path: string }>({
      endpoint: "/api/apps/reveal",
      method: "POST",
      body: { path },
      timeoutMs: 10_000
    }).then((response) => mapResponse(response, mapFileRevealResult));
  }

  async showItemInFolder(path: string): Promise<ApiResponse<FileRevealResult>> {
    if (!window.lengrvis?.shell.showItemInFolder) {
      return this.revealFile(path);
    }
    const receivedAt = new Date().toISOString();
    try {
      const result = await window.lengrvis.shell.showItemInFolder(path);
      return {
        ok: result.ok,
        status: result.ok ? 200 : 400,
        data: result,
        error: result.ok ? undefined : { message: result.error ?? "无法打开所在位置" },
        receivedAt
      };
    } catch (error) {
      return {
        ok: false,
        status: 0,
        error: { message: zhUserFacingError(error instanceof Error ? error.message : "无法打开所在位置") },
        receivedAt
      };
    }
  }

  parseDocument(body: DocumentParseRequest): Promise<ApiResponse<DocumentIR>> {
    const request = window.lengrvis?.documents
      ? window.lengrvis.documents.parse(body) as Promise<ApiResponse<BackendDocumentIR>>
      : this.request<BackendDocumentIR, BackendDocumentParseRequest>({
          endpoint: "/api/documents/parse",
          method: "POST",
          body: {
            path: body.path,
            include_text: body.includeText
          },
          timeoutMs: 30_000
        });
    return request.then((response) => mapResponse(response, mapDocumentIR));
  }

  askDocument(body: DocumentAskRequest): Promise<ApiResponse<DocumentAskResponse>> {
    const request = window.lengrvis?.documents
      ? window.lengrvis.documents.ask(body) as Promise<ApiResponse<BackendDocumentAskResponse>>
      : this.request<BackendDocumentAskResponse, BackendDocumentAskRequest>({
          endpoint: "/api/documents/ask",
          method: "POST",
          body: {
            path: body.path,
            question: body.question,
            top_k: body.topK
          },
          timeoutMs: 30_000
        });
    return request.then((response) => mapResponse(response, mapDocumentAskResponse));
  }

  compareDocuments(body: DocumentCompareRequest): Promise<ApiResponse<DocumentCompareResponse>> {
    const request = window.lengrvis?.documents
      ? window.lengrvis.documents.compare(body) as Promise<ApiResponse<BackendDocumentCompareResponse>>
      : this.request<BackendDocumentCompareResponse, BackendDocumentCompareRequest>({
          endpoint: "/api/documents/compare",
          method: "POST",
          body: {
            paths: body.paths,
            focus: body.focus
          },
          timeoutMs: 45_000
        });
    return request.then((response) => mapResponse(response, mapDocumentCompareResponse));
  }

  scanCleanup(body: CleanupScanRequest = {}): Promise<ApiResponse<CleanupPlan>> {
    return this.request<BackendCleanupPlan, BackendCleanupScanRequest>({
      endpoint: "/api/files/cleanup/scan",
      method: "POST",
      body: cleanupScanRequestFor(body),
      timeoutMs: 30_000
    }).then((response) => mapResponse(response, mapCleanupPlan));
  }

  planCleanup(body: CleanupPlanRequest = {}): Promise<ApiResponse<CleanupPlan>> {
    return this.request<BackendCleanupPlan, BackendCleanupPlanRequest>({
      endpoint: "/api/files/cleanup/plan",
      method: "POST",
      body: {
        ...cleanupScanRequestFor(body),
        item_ids: body.itemIds,
        prefer_trash: body.preferTrash
      },
      timeoutMs: 30_000
    }).then((response) => mapResponse(response, mapCleanupPlan));
  }

  executeCleanup(body: CleanupExecuteRequest): Promise<ApiResponse<CleanupExecutionResult>> {
    const selectedItemIds = body.selectedItemIds ?? body.items?.map((item) => item.id);
    const requestBody: BackendCleanupExecuteRequest = {
      roots: body.roots,
      plan_id: body.planId,
      content_hash: body.contentHash,
      selected_item_ids: selectedItemIds,
      dry_run: body.dryRun,
      approved: body.approved,
      approval_id: body.approvalId
    };
    const response = window.lengrvis?.cleanup
      ? window.lengrvis.cleanup.execute(requestBody as Record<string, unknown>)
      : this.request<BackendCleanupExecutionResult, BackendCleanupExecuteRequest>({
          endpoint: "/api/files/cleanup/execute",
          method: "POST",
          body: requestBody,
          timeoutMs: 60_000
        });
    return response.then((result) =>
      mapResponse(result as ApiResponse<BackendCleanupExecutionResult>, mapCleanupExecutionResult)
    );
  }

  rollbackCleanup(body: CleanupRollbackRequest): Promise<ApiResponse<CleanupExecutionResult>> {
    const requestBody: BackendCleanupRollbackRequest = {
      plan_id: body.planId,
      execution_id: body.executionId
    };
    const response = window.lengrvis?.cleanup
      ? window.lengrvis.cleanup.rollback(requestBody as Record<string, unknown>)
      : this.request<BackendCleanupExecutionResult, BackendCleanupRollbackRequest>({
          endpoint: "/api/files/cleanup/rollback",
          method: "POST",
          body: requestBody,
          timeoutMs: 60_000
        });
    return response.then((result) =>
      mapResponse(result as ApiResponse<BackendCleanupExecutionResult>, mapCleanupExecutionResult)
    );
  }

  readBrowserPage(url: string): Promise<ApiResponse<BrowserPageSnapshot>> {
    return this.request<BackendBrowserPage>({
      endpoint: "/api/browser/read",
      query: { url },
      timeoutMs: 20_000
    }).then((response) => mapResponse(response, mapBrowserPage));
  }

  getBrowserLinks(url: string): Promise<ApiResponse<BrowserLinkResult[]>> {
    return this.request<BackendBrowserLinks>({
      endpoint: "/api/browser/links",
      query: { url },
      timeoutMs: 20_000
    }).then((response) => mapResponse(response, (data) => data.links.map(mapBrowserLink)));
  }

  async listBrowserSessions(): Promise<ApiResponse<BrowserSession[]>> {
    const receivedAt = new Date().toISOString();
    const [hostResult, backendResult] = await Promise.allSettled([
      this.getBrowserHostSnapshot(),
      this.request<BackendBrowserSessions>({ endpoint: "/api/browser/sessions", timeoutMs: 2500 })
    ]);

    const snapshot = hostResult.status === "fulfilled" ? hostResult.value : emptyBrowserHostSnapshot(false);
    const backendSessions =
      backendResult.status === "fulfilled" && backendResult.value.ok && backendResult.value.data?.ok !== false
        ? (backendResult.value.data?.sessions ?? []).map(mapBrowserSession)
        : [];

    if (hostResult.status === "fulfilled" || backendResult.status === "fulfilled") {
      return {
        ok: true,
        status: snapshot.hostAvailable || backendSessions.length ? 200 : 204,
        data: mergeBrowserSessionArrays(backendSessions, snapshot.sessions),
        receivedAt
      };
    }

    return {
      ok: false,
      status: 0,
      error: {
        code: "BROWSER_ACTIVITY_UNAVAILABLE",
        message: "Browser activity state is unavailable"
      },
      receivedAt
    };
  }

  async listBrowserSessionEvents(sessionId: string, limit = 200): Promise<ApiResponse<BrowserActivityEvent[]>> {
    const response = await this.request<BackendBrowserEvents>({
      endpoint: `/api/browser/session/${encodeURIComponent(sessionId)}/events`,
      query: { limit },
      timeoutMs: 2500
    });
    const mapped = mapResponse(response, (data) => (data.events ?? []).map(mapBrowserActivityEvent));
    if (mapped.ok && response.data?.ok === false) {
      return {
        ok: false,
        status: response.status,
        error: {
          message: response.data.error ?? "Browser session events unavailable",
          details: response.data
        },
        receivedAt: response.receivedAt
      };
    }
    return mapped;
  }

  async observeBrowserSession(sessionId: string): Promise<ApiResponse<BrowserActivityEvent>> {
    const hostSession = await this.hasBrowserHostSession(sessionId);
    if (hostSession) {
      return {
        ok: false,
        status: 204,
        error: {
          code: "DESKTOP_BROWSER_HOST_SESSION",
          message: "Using desktop browser host observation."
        },
        receivedAt: new Date().toISOString()
      };
    }

    const response = await this.request<BackendBrowserActivityEnvelope, { session_id: string }>({
      endpoint: "/api/browser/observe",
      method: "POST",
      body: { session_id: sessionId },
      timeoutMs: 10_000
    });
    const mapped = mapResponse(response, mapBrowserActivityEnvelope);
    if (mapped.ok && mapped.data?.ok === false) {
      return {
        ok: false,
        status: response.status,
        error: {
          message: mapped.data.error ?? "Browser observe failed",
          details: response.data
        },
        receivedAt: response.receivedAt
      };
    }
    return mapped;
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
    void sessionId;
    return Promise.resolve({
      ok: false,
      status: 501,
      error: {
        code: "UNSUPPORTED_BROWSER_SESSION_COMMAND",
        message: `Backend browser ${command} is not exposed; using desktop browser host state.`
      },
      receivedAt: new Date().toISOString()
    });
  }

  async exportBrowserReplay(sessionId: string): Promise<ApiResponse<BrowserReplayExport>> {
    const hostReplay = await this.exportBrowserHostReplay(sessionId);
    if (hostReplay) return hostReplay;

    const response = await this.request<BackendBrowserReplayExport, { session_id: string }>({
      endpoint: "/api/browser/replay-export",
      method: "POST",
      body: { session_id: sessionId },
      timeoutMs: 20_000
    });
    const mapped = mapResponse(response, mapBrowserReplayExport);
    if (mapped.ok && mapped.data?.ok === false) {
      return {
        ok: false,
        status: response.status,
        error: {
          message: mapped.data.error ?? "Replay export failed",
          details: response.data
        },
        receivedAt: response.receivedAt
      };
    }
    return mapped;
  }

  subscribeBrowserSession(
    sessionId: string,
    handlers: {
      onMessage: (message: BackendBrowserSessionStreamEvent) => void;
      onError?: (error: Event) => void;
      onOpen?: () => void;
    }
  ): () => void {
    void sessionId;
    void handlers;
    // Current backend has HTTP browser activity endpoints and Electron host snapshots, but no per-session browser WebSocket.
    return () => undefined;
  }

  getBrowserHostSnapshot(): Promise<BrowserHostSnapshot> {
    return window.lengrvis?.browserHost.getSnapshot() ?? Promise.resolve(emptyBrowserHostSnapshot(false));
  }

  openBrowserHost(request: BrowserHostOpenRequest): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.open(request) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  showBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.show(sessionId) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  hideBrowserHost(): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.hide() ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  setBrowserHostBounds(bounds: { x: number; y: number; width: number; height: number }): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.setBounds(bounds) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  pauseBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.pause(sessionId) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  resumeBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.resume(sessionId) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  takeoverBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.takeover(sessionId) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  releaseBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.release(sessionId) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  stopBrowserHost(sessionId: string): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.stop(sessionId) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  performBrowserHostAction(sessionId: string, action: BrowserAction): Promise<BrowserHostActionResult> {
    return window.lengrvis?.browserHost.performAction({ sessionId, action }) ?? Promise.resolve({
      ok: false,
      snapshot: emptyBrowserHostSnapshot(false),
      error: "Desktop browser host is unavailable"
    });
  }

  subscribeBrowserHostSnapshots(handler: (snapshot: BrowserHostSnapshot) => void): () => void {
    return window.lengrvis?.browserHost.onSnapshot(handler) ?? (() => undefined);
  }

  private async exportBrowserHostReplay(sessionId: string): Promise<ApiResponse<BrowserReplayExport> | null> {
    if (!window.lengrvis?.browserHost) return null;
    const receivedAt = new Date().toISOString();
    try {
      const snapshot = await this.getBrowserHostSnapshot();
      const session = snapshot.sessions.find((item) => item.id === sessionId);
      if (!session) return null;
      return {
        ok: true,
        status: 200,
        data: {
          ok: true,
          session,
          events: snapshot.events.filter((event) => event.session_id === sessionId)
        },
        receivedAt
      };
    } catch (error) {
      return {
        ok: false,
        status: 0,
        error: {
          code: "BROWSER_HOST_UNAVAILABLE",
          message: error instanceof Error ? error.message : "Browser host replay is unavailable"
        },
        receivedAt
      };
    }
  }

  private async hasBrowserHostSession(sessionId: string): Promise<boolean> {
    if (!window.lengrvis?.browserHost) return false;
    try {
      const snapshot = await this.getBrowserHostSnapshot();
      return snapshot.sessions.some((session) => session.id === sessionId);
    } catch {
      return false;
    }
  }

  openWindowsSettings(uri: string): Promise<ApiResponse<{ ok: boolean; uri: string; opened?: boolean; error?: string }>> {
    return this.request({
      endpoint: "/api/system/open-settings",
      method: "POST",
      body: { uri }
    });
  }

  listSchedules(): Promise<ApiResponse<BackendScheduledTask[]>> {
    return this.request<BackendScheduledTask[]>({ endpoint: "/api/schedules" });
  }

  createSchedule(input: { cron: string; goal: string; mode: string; note?: string }): Promise<ApiResponse<BackendScheduledTask>> {
    return this.request<BackendScheduledTask, typeof input>({
      endpoint: "/api/schedules",
      method: "POST",
      body: input
    });
  }

  deleteSchedule(scheduleId: string): Promise<ApiResponse<{ ok: boolean; id: string }>> {
    return this.request({ endpoint: `/api/schedules/${scheduleId}`, method: "DELETE" });
  }

  enableSchedule(scheduleId: string, enabled: boolean): Promise<ApiResponse<BackendScheduledTask>> {
    return this.request<BackendScheduledTask, { enabled: boolean }>({
      endpoint: `/api/schedules/${scheduleId}/enable`,
      method: "POST",
      body: { enabled }
    });
  }

  listMemories(): Promise<ApiResponse<BackendMemory[]>> {
    return this.request<BackendMemory[]>({ endpoint: "/api/memories" });
  }

  saveMemory(content: string, options: { tags?: string[]; taskId?: string; kind?: string } = {}): Promise<ApiResponse<BackendMemory>> {
    return this.request<BackendMemory, { content: string; tags: string[]; task_id: string; kind: string }>({
      endpoint: "/api/memories",
      method: "POST",
      body: {
        content,
        tags: options.tags ?? [],
        task_id: options.taskId ?? "",
        kind: options.kind ?? "fact"
      }
    });
  }

  recallMemory(query: string, options: { k?: number; tags?: string[] } = {}): Promise<ApiResponse<BackendMemory[]>> {
    return this.request<BackendMemory[], { query: string; k: number; tags: string[] }>({
      endpoint: "/api/memories/recall",
      method: "POST",
      body: { query, k: options.k ?? 5, tags: options.tags ?? [] }
    });
  }

  forgetMemory(memoryId: string): Promise<ApiResponse<{ ok: boolean; id: string }>> {
    return this.request({ endpoint: `/api/memories/${memoryId}`, method: "DELETE" });
  }

  previewRollback(taskId: string): Promise<ApiResponse<{ task_id: string; steps: unknown[]; count: number }>> {
    return this.request({ endpoint: `/api/tasks/${taskId}/rollback-preview` });
  }

  executeRollback(taskId: string): Promise<ApiResponse<{ executed: unknown[]; count: number }>> {
    const response = window.lengrvis?.tasks
      ? window.lengrvis.tasks.rollback(taskId)
      : this.request({ endpoint: `/api/tasks/${taskId}/rollback`, method: "POST" });
    return response as Promise<ApiResponse<{ executed: unknown[]; count: number }>>;
  }

  subscribeRunEvents(
    runId: string,
    handlers: JsonRealtimeHandlers<BackendRunStreamEvent>
  ): () => void {
    if (!runId) {
      return () => undefined;
    }

    const request = { endpoint: `/ws/runs/${encodeURIComponent(runId)}` };
    return subscribeJsonRealtime<BackendRunStreamEvent>(request, handlers);
  }

  getTaskExplain(taskId: string): Promise<ApiResponse<TaskExplain>> {
    return this.request<BackendTaskExplain>({
      endpoint: `/api/tasks/${taskId}/explain`,
      timeoutMs: 10_000
    }).then((response) => mapResponse(response, mapTaskExplain));
  }

  private async mapTaskEventWithRecordings(task: BackendTask): Promise<TaskEvent> {
    const base = mapTaskEvent(task);
    const timeline = await this.request<BackendTimeline>({
      endpoint: `/api/tasks/${task.id}/timeline`,
      timeoutMs: 10_000
    });
    if (!timeline.ok || !timeline.data) {
      return base;
    }
    const boundaryEvents = mapBoundaryEvents(timeline.data.boundary_events);
    return {
      ...base,
      recordings: mapTaskRecordings(timeline.data),
      cleanupPlan: base.cleanupPlan ?? cleanupPlanFromTimeline(timeline.data),
      boundaryEvents: boundaryEvents.length ? boundaryEvents : base.boundaryEvents
    };
  }

  clusterFiles(options: FileClusterOptions = {}): Promise<ApiResponse<BackendClusterResponse>> {
    const body: BackendClusterRequest = {};
    const groupBy = options.group_by ?? options.groupBy;
    const clusterBy = options.cluster_by ?? options.clusterBy;
    const metadataWeight = options.metadata_weight ?? options.metadataWeight;
    const imagePaths = options.image_paths ?? options.imagePaths;

    if (typeof options.k === "number") body.k = options.k;
    if (groupBy) body.group_by = groupBy;
    if (clusterBy) body.cluster_by = clusterBy;
    if (options.paths?.length) body.paths = options.paths;
    if (imagePaths?.length) body.image_paths = imagePaths;
    if (options.images?.length) body.images = options.images;
    if (typeof options.limit === "number") body.limit = options.limit;
    if (typeof metadataWeight === "number") body.metadata_weight = metadataWeight;

    return this.request<BackendClusterResponse, BackendClusterRequest>({
      endpoint: "/api/files/cluster",
      method: "POST",
      body,
      timeoutMs: 15_000
    });
  }

  listSkills(): Promise<ApiResponse<SkillsCatalog>> {
    return this.request<BackendSkillsCatalog>({ endpoint: "/api/skills" }).then((response) =>
      mapResponse(response, mapSkillsCatalog)
    );
  }

  importSkill(path: string): Promise<ApiResponse<SkillImportResult>> {
    if (window.lengrvis?.skills) {
      return window.lengrvis.skills.importPackage(path).then((response) =>
        mapResponse(response as ApiResponse<BackendSkillImportResult>, mapSkillImportResult)
      );
    }
    return this.request<BackendSkillImportResult, { path: string }>({
      endpoint: "/api/skills/import",
      method: "POST",
      body: { path },
      timeoutMs: 30_000
    }).then((response) => mapResponse(response, mapSkillImportResult));
  }

  refreshSkills(): Promise<ApiResponse<{ ok: boolean; toolCount: number; skillCount: number }>> {
    if (window.lengrvis?.skills) {
      return window.lengrvis.skills.refresh().then((response) =>
        mapResponse(response as ApiResponse<BackendSkillRefresh>, (data) => ({
          ok: Boolean(data.ok),
          toolCount: Number(data.tool_count ?? 0),
          skillCount: Number(data.skill_count ?? 0)
        }))
      );
    }
    return this.request<BackendSkillRefresh>({ endpoint: "/api/skills/refresh", method: "POST" }).then((response) =>
      mapResponse(response, (data) => ({
        ok: Boolean(data.ok),
        toolCount: Number(data.tool_count ?? 0),
        skillCount: Number(data.skill_count ?? 0)
      }))
    );
  }
}

function mapCommercePlanStatus(data: BackendCommercePlanStatus): CommercePlanStatus {
  return {
    plan: normalizeCommercePlan(data.plan),
    remoteDesktopEnabled: Boolean(data.remote_desktop_enabled),
    features: data.features as Record<CommerceFeature, boolean>,
    highRiskFeatures: data.high_risk_features as CommerceFeature[]
  };
}

function mapCommerceLicenseStatus(data: BackendCommerceLicenseStatus): CommerceLicenseStatus {
  return {
    state: data.state,
    present: Boolean(data.present),
    active: Boolean(data.active),
    expired: Boolean(data.expired),
    revoked: Boolean(data.revoked),
    verifierConfigured: Boolean(data.verifier_configured),
    managedBy: data.managed_by ?? undefined,
    requestedEnvPlan: data.requested_env_plan ? normalizeCommercePlan(data.requested_env_plan) : undefined,
    planEnvIgnored: Boolean(data.plan_env_ignored),
    licenseId: data.license_id ?? undefined,
    issuer: data.issuer ?? undefined,
    replaces: data.replaces ?? undefined,
    revocationCapable: Boolean(data.revocation_capable),
    revocationSource: data.revocation_source ?? undefined,
    revocationGeneratedAt: data.revocation_generated_at ?? undefined,
    plan: data.plan ? normalizeCommercePlan(data.plan) : undefined,
    subject: data.subject || undefined,
    seats: data.seats,
    subscriptionId: data.subscription_id ?? undefined,
    subscriptionStatus: data.subscription_status ?? undefined,
    renewsAt: data.renews_at ?? undefined,
    cancelAtPeriodEnd: Boolean(data.cancel_at_period_end),
    deviceId: data.device_id ?? undefined,
    orderRef: data.order_ref ?? undefined,
    issuedAt: data.issued_at ?? undefined,
    expiresAt: data.expires_at ?? undefined,
    errorCode: data.error_code
  };
}

function mapCommerceQuotaStatus(data: BackendCommerceQuotaStatus): CommerceQuotaStatus {
  const usage = data.usage
    ? {
        calls: Number(data.usage.calls || 0),
        totalTokens: Number(data.usage.total_tokens || 0),
        totalCostUsd: Number(data.usage.total_cost_usd || 0),
        windowHours: Number(data.usage.window_hours || data.window_hours || 0),
        lastEventAt: data.usage.last_event_at || undefined
      }
    : undefined;
  const windows = Array.isArray(data.windows)
    ? data.windows.map((window) => ({
        key: window.key || `${Number(window.window_hours || 0)}h`,
        windowHours: Number(window.window_hours || 0),
        limits: {
          totalTokens: window.limits.total_tokens,
          calls: window.limits.calls,
          totalCostUsd: window.limits.total_cost_usd
        },
        usage: window.usage
          ? {
              calls: Number(window.usage.calls || 0),
              totalTokens: Number(window.usage.total_tokens || 0),
              totalCostUsd: Number(window.usage.total_cost_usd || 0),
              windowHours: Number(window.usage.window_hours || window.window_hours || 0),
              lastEventAt: window.usage.last_event_at || undefined
            }
          : undefined,
        exceeded: Array.isArray(window.exceeded) ? window.exceeded.map(String) : []
      }))
    : [];
  return {
    plan: normalizeCommercePlan(data.plan),
    enforced: Boolean(data.enforced),
    unlimited: Boolean(data.unlimited),
    windowHours: Number(data.window_hours || 0),
    limits: {
      totalTokens: data.limits.total_tokens,
      calls: data.limits.calls,
      totalCostUsd: data.limits.total_cost_usd
    },
    usage,
    exceeded: Array.isArray(data.exceeded) ? data.exceeded.map(String) : [],
    windows:
      windows.length > 0
        ? windows
        : [
            {
              key: `${Number(data.window_hours || 0)}h`,
              windowHours: Number(data.window_hours || 0),
              limits: {
                totalTokens: data.limits.total_tokens,
                calls: data.limits.calls,
                totalCostUsd: data.limits.total_cost_usd
              },
              usage,
              exceeded: Array.isArray(data.exceeded) ? data.exceeded.map(String) : []
            }
          ]
  };
}

function normalizeCommercePlan(plan: "free" | "pro" | "max" | "team"): CommercePlan {
  return plan === "team" ? "max" : plan;
}
