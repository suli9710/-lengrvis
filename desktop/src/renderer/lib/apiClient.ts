import type {
  AgentConversation,
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
  ContextUsage,
  DesktopWebSocketSubscribeRequest,
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
  InstalledApp,
  InstalledSkill,
  IntentSuggestion,
  LocalLibraryItem,
  LocalLibraryResponse,
  LLMCostSummary,
  LLMHealthStatus,
  LLMProfile,
  LocalLLMHealth,
  LocalModelReadiness,
  LocalModelSetupPlan,
  PerceptionSuggestionLaunchRequest,
  PerceptionSuggestionLaunchResponse,
  Plan,
  SafetyReview,
  SkillImportResult,
  SkillsCatalog,
  StartupItem,
  SystemDiagnostic,
  SystemInfo,
  SystemProcess,
  TaskEvent,
  TaskBoundaryEvent,
  RunEventPayload,
  TaskExplain,
  TaskExplainChainItem,
  TaskExplainEvidence,
  TaskExplainMessage,
  TaskExplainReview,
  TaskExplainStep
} from "../../shared/types";
import type { DesktopMobilePairingCode } from "../../shared/mobilePairingPayload";
import {
  zhApprovalType,
  zhBackendTaskStatus,
  zhBackendText,
  zhRiskLevel,
  zhSafetyVerdict,
  zhToolName,
  zhUserFacingError
} from "./zh";

export const FALLBACK_BACKEND_URL = "http://127.0.0.1:8000";
const DEFAULT_TIMEOUT_MS = 30_000;
const WS_RETRY_DELAY_MS = 2_500;

export type RealtimeConnectionState =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed"
  | "error"
  | "unauthorized"
  | "policy_violation"
  | "bad_message";

export interface RealtimeConnectionStatus {
  state: RealtimeConnectionState;
  endpoint: string;
  at: string;
  attempt?: number;
  code?: number;
  reason?: string;
  wasClean?: boolean;
  retryInMs?: number;
  message?: string;
  rawMessage?: string;
}

export interface JsonRealtimeHandlers<TMessage> {
  onMessage: (message: TMessage) => void;
  onError?: (error: Event) => void;
  onOpen?: () => void;
  onStatus?: (status: RealtimeConnectionStatus) => void;
  onBadMessage?: (status: RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string }) => void;
}

export interface LocalModelInstallRequest {
  model?: string;
}

export interface LocalModelInstallResponse {
  ok?: boolean;
  model?: string;
  message?: string;
  error?: string;
  progress?: unknown;
  final?: unknown;
}

export interface OllamaActionResponse {
  ok?: boolean;
  model?: string;
  message?: string;
  error?: string;
  source?: string;
  executable?: string;
  models_dir?: string;
}

export class LengrvisApiClient {
  private lastLoadedSettings: AppSettings | null = null;

  async request<TResponse, TBody = unknown>(request: ApiRequest<TBody>): Promise<ApiResponse<TResponse>> {
    if (!window.lengrvis) {
      return requestBackendDirect<TResponse, TBody>(FALLBACK_BACKEND_URL, request);
    }

    return window.lengrvis.api.request<TResponse, TBody>(request);
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
            status: meta.status ?? "ok"
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
        appVersion: window.lengrvis?.versions.app ?? "0.1.0",
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
            document_id: body.documentId,
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
    return this.request({ endpoint: `/api/tasks/${taskId}/rollback`, method: "POST" });
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

interface BackendScheduledTask {
  id: string;
  cron: string;
  goal: string;
  mode: string;
  enabled: boolean;
  next_run_at?: string;
  last_run_at?: string;
  last_status?: string;
  last_task_id?: string;
  note?: string;
  created_at?: string;
  updated_at?: string;
}

interface BackendMemory {
  id: string;
  kind: string;
  content: string;
  tags: string[];
  task_id?: string;
  source?: string;
  use_count?: number;
  last_used_at?: string;
  created_at?: string;
}

async function requestBackendDirect<TResponse, TBody = unknown>(
  baseUrl: string,
  request: ApiRequest<TBody>
): Promise<ApiResponse<TResponse>> {
  const receivedAt = new Date().toISOString();

  try {
    const url = buildRequestUrl(baseUrl, request);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), request.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    const response = await fetch(url, {
      method: request.method ?? "GET",
      headers: {
        Accept: "application/json",
        ...(request.body ? { "Content-Type": "application/json" } : {})
      },
      body: request.body ? JSON.stringify(request.body) : undefined,
      signal: controller.signal
    });
    window.clearTimeout(timeout);

    const data = await parseResponseBody(response);
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: {
          code: `HTTP_${response.status}`,
          message: zhUserFacingError(getErrorMessage(data, response.statusText || `HTTP ${response.status}`)),
          details: data
        },
        receivedAt
      };
    }

    return { ok: true, status: response.status, data: data as TResponse, receivedAt };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: {
        code: "NETWORK_ERROR",
        message: zhUserFacingError(error instanceof Error ? error.message : "Backend request failed")
      },
      receivedAt
    };
  }
}

function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  const url = buildRendererLoopbackBackendApiUrl(baseUrl, request.endpoint, request.query);
  if (!url) {
    throw new Error("Renderer direct backend requests require a loopback HTTP(S) backend");
  }
  return new URL(url);
}

function getBackendBaseUrl(baseUrl?: string): string {
  return normalizeRendererLoopbackBackendBaseUrl(baseUrl ?? window.lengrvis?.backendBaseUrl) ?? FALLBACK_BACKEND_URL;
}

export function normalizeRendererLoopbackBackendBaseUrl(baseUrl?: string): string | null {
  const candidate = typeof baseUrl === "string" && baseUrl.trim() ? baseUrl.trim() : FALLBACK_BACKEND_URL;
  try {
    const url = new URL(candidate);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (!isRendererLoopbackHostname(url.hostname)) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function buildRendererLoopbackBackendApiUrl(
  baseUrl: string | undefined,
  endpoint: string,
  query?: Record<string, ApiQueryValue>
): string | null {
  const backendBaseUrl = normalizeRendererLoopbackBackendBaseUrl(baseUrl);
  if (!backendBaseUrl) return null;

  const safeEndpoint = validateRendererBackendRelativeEndpoint(endpoint, ["/api"]);
  const url = new URL(safeEndpoint, backendBaseUrl);
  if (url.origin !== new URL(backendBaseUrl).origin) return null;
  appendRendererBackendQuery(url, query);
  return url.toString();
}

export function absoluteRendererLoopbackBackendUrl(pathOrUrl: string, baseUrl?: string): string {
  if (!pathOrUrl) return "";
  const backendBaseUrl = normalizeRendererLoopbackBackendBaseUrl(baseUrl);
  if (!backendBaseUrl) return "";
  try {
    const url = new URL(pathOrUrl, backendBaseUrl);
    if (url.origin !== new URL(backendBaseUrl).origin) return "";
    if (!["http:", "https:"].includes(url.protocol)) return "";
    if (!isRendererLoopbackHostname(url.hostname)) return "";
    return url.toString();
  } catch {
    return "";
  }
}

export function buildRendererLoopbackBackendWebSocketUrl(
  baseUrl: string | undefined,
  endpoint: string,
  query?: Record<string, ApiQueryValue>
): string | null {
  const backendBaseUrl = normalizeRendererLoopbackBackendBaseUrl(baseUrl);
  if (!backendBaseUrl) return null;

  const safeEndpoint = validateRendererBackendRelativeEndpoint(endpoint, ["/ws", "/api/ws"]);
  const url = new URL(safeEndpoint, backendBaseUrl);
  if (url.origin !== new URL(backendBaseUrl).origin) return null;
  appendRendererBackendQuery(url, query);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function validateRendererBackendRelativeEndpoint(endpoint: string, allowedRoots: readonly string[]): string {
  if (typeof endpoint !== "string") {
    throw new Error("Renderer backend endpoint is required");
  }
  if (!endpoint || endpoint.length > 512) {
    throw new Error("Renderer backend endpoint length is invalid");
  }
  if (endpoint.trim() !== endpoint || /\s|[\u0000-\u001F\u007F]/.test(endpoint)) {
    throw new Error("Renderer backend endpoint contains unsafe characters");
  }
  if (endpoint.includes("?") || endpoint.includes("#")) {
    throw new Error("Renderer backend endpoint must not include query strings or fragments");
  }
  if (
    !endpoint.startsWith("/") ||
    endpoint.startsWith("//") ||
    endpoint.includes("//") ||
    endpoint.includes("\\") ||
    /^[a-z][a-z0-9+.-]*:/i.test(endpoint)
  ) {
    throw new Error("Renderer backend endpoints must be backend-relative");
  }
  if (/%2f|%5c/i.test(endpoint)) {
    throw new Error("Renderer backend endpoint must not contain encoded path separators");
  }

  let decodedPath = "";
  try {
    decodedPath = decodeURIComponent(endpoint);
  } catch {
    throw new Error("Renderer backend endpoint encoding is invalid");
  }
  if (decodedPath.includes("\\") || decodedPath.includes("//")) {
    throw new Error("Renderer backend endpoint contains unsafe path separators");
  }
  if (decodedPath.split("/").some((segment) => segment === "." || segment === "..")) {
    throw new Error("Renderer backend endpoint contains unsafe path segments");
  }
  if (!allowedRoots.some((root) => decodedPath === root || decodedPath.startsWith(`${root}/`))) {
    throw new Error("Renderer backend endpoint targets an unsupported backend path");
  }
  return endpoint;
}

function appendRendererBackendQuery(url: URL, query?: Record<string, ApiQueryValue>): void {
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === null || value === undefined) continue;
    if (!isSafeRendererBackendQueryKey(key)) {
      throw new Error("Renderer backend query key is unsafe");
    }
    if (typeof value === "number" && !Number.isFinite(value)) {
      throw new Error("Renderer backend query value must be finite");
    }
    if (!["string", "number", "boolean"].includes(typeof value)) {
      throw new Error("Renderer backend query values must be primitive");
    }
    url.searchParams.set(key, String(value));
  }
}

function isSafeRendererBackendQueryKey(key: string): boolean {
  return !["__proto__", "constructor", "prototype"].includes(key) && /^[A-Za-z0-9_.:-]{1,96}$/.test(key);
}

function isRendererLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "localhost" || normalized === "::1" || normalized === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(normalized);
}

function subscribeJsonRealtime<TMessage>(
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): () => void {
  if (window.lengrvis?.realtime) {
    return subscribeDesktopJsonStream(request, handlers);
  }

  if (typeof WebSocket === "undefined") {
    return () => undefined;
  }

  if (!isWebOnlyDevRealtimeFallbackEnabled()) {
    emitRealtimeStatus(request, handlers, "error", {
      message: "Desktop realtime bridge is unavailable"
    });
    return () => undefined;
  }

  const url = buildRendererLoopbackBackendWebSocketUrl(getBackendBaseUrl(), request.endpoint, request.query);
  if (!url) {
    emitRealtimeStatus(request, handlers, "error", {
      message: "Renderer web-only realtime fallback requires a loopback backend"
    });
    return () => undefined;
  }

  return subscribeWebOnlyDevJsonStream(url, request, handlers);
}

function isWebOnlyDevRealtimeFallbackEnabled(): boolean {
  return !window.lengrvis && import.meta.env.DEV;
}

function subscribeDesktopJsonStream<TMessage>(
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): () => void {
  let unsubscribeSocket: (() => void) | null = null;
  let closedByCaller = false;
  let retryId: number | undefined;
  let reconnectAttempt = 0;

  const connect = () => {
    const state = reconnectAttempt > 0 ? "reconnecting" : "connecting";
    emitRealtimeStatus(request, handlers, state, {
      attempt: reconnectAttempt,
      retryInMs: reconnectAttempt > 0 ? WS_RETRY_DELAY_MS : undefined
    });

    unsubscribeSocket = window.lengrvis!.realtime.subscribe(request, {
      onOpen: () => {
        reconnectAttempt = 0;
        emitRealtimeStatus(request, handlers, "open");
        handlers.onOpen?.();
      },
      onMessage: (data) => {
        parseJsonRealtimeMessage(data, request, handlers);
      },
      onError: (error) => {
        const status = realtimeStatusFromError(request, error);
        handlers.onStatus?.(status);
        handlers.onError?.(makeWebSocketErrorEvent(status.message));
      },
      onClose: (event) => {
        unsubscribeSocket = null;
        if (closedByCaller) return;
        const status = realtimeStatusFromClose(request, event, reconnectAttempt + 1);
        if (shouldRetryRealtime(status)) {
          reconnectAttempt += 1;
          handlers.onStatus?.({
            ...status,
            state: "reconnecting",
            attempt: reconnectAttempt,
            retryInMs: WS_RETRY_DELAY_MS
          });
          retryId = window.setTimeout(connect, WS_RETRY_DELAY_MS);
          return;
        }
        handlers.onStatus?.(status);
      }
    });
  };

  connect();

  return () => {
    closedByCaller = true;
    if (retryId !== undefined) window.clearTimeout(retryId);
    unsubscribeSocket?.();
    unsubscribeSocket = null;
  };
}

function subscribeWebOnlyDevJsonStream<TMessage>(
  url: string,
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): () => void {
  let socket: WebSocket | null = null;
  let closedByCaller = false;
  let retryId: number | undefined;
  let reconnectAttempt = 0;

  const connect = () => {
    const state = reconnectAttempt > 0 ? "reconnecting" : "connecting";
    emitRealtimeStatus(request, handlers, state, {
      attempt: reconnectAttempt,
      retryInMs: reconnectAttempt > 0 ? WS_RETRY_DELAY_MS : undefined
    });

    socket = new WebSocket(url);

    socket.onopen = () => {
      reconnectAttempt = 0;
      emitRealtimeStatus(request, handlers, "open");
      handlers.onOpen?.();
    };
    socket.onmessage = (event) => {
      parseJsonRealtimeMessage(event.data, request, handlers);
    };
    socket.onerror = (event) => {
      const status = realtimeStatusFromError(request, event);
      handlers.onStatus?.(status);
      handlers.onError?.(event);
    };
    socket.onclose = (event) => {
      socket = null;
      if (closedByCaller) return;
      const status = realtimeStatusFromClose(request, event, reconnectAttempt + 1);
      if (shouldRetryRealtime(status)) {
        reconnectAttempt += 1;
        handlers.onStatus?.({
          ...status,
          state: "reconnecting",
          attempt: reconnectAttempt,
          retryInMs: WS_RETRY_DELAY_MS
        });
        retryId = window.setTimeout(connect, WS_RETRY_DELAY_MS);
        return;
      }
      handlers.onStatus?.(status);
    };
  };

  connect();

  return () => {
    closedByCaller = true;
    if (retryId !== undefined) window.clearTimeout(retryId);
    socket?.close();
    socket = null;
  };
}

function parseJsonRealtimeMessage<TMessage>(
  data: unknown,
  request: DesktopWebSocketSubscribeRequest,
  handlers: JsonRealtimeHandlers<TMessage>
): void {
  const rawMessage = rawRealtimeMessage(data);
  try {
    handlers.onMessage(JSON.parse(rawMessage) as TMessage);
  } catch (error) {
    const status = createRealtimeStatus(request, "bad_message", {
      message: error instanceof Error ? error.message : "Malformed realtime message",
      rawMessage
    });
    handlers.onBadMessage?.(status as RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string });
    handlers.onStatus?.(status);
  }
}

function emitRealtimeStatus<TMessage>(
  request: DesktopWebSocketSubscribeRequest,
  handlers: Pick<JsonRealtimeHandlers<TMessage>, "onStatus">,
  state: RealtimeConnectionState,
  patch: Partial<RealtimeConnectionStatus> = {}
): void {
  handlers.onStatus?.(createRealtimeStatus(request, state, patch));
}

function createRealtimeStatus(
  request: DesktopWebSocketSubscribeRequest,
  state: RealtimeConnectionState,
  patch: Partial<RealtimeConnectionStatus> = {}
): RealtimeConnectionStatus {
  return {
    state,
    endpoint: request.endpoint,
    at: new Date().toISOString(),
    ...patch
  };
}

function realtimeStatusFromError(
  request: DesktopWebSocketSubscribeRequest,
  error: unknown
): RealtimeConnectionStatus {
  const message = realtimeErrorMessage(error);
  return createRealtimeStatus(request, classifyRealtimeIssue(undefined, message) ?? "error", { message });
}

function realtimeStatusFromClose(
  request: DesktopWebSocketSubscribeRequest,
  event: { code?: number; reason?: string; wasClean?: boolean },
  attempt: number
): RealtimeConnectionStatus {
  const code = event.code;
  const reason = event.reason ?? "";
  const state = classifyRealtimeIssue(code, reason);
  if (state) {
    return createRealtimeStatus(request, state, {
      code,
      reason,
      wasClean: event.wasClean,
      attempt
    });
  }
  return createRealtimeStatus(request, event.wasClean || code === 1000 ? "closed" : "reconnecting", {
    code,
    reason,
    wasClean: event.wasClean,
    attempt
  });
}

function classifyRealtimeIssue(code?: number, message = ""): RealtimeConnectionState | null {
  const lower = message.toLowerCase();
  if (
    code === 1008 ||
    lower.includes("1008") ||
    lower.includes("policy violation") ||
    lower.includes("policy_violation")
  ) {
    return "policy_violation";
  }
  if (
    lower.includes("401") ||
    lower.includes("unauthorized") ||
    lower.includes("missing desktop api token")
  ) {
    return "unauthorized";
  }
  return null;
}

function shouldRetryRealtime(status: RealtimeConnectionStatus): boolean {
  return status.state === "reconnecting" || status.state === "error";
}

function realtimeErrorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error && typeof (error as { message?: unknown }).message === "string") {
    return (error as { message: string }).message;
  }
  if (error instanceof Error) return error.message;
  return "Realtime connection error";
}

function rawRealtimeMessage(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    const serialized = JSON.stringify(value);
    if (serialized) return serialized;
  } catch {
    // Fall back to String below so malformed values are still visible to the UI.
  }
  return String(value);
}

function buildBrowserSessionWebSocketUrl(baseUrl: string, sessionId: string): string {
  return buildRendererLoopbackBackendWebSocketUrl(baseUrl, `/api/ws/browser/sessions/${encodeURIComponent(sessionId)}`) ?? "";
}

function makeWebSocketErrorEvent(message?: string): Event {
  return typeof Event === "function"
    ? Object.assign(new Event("error"), { message })
    : ({ type: "error", message } as unknown as Event);
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (response.status === 204) return undefined;
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  return text ? { message: text } : undefined;
}

function getErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object") {
    const direct = (data as { message?: unknown }).message;
    if (typeof direct === "string") return direct;
    const nested = (data as { error?: { message?: unknown } }).error?.message;
    if (typeof nested === "string") return nested;
  }
  return fallback || "Backend request failed";
}

function mapResponse<TInput, TOutput>(
  response: ApiResponse<TInput>,
  mapper: (data: TInput) => TOutput
): ApiResponse<TOutput> {
  if (!response.ok || response.data === undefined) {
    return {
      ok: response.ok,
      status: response.status,
      error: response.error,
      receivedAt: response.receivedAt
    };
  }
  return {
    ok: true,
    status: response.status,
    data: mapper(response.data),
    receivedAt: response.receivedAt
  };
}

function compactLocalModelRequest(request: LocalModelInstallRequest): LocalModelInstallRequest {
  const model = String(request.model ?? "").trim();
  return model ? { model } : {};
}

function mapTaskState(status: string): TaskEvent["state"] {
  if (status === "completed") return "completed";
  if (status === "failed" || status === "denied" || status === "cancelled") return "failed";
  if (status === "paused") return "paused";
  if (status === "waiting_user_approval" || status === "awaiting_approval") return "blocked";
  return "running";
}

function mapRunCreateResponse(data: BackendRunCreateResponse | BackendSuggestionLaunchResponse, fallbackTitle: string): PerceptionSuggestionLaunchResponse {
  const run = "run" in data ? data.run : undefined;
  const runId = data.run_id ?? run?.run_id ?? crypto.randomUUID();
  const phase = data.phase ?? run?.phase ?? "running";
  const engine = data.engine ?? run?.engine ?? "auto";
  const backendMessage = "message" in data ? data.message : undefined;
  const title = backendMessage ?? run?.message ?? fallbackTitle;
  return {
    runId,
    engine,
    message: {
      id: `${runId}-suggestion-started`,
      role: "assistant",
      author: "Lengrvis",
      content: `已开始处理建议任务，当前状态：${zhBackendTaskStatus(phase)}。`,
      createdAt: new Date().toISOString(),
      status: "sent"
    },
    taskUpdates: [
      {
        id: runId,
        runId,
        title,
        description: `状态：${zhBackendTaskStatus(phase)}`,
        state: mapTaskState(phase),
        agent: runEngineAgentName(engine),
        createdAt: run?.created_at ?? new Date().toISOString(),
        updatedAt: run?.updated_at ?? run?.created_at ?? new Date().toISOString()
      }
    ]
  };
}

function mapRunTaskEvent(run: BackendRunState): TaskEvent {
  const cleanupPlan = cleanupPlanFromApprovalPayload(run.cleanup_plan ?? run.cleanupPlan ?? run.diff_preview);
  return {
    id: run.run_id,
    runId: run.run_id,
    title: run.message || run.run_id,
    description: zhBackendText(run.error) || `状态：${zhBackendTaskStatus(run.phase)}（${zhRunEngine(run.engine)}）`,
    state: mapTaskState(run.phase),
    agent: runEngineAgentName(run.engine),
    createdAt: run.created_at || new Date().toISOString(),
    updatedAt: run.updated_at || run.created_at || new Date().toISOString(),
    recordings: [],
    cleanupPlan
  };
}

function mapBoundaryEvents(value: unknown): TaskBoundaryEvent[] {
  return arrayOfObjects(value).map((event) => ({
    id: String(event.id ?? crypto.randomUUID()),
    kind: String(event.kind ?? "boundary"),
    title: zhBackendText(String(event.title ?? "工程边界")),
    detail: zhBackendText(String(event.detail ?? "")),
    severity: String(event.severity ?? "info"),
    stepId: optionalString(event.step_id ?? event.stepId),
    createdAt: String(event.created_at ?? event.createdAt ?? new Date().toISOString()),
    payload: recordOrUndefined(event.payload)
  }));
}

function zhRunEngine(engine?: string): string {
  if (engine === "developer") return "开发执行";
  if (engine === "os") return "电脑执行";
  if (engine === "auto") return "自动选择";
  return engine || "未知执行";
}

function runEngineAgentName(engine?: string): string {
  if (engine === "developer") return "开发执行引擎";
  if (engine === "os") return "电脑执行引擎";
  return "执行引擎";
}

function latestRunState(runs: BackendRunState[]): BackendRunState | null {
  return [...runs].sort((left, right) => {
    const leftTime = Date.parse(left.updated_at || left.created_at || "");
    const rightTime = Date.parse(right.updated_at || right.created_at || "");
    return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
  })[0] ?? null;
}

function hasRunTimelineEvents(timeline: BackendRunTimeline): boolean {
  return Boolean(timeline.events?.length);
}

function mapRunPlan(run: BackendRunState, timeline: BackendRunTimeline): Plan {
  const planEvent = [...(timeline.events ?? [])].reverse().find((event) => event.name === "plan.generated");
  const planPayload = (planEvent?.payload?.plan ?? planEvent?.payload?.structured_payload) as BackendPlan | undefined;
  if (!planPayload?.steps?.length) {
    return {
      ...emptyPlan(),
      id: run.run_id,
      title: run.message || run.run_id,
      objective: zhBackendText(run.error) || `状态：${zhBackendTaskStatus(run.phase)}`,
      updatedAt: run.updated_at
    };
  }
  return {
    id: planPayload.id || run.run_id,
    title: planPayload.goal || run.message || run.run_id,
    objective: planPayload.assumptions?.join(" ") || run.message,
    updatedAt: run.updated_at,
    steps: planPayload.steps.map((step) => ({
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
}

function mapRunConversation(run: BackendRunState, events: BackendRunEvent[]): AgentConversation {
  return {
    id: `${run.run_id}-events`,
    title: run.message || run.run_id,
    status: run.phase === "completed" ? "done" : run.phase === "awaiting_approval" ? "waiting" : "running",
    messages: events.map((event) => {
      const payload = event.payload ?? {};
      const agent = String(payload.from_agent ?? runEngineAgentName(run.engine));
      const content = String(payload.content ?? payload.transition_reason ?? event.name);
      return {
        id: event.id,
        role: "assistant" as const,
        name: agent,
        agent,
        content: zhBackendText(content),
        createdAt: event.created_at,
        metadata: { ...payload, event_type: event.name },
        kind: mapRunEventKind(event.name)
      };
    })
  };
}

function mapRunEventKind(name: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (name === "tool.result" || name === "run.completed") return "result";
  if (name === "approval.needed" || name === "run.waiting_approval") return "handoff";
  if (name === "tool.progress") return "observation";
  return "action";
}

function mapCommandInfo(command: BackendCommandInfo): CommandInfo {
  return {
    name: String(command.name ?? ""),
    title: String(command.title ?? command.name ?? ""),
    description: String(command.description ?? ""),
    category: String(command.category ?? ""),
    inputSchema: (command.input_schema && typeof command.input_schema === "object" ? command.input_schema : {}) as Record<string, unknown>
  };
}

function mapCommandExecutionResult(result: BackendCommandExecutionResult): CommandExecutionResult {
  return {
    ok: Boolean(result.ok),
    command: String(result.command ?? ""),
    title: result.title ? String(result.title) : undefined,
    result: result.result,
    diagnostics: Array.isArray(result.diagnostics) ? result.diagnostics.map(String) : undefined,
    error: result.error ? String(result.error) : undefined,
    nextAction: result.next_action ? String(result.next_action) : undefined
  };
}

function mapLocalLibraryResponse(data: BackendLocalLibraryResponse): LocalLibraryResponse {
  return {
    section: String(data.section ?? "gallery"),
    roots: data.roots ?? [],
    items: (data.items ?? []).map(mapLocalLibraryItem),
    count: Number(data.count ?? data.items?.length ?? 0),
    total: Number(data.total ?? data.items?.length ?? 0),
    scanned: Number(data.scanned ?? 0),
    truncated: Boolean(data.truncated),
    stats: {
      size: Number(data.stats?.size ?? 0),
      byExtension: data.stats?.by_extension ?? {}
    }
  };
}

function mapLocalLibraryItem(item: BackendLocalLibraryItem): LocalLibraryItem {
  return {
    id: String(item.id ?? item.path),
    path: String(item.path ?? ""),
    name: String(item.name ?? item.path ?? ""),
    parent: String(item.parent ?? ""),
    kind: String(item.kind ?? "document"),
    extension: String(item.extension ?? ""),
    mimeType: String(item.mime_type ?? ""),
    size: Number(item.size ?? 0),
    createdAt: Number(item.created_at ?? 0),
    modifiedAt: Number(item.modified_at ?? 0),
    previewUrl: String(item.preview_url ?? ""),
    groupLabel: String(item.group_label ?? ""),
    iconUrl: String(item.icon_url ?? ""),
    width: Number(item.width ?? 0),
    height: Number(item.height ?? 0)
  };
}

function mapDocumentIR(data: BackendDocumentIR): DocumentIR {
  const path = String(data.path ?? "");
  const blocks = (data.blocks ?? []).map(mapDocumentBlock);
  const tables = [
    ...(data.tables ?? []).map(mapDocumentTable),
    ...blocks
      .filter((block) => block.type === "table" && (block.columns?.length || block.rows?.length))
      .map((block, index) => ({
        id: block.id || `table-${index + 1}`,
        title: block.text,
        columns: block.columns ?? [],
        rows: block.rows ?? [],
        page: block.page,
        sourceBlockId: block.id
      }))
  ];
  return {
    id: String(data.id ?? data.document_id ?? path),
    path,
    title: String(data.title ?? data.name ?? fileNameFromPath(path) ?? "文档"),
    mimeType: optionalString(data.mime_type ?? data.mimeType),
    language: optionalString(data.language),
    summary: optionalString(data.summary),
    text: optionalString(data.text),
    truncated: data.truncated === undefined ? undefined : Boolean(data.truncated),
    blocks,
    tables,
    citations: (data.citations ?? []).map(mapDocumentCitation),
    metadata: recordOrUndefined(data.metadata),
    createdAt: optionalString(data.created_at ?? data.createdAt)
  };
}

function mapDocumentBlock(block: BackendDocumentBlock): DocumentIR["blocks"][number] {
  const rows = tableRowsFromUnknown(block.rows);
  return {
    id: String(block.id ?? block.block_id ?? crypto.randomUUID()),
    type: String(block.type ?? block.kind ?? "paragraph"),
    text: optionalString(block.text ?? block.content),
    level: numberOrUndefined(block.level),
    page: numberOrUndefined(block.page),
    order: numberOrUndefined(block.order ?? block.index),
    columns: stringArray(block.columns),
    rows,
    metadata: recordOrUndefined(block.metadata)
  };
}

function mapDocumentTable(table: BackendDocumentTable): DocumentTable {
  return {
    id: String(table.id ?? table.table_id ?? crypto.randomUUID()),
    title: optionalString(table.title ?? table.name),
    columns: stringArray(table.columns),
    rows: tableRowsFromUnknown(table.rows),
    page: numberOrUndefined(table.page),
    sourceBlockId: optionalString(table.source_block_id ?? table.sourceBlockId)
  };
}

function mapDocumentCitation(citation: BackendDocumentCitation, index = 0): DocumentCitation {
  const label = String(citation.label ?? citation.id ?? `引用 ${index + 1}`);
  return {
    id: String(citation.id ?? label),
    label,
    text: String(citation.text ?? citation.snippet ?? citation.content ?? ""),
    path: optionalString(citation.path),
    blockId: optionalString(citation.block_id ?? citation.blockId),
    page: numberOrUndefined(citation.page),
    score: numberOrUndefined(citation.score)
  };
}

function mapDocumentAskResponse(data: BackendDocumentAskResponse): DocumentAskResponse {
  const sourceChunks = (data.source_chunks ?? data.sources ?? []).map(mapDocumentCitation);
  const citationItems = arrayOfObjects(data.citation_items ?? data.citations_detail ?? data.citations);
  return {
    answer: String(data.answer ?? data.summary ?? ""),
    citations: citationItems.length ? citationItems.map(mapDocumentCitation) : sourceChunks,
    sourceChunks,
    note: optionalString(data.note)
  };
}

function mapDocumentCompareResponse(data: BackendDocumentCompareResponse): DocumentCompareResponse {
  return {
    summary: String(data.summary ?? ""),
    documents: (data.documents ?? []).map(mapDocumentIR),
    differences: (data.differences ?? data.items ?? []).map((item, index) => ({
      id: String(item.id ?? `difference-${index + 1}`),
      title: String(item.title ?? item.field ?? `差异 ${index + 1}`),
      detail: String(item.detail ?? item.summary ?? item.text ?? ""),
      severity: optionalString(item.severity),
      citations: (item.citations ?? []).map(mapDocumentCitation)
    })),
    tables: (data.tables ?? []).map(mapDocumentTable),
    note: optionalString(data.note)
  };
}

function cleanupScanRequestFor(body: CleanupScanRequest): BackendCleanupScanRequest {
  return {
    roots: body.roots,
    threshold_mb: body.thresholdMb,
    include_caches: body.includeCaches
  };
}

function mapCleanupPlan(input: BackendCleanupPlan): CleanupPlan {
  const plan = normalizeCleanupPlan(input);
  return {
    id: String(plan.id ?? plan.plan_id ?? crypto.randomUUID()),
    contentHash: optionalString(plan.content_hash ?? plan.contentHash),
    title: String(plan.title ?? "清理计划"),
    summary: optionalString(plan.summary ?? plan.detail),
    status: optionalString(plan.status),
    createdAt: optionalString(plan.created_at ?? plan.createdAt),
    updatedAt: optionalString(plan.updated_at ?? plan.updatedAt),
    totalBytes: numberOrUndefined(plan.total_bytes ?? plan.totalBytes),
    reclaimableBytes: numberOrUndefined(plan.reclaimable_bytes ?? plan.reclaimableBytes ?? plan.freed_bytes ?? plan.freedBytes),
    permanentDeleteBytes: numberOrUndefined(plan.permanent_delete_bytes ?? plan.permanentDeleteBytes),
    trashBytes: numberOrUndefined(plan.trash_bytes ?? plan.trashBytes),
    riskWarnings: stringArray(plan.risk_warnings ?? plan.riskWarnings ?? plan.warnings),
    items: cleanupItemsForPlan(plan)
  };
}

function normalizeCleanupPlan(input: BackendCleanupPlan): BackendCleanupPlan {
  if (input && typeof input === "object" && input.cleanup_plan && typeof input.cleanup_plan === "object") {
    return input.cleanup_plan as BackendCleanupPlan;
  }
  if (input && typeof input === "object" && input.plan && typeof input.plan === "object") {
    return input.plan as BackendCleanupPlan;
  }
  return input;
}

function cleanupItemsForPlan(plan: BackendCleanupPlan): CleanupItem[] {
  const direct = arrayOfObjects(plan.items).map((item) => mapCleanupItem(item, "suggestion_only"));
  if (direct.length) return direct;

  const buckets = plan.buckets && typeof plan.buckets === "object" ? plan.buckets : {};
  return [
    ...arrayOfObjects(buckets.direct_delete ?? buckets.permanent_delete).map((item) => mapCleanupItem(item, "direct_delete")),
    ...arrayOfObjects(buckets.recycle_bin ?? buckets.trash).map((item) => mapCleanupItem(item, "recycle_bin")),
    ...arrayOfObjects(buckets.suggestion_only ?? buckets.info_only).map((item) => mapCleanupItem(item, "suggestion_only")),
    ...arrayOfObjects(buckets.immediate).map((item) => mapCleanupItem(item, "recycle_bin")),
    ...arrayOfObjects(buckets.approval).map((item) => mapCleanupItem(item, "recycle_bin"))
  ];
}

function mapCleanupItem(item: BackendCleanupItem, fallbackBucket: string): CleanupItem {
  const bucket = String(item.bucket ?? fallbackBucket);
  const action = String(item.action ?? "");
  const disposition = cleanupDispositionFor(item, bucket, action);
  const sizeBytes = numberOrUndefined(item.size_bytes ?? item.sizeBytes ?? item.bytes);
  const sizeMb = numberOrUndefined(item.size_mb ?? item.sizeMb);
  return {
    id: String(item.id ?? item.path ?? crypto.randomUUID()),
    path: String(item.path ?? ""),
    name: optionalString(item.name),
    action,
    disposition,
    bucket,
    sizeBytes: sizeBytes ?? (sizeMb === undefined ? undefined : Math.round(sizeMb * 1024 * 1024)),
    sizeMb,
    category: optionalString(item.category),
    detail: optionalString(item.detail ?? item.description),
    reason: optionalString(item.reason),
    riskLevel: optionalString(item.risk_level ?? item.riskLevel),
    canRollback: item.can_rollback === undefined && item.canRollback === undefined
      ? disposition === "trash"
      : Boolean(item.can_rollback ?? item.canRollback),
    selected: item.selected === undefined ? undefined : Boolean(item.selected),
    modifiedAt: optionalString(item.modified_at ?? item.modifiedAt),
    metadata: recordOrUndefined(item.metadata)
  };
}

function cleanupDispositionFor(item: BackendCleanupItem, bucket: string, action: string): CleanupItem["disposition"] {
  const explicit = item.disposition ?? item.mode ?? item.delete_mode;
  if (typeof explicit === "string" && explicit) return explicit;
  const normalized = `${bucket} ${action}`.toLowerCase();
  if (normalized.includes("permanent") || normalized.includes("direct_delete") || normalized.includes("delete_permanent")) {
    return "permanent_delete";
  }
  if (normalized.includes("info_only") || normalized.includes("suggestion") || normalized.includes("review")) {
    return "suggestion_only";
  }
  if (normalized.includes("trash") || normalized.includes("recycle") || normalized.includes("cache") || normalized.includes("temp")) {
    return "trash";
  }
  return "suggestion_only";
}

function mapCleanupExecutionResult(result: BackendCleanupExecutionResult): CleanupExecutionResult {
  return {
    ok: result.ok !== false,
    planId: optionalString(result.plan_id ?? result.planId),
    executionId: optionalString(result.execution_id ?? result.executionId),
    freedBytes: numberOrUndefined(result.freed_bytes ?? result.freedBytes),
    executed: arrayOfObjects(result.executed).map((item) => mapCleanupItem(item, "recycle_bin")),
    rolledBack: arrayOfObjects(result.rolled_back ?? result.rolledBack).map((item) => mapCleanupItem(item, "recycle_bin")),
    errors: stringArray(result.errors)
  };
}

function mapTaskEvent(task: BackendTask): TaskEvent {
  const cleanupPlan = cleanupPlanFromApprovalPayload(task.cleanup_plan ?? task.cleanupPlan ?? task.diff_preview);
  return {
    id: task.id,
    title: task.user_goal,
    description: zhBackendText(task.final_summary) || `当前后端状态：${zhBackendTaskStatus(task.status)}`,
    state: mapTaskState(task.status),
    agent: "调度 Agent",
    createdAt: task.created_at,
    updatedAt: task.updated_at,
    recordings: [],
    cleanupPlan,
    boundaryEvents: mapBoundaryEvents(task.boundary_events)
  };
}

function mapTaskRecordings(timeline: BackendTimeline): NonNullable<TaskEvent["recordings"]> {
  const byStep = new Map<string, NonNullable<TaskEvent["recordings"]>[number]>();
  const direct = Array.isArray(timeline.recordings) ? timeline.recordings : [];
  const fromMessages = timeline.messages
    .map((message) => metadataPayloadFor<BackendStepRecordingPayload>(message))
    .filter((payload): payload is BackendStepRecordingPayload => payload?.kind === "step_screenshot");

  for (const item of direct) {
    mergeRecording(
      byStep,
      String(item.step_id ?? ""),
      String(item.tool_name ?? ""),
      String(item.agent ?? ""),
      Array.isArray(item.frames) ? item.frames : []
    );
  }
  for (const payload of fromMessages) {
    mergeRecording(
      byStep,
      String(payload.step_id ?? ""),
      String(payload.tool_name ?? ""),
      String(payload.agent ?? ""),
      Array.isArray(payload.frames) ? payload.frames : []
    );
  }

  return Array.from(byStep.values()).map((recording) => ({
    ...recording,
    frames: dedupeFrames(recording.frames).sort((a, b) => Date.parse(a.capturedAt) - Date.parse(b.capturedAt))
  }));
}

function cleanupPlanFromTimeline(timeline: BackendTimeline): CleanupPlan | undefined {
  const direct = cleanupPlanFromApprovalPayload(timeline.cleanup_plan ?? timeline.cleanupPlan);
  if (direct) return direct;

  for (const message of timeline.messages) {
    const payload = metadataPayloadFor<unknown>(message);
    const plan = cleanupPlanFromApprovalPayload(payload);
    if (plan) return plan;
  }
  return undefined;
}

function mapTaskExplain(data: BackendTaskExplain): TaskExplain {
  return {
    taskId: String(data.task_id ?? ""),
    userGoal: zhBackendText(String(data.user_goal ?? "")),
    status: String(data.status ?? ""),
    mode: String(data.mode ?? ""),
    generatedAt: String(data.generated_at ?? ""),
    complete: Boolean(data.complete),
    missingSections: (data.missing_sections ?? []).map(String),
    dataSources: Object.fromEntries(Object.entries(data.data_sources ?? {}).map(([key, value]) => [key, Number(value ?? 0)])),
    userGoalRecord: {
      text: zhBackendText(String(data.user_goal_record?.text ?? "")),
      evidence: (data.user_goal_record?.evidence ?? []).map(mapExplainEvidence)
    },
    supervisorJudgment: {
      summary: zhBackendText(String(data.supervisor_judgment?.summary ?? "")),
      delegate: Boolean(data.supervisor_judgment?.delegate),
      agentHint: String(data.supervisor_judgment?.agent_hint ?? ""),
      inferred: Boolean(data.supervisor_judgment?.inferred),
      evidence: (data.supervisor_judgment?.evidence ?? []).map(mapExplainEvidence)
    },
    plannerReasoning: {
      summary: zhBackendText(String(data.planner_reasoning?.summary ?? "")),
      planId: String(data.planner_reasoning?.plan_id ?? ""),
      goal: zhBackendText(String(data.planner_reasoning?.goal ?? "")),
      assumptions: (data.planner_reasoning?.assumptions ?? []).map((item) => zhBackendText(String(item))),
      stepCount: Number(data.planner_reasoning?.step_count ?? 0),
      globalRiskLevel: String(data.planner_reasoning?.global_risk_level ?? ""),
      requiresUserApproval: Boolean(data.planner_reasoning?.requires_user_approval),
      evidence: (data.planner_reasoning?.evidence ?? []).map(mapExplainEvidence)
    },
    globalSafetyReviews: (data.global_safety_reviews ?? []).map(mapExplainReview),
    steps: (data.steps ?? []).map(mapExplainStep),
    subagentSuggestions: (data.subagent_suggestions ?? []).map(mapExplainMessage),
    finalResult: {
      status: String(data.final_result?.status ?? ""),
      summary: zhBackendText(String(data.final_result?.summary ?? "")),
      safetyReviews: (data.final_result?.safety_reviews ?? []).map(mapExplainReview),
      evidence: (data.final_result?.evidence ?? []).map(mapExplainEvidence)
    },
    chain: (data.chain ?? []).map(mapExplainChainItem)
  };
}

function mapExplainStep(step: BackendTaskExplainStep): TaskExplainStep {
  return {
    id: String(step.id ?? step.step_id ?? ""),
    stepId: String(step.step_id ?? step.id ?? ""),
    order: Number(step.order ?? 0),
    agentName: String(step.agent_name ?? ""),
    toolName: String(step.tool_name ?? ""),
    description: zhBackendText(String(step.description ?? "")),
    status: String(step.status ?? ""),
    riskLevel: String(step.risk_level ?? ""),
    requiresApproval: Boolean(step.requires_approval),
    expectedObservation: zhBackendText(String(step.expected_observation ?? "")),
    rollbackStrategy: zhBackendText(String(step.rollback_strategy ?? "")),
    plannerReason: zhBackendText(String(step.planner_reason ?? "")),
    safetyReviews: (step.safety_reviews ?? []).map(mapExplainReview),
    subagentSuggestions: (step.subagent_suggestions ?? []).map(mapExplainMessage),
    observations: (step.observations ?? []).map(mapExplainMessage)
  };
}

function mapExplainReview(review: BackendTaskExplainReview): TaskExplainReview {
  return {
    id: String(review.id ?? ""),
    stepId: review.step_id === undefined ? undefined : review.step_id,
    targetType: String(review.target_type ?? ""),
    verdict: String(review.verdict ?? ""),
    riskLevel: String(review.risk_level ?? ""),
    reasons: (review.reasons ?? []).map((item) => zhBackendText(String(item))),
    requiredChanges: (review.required_changes ?? []).map((item) => zhBackendText(String(item))),
    userConfirmationMessage: zhBackendText(String(review.user_confirmation_message ?? "")),
    safeAlternative: zhBackendText(String(review.safe_alternative ?? "")),
    createdAt: String(review.created_at ?? ""),
    evidence: (review.evidence ?? []).map(mapExplainEvidence)
  };
}

function mapExplainMessage(message: BackendTaskExplainMessage): TaskExplainMessage {
  return {
    id: String(message.id ?? ""),
    stepId: message.step_id === undefined ? undefined : message.step_id,
    fromAgent: String(message.from_agent ?? ""),
    toAgent: message.to_agent === undefined ? undefined : message.to_agent,
    messageType: String(message.message_type ?? ""),
    content: zhBackendText(String(message.content ?? "")),
    createdAt: String(message.created_at ?? ""),
    evidence: (message.evidence ?? []).map(mapExplainEvidence),
    action: message.action
      ? {
          kind: String(message.action.kind ?? ""),
          toolName: String(message.action.tool_name ?? ""),
          rationale: zhBackendText(String(message.action.rationale ?? "")),
          followUpQuestion: zhBackendText(String(message.action.follow_up_question ?? ""))
        }
      : undefined
  };
}

function mapExplainChainItem(item: BackendTaskExplainChainItem): TaskExplainChainItem {
  return {
    stage: String(item.stage ?? ""),
    title: String(item.title ?? ""),
    summary: zhBackendText(String(item.summary ?? "")),
    evidence: (item.evidence ?? []).map(mapExplainEvidence)
  };
}

function mapExplainEvidence(item: BackendTaskExplainEvidence): TaskExplainEvidence {
  return {
    source: String(item.source ?? ""),
    id: String(item.id ?? ""),
    createdAt: item.created_at ? String(item.created_at) : undefined,
    actor: item.actor ? String(item.actor) : undefined,
    eventType: item.event_type ? String(item.event_type) : undefined,
    stepId: item.step_id ? String(item.step_id) : undefined,
    summary: zhBackendText(String(item.summary ?? ""))
  };
}

function mergeRecording(
  target: Map<string, NonNullable<TaskEvent["recordings"]>[number]>,
  stepId: string,
  toolName: string,
  agent: string,
  frames: BackendStepRecordingFrame[]
) {
  if (!stepId || !frames.length) return;
  const current = target.get(stepId) ?? { stepId, toolName, agent, frames: [] };
  current.toolName = current.toolName || toolName;
  current.agent = current.agent || agent;
  current.frames.push(...frames.map(mapRecordingFrame));
  target.set(stepId, current);
}

function mapRecordingFrame(frame: BackendStepRecordingFrame): NonNullable<TaskEvent["recordings"]>[number]["frames"][number] {
  const url = typeof frame.url === "string" && frame.url ? absoluteBackendUrl(frame.url) : undefined;
  return {
    phase: String(frame.phase ?? ""),
    ok: frame.ok !== false,
    capturedAt: String(frame.captured_at ?? ""),
    url,
    width: Number(frame.width ?? 0) || undefined,
    height: Number(frame.height ?? 0) || undefined,
    error: typeof frame.error === "string" ? frame.error : undefined
  };
}

function dedupeFrames<TFrame extends { phase: string; capturedAt: string; url?: string }>(frames: TFrame[]): TFrame[] {
  const seen = new Set<string>();
  const result: TFrame[] = [];
  for (const frame of frames) {
    const key = `${frame.phase}|${frame.capturedAt}|${frame.url ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(frame);
  }
  return result;
}

function absoluteBackendUrl(path: string): string | undefined {
  return absoluteRendererLoopbackBackendUrl(path, getBackendBaseUrl()) || undefined;
}

function mapAgentKind(kind?: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (kind === "observation") return "observation";
  if (kind === "review" || kind === "critique") return "handoff";
  if (kind === "final") return "result";
  return "action";
}

function agentNameFor(message?: BackendAgentMessage): string {
  return message?.name ?? message?.metadata?.from_agent ?? message?.from_agent ?? "assistant";
}

function metadataPayloadFor<TPayload>(message?: BackendAgentMessage): TPayload | undefined {
  const payload = message?.metadata?.structured_payload ?? message?.structured_payload;
  return payload as TPayload | undefined;
}

function mapRiskSeverity(risk: string): SafetyReview["findings"][number]["severity"] {
  if (risk.startsWith("R4")) return "critical";
  if (risk.startsWith("R3")) return "high";
  if (risk.startsWith("R2")) return "medium";
  return "low";
}

function mapApproval(approval: BackendApproval): ApprovalRequest {
  const cleanupPlan = cleanupPlanFromApprovalPayload(approval.diff_preview);
  return {
    id: approval.id,
    taskId: approval.task_id ? String(approval.task_id) : undefined,
    stepId: approval.step_id === undefined ? undefined : approval.step_id,
    approvalType: approval.approval_type,
    title: cleanupPlan ? "清理计划审批" : zhApprovalType(approval.approval_type),
    reason: zhBackendText(approval.message),
    requester: "HumanGateAgent",
    riskLevel: cleanupPlan?.items.some((item) => item.disposition === "permanent_delete")
      ? "high"
      : mapRiskSeverity(approval.risk_level ?? ""),
    createdAt: approval.created_at,
    proposedAction: formatDiffPreview(approval.diff_preview),
    status: approval.status === "rejected" ? "denied" : approval.status === "approved" ? "approved" : "pending",
    rawPayload: approval.diff_preview,
    cleanupPlan,
    toolName: approval.tool_name,
    toolTrustTier: approval.tool_trust_tier,
    toolEffects: approval.tool_effects ?? [],
    resourceKinds: approval.resource_kinds ?? [],
    policyMode: approval.policy_mode ?? approval.permission_mode,
    dryRunSummary: approval.dry_run_summary,
    modelAction: optionalObjectRecord(approval.model_action),
    runtimeControlFields: optionalObjectRecord(approval.runtime_control_fields ?? approval.runtime_fields),
    engineeringBoundary: optionalObjectRecord(approval.engineering_boundary)
  };
}

function optionalObjectRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function cleanupPlanFromApprovalPayload(payload: unknown): CleanupPlan | undefined {
  const candidate = findCleanupPayload(payload);
  if (!candidate) return undefined;
  const plan = mapCleanupPlan(candidate);
  return plan.items.length ? plan : undefined;
}

function findCleanupPayload(value: unknown): BackendCleanupPlan | undefined {
  if (!value || typeof value !== "object") return undefined;
  if (Array.isArray(value)) {
    const items = value.filter((item): item is BackendCleanupItem => Boolean(item && typeof item === "object"));
    return items.some(looksLikeCleanupItem) ? { items } : undefined;
  }

  const record = value as Record<string, unknown>;
  for (const key of ["cleanup_plan", "cleanupPlan", "plan", "diff_preview", "payload"]) {
    const nested = findCleanupPayload(record[key]);
    if (nested) return nested;
  }

  if (looksLikeCleanupPlan(record)) return record as BackendCleanupPlan;
  return undefined;
}

function looksLikeCleanupPlan(record: Record<string, unknown>): boolean {
  if (Array.isArray(record.items) && record.items.some(looksLikeCleanupItem)) return true;
  const buckets = record.buckets;
  return Boolean(
    buckets &&
      typeof buckets === "object" &&
      ["direct_delete", "permanent_delete", "recycle_bin", "trash", "suggestion_only", "info_only", "immediate", "approval"]
        .some((key) => Array.isArray((buckets as Record<string, unknown>)[key]))
  );
}

function looksLikeCleanupItem(value: unknown): value is BackendCleanupItem {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  const action = String(record.action ?? record.disposition ?? record.bucket ?? "").toLowerCase();
  return Boolean(record.path && (
    action.includes("clean") ||
    action.includes("delete") ||
    action.includes("trash") ||
    action.includes("cache") ||
    action.includes("review") ||
    action.includes("recycle")
  ));
}

function settingsPatchFor(settings: AppSettings, baseline: AppSettings | null): Partial<BackendSettings> {
  const body: Partial<BackendSettings> = {};
  const previous = baseline ?? settings;
  const add = <K extends keyof BackendSettings>(key: K, value: BackendSettings[K], changed: boolean) => {
    if (baseline === null || changed) {
      body[key] = value;
    }
  };

  add("provider_name", settings.providerName, settings.providerName !== previous.providerName);
  add("base_url", settings.apiBaseUrl, settings.apiBaseUrl !== previous.apiBaseUrl);
  add("model", settings.model, settings.model !== previous.model);
  add("review_model", settings.reviewModel, settings.reviewModel !== previous.reviewModel);
  add("wire_api", settings.wireApi, settings.wireApi !== previous.wireApi);
  add("requires_openai_auth", settings.requiresOpenAiAuth, settings.requiresOpenAiAuth !== previous.requiresOpenAiAuth);
  add("model_reasoning_effort", settings.modelReasoningEffort, settings.modelReasoningEffort !== previous.modelReasoningEffort);
  add("disable_response_storage", settings.disableResponseStorage, settings.disableResponseStorage !== previous.disableResponseStorage);
  add("temperature", settings.temperature, settings.temperature !== previous.temperature);
  add("max_tokens", settings.maxTokens, settings.maxTokens !== previous.maxTokens);
  add("timeout", settings.timeout, settings.timeout !== previous.timeout);
  add("llm_api_max_retries", settings.llmApiMaxRetries, settings.llmApiMaxRetries !== previous.llmApiMaxRetries);
  add(
    "llm_api_retry_backoff_seconds",
    settings.llmApiRetryBackoffSeconds,
    settings.llmApiRetryBackoffSeconds !== previous.llmApiRetryBackoffSeconds
  );
  add(
    "llm_api_circuit_failure_threshold",
    settings.llmApiCircuitFailureThreshold,
    settings.llmApiCircuitFailureThreshold !== previous.llmApiCircuitFailureThreshold
  );
  add(
    "llm_api_circuit_cooldown_seconds",
    settings.llmApiCircuitCooldownSeconds,
    settings.llmApiCircuitCooldownSeconds !== previous.llmApiCircuitCooldownSeconds
  );
  add("model_context_window", settings.modelContextWindow, settings.modelContextWindow !== previous.modelContextWindow);
  add(
    "model_auto_compact_token_limit",
    settings.modelAutoCompactTokenLimit,
    settings.modelAutoCompactTokenLimit !== previous.modelAutoCompactTokenLimit
  );
  const allowedDirectories = allowedDirectoriesForSettings(settings, previous);
  const previousAllowedDirectories = allowedDirectoriesForSettings(previous);
  add("allowed_directories", allowedDirectories, !sameStringArray(allowedDirectories, previousAllowedDirectories));
  add("allow_browser_network", settings.allowBrowserNetwork, settings.allowBrowserNetwork !== previous.allowBrowserNetwork);
  add("remote_desktop_enabled", settings.remoteDesktopEnabled, settings.remoteDesktopEnabled !== previous.remoteDesktopEnabled);
  add("app_allowlist", settings.appAllowlist, !sameStringArray(settings.appAllowlist, previous.appAllowlist));
  add("browser_max_page_bytes", settings.browserMaxPageBytes, settings.browserMaxPageBytes !== previous.browserMaxPageBytes);
  add("browser_screenshot_dir", settings.browserScreenshotDir, settings.browserScreenshotDir !== previous.browserScreenshotDir);
  add("onnx_model_path", settings.onnxModelPath, settings.onnxModelPath !== previous.onnxModelPath);
  add("onnx_execution_provider", settings.onnxExecutionProvider, settings.onnxExecutionProvider !== previous.onnxExecutionProvider);
  add("onnx_provider_preference", settings.onnxProviderPreference, settings.onnxProviderPreference !== previous.onnxProviderPreference);
  add("onnx_directml_device_id", settings.onnxDirectmlDeviceId, settings.onnxDirectmlDeviceId !== previous.onnxDirectmlDeviceId);
  add("onnx_openvino_device", settings.onnxOpenvinoDevice, settings.onnxOpenvinoDevice !== previous.onnxOpenvinoDevice);
  add("onnx_openvino_cache_dir", settings.onnxOpenvinoCacheDir, settings.onnxOpenvinoCacheDir !== previous.onnxOpenvinoCacheDir);
  add("onnx_warm_on_startup", settings.onnxWarmOnStartup, settings.onnxWarmOnStartup !== previous.onnxWarmOnStartup);
  add("onnx_model_family", settings.onnxModelFamily, settings.onnxModelFamily !== previous.onnxModelFamily);
  add("embedding_backend", settings.onnxEmbeddingBackend, settings.onnxEmbeddingBackend !== previous.onnxEmbeddingBackend);
  add("onnx_embedding_model_path", settings.onnxEmbeddingModelPath, settings.onnxEmbeddingModelPath !== previous.onnxEmbeddingModelPath);
  add(
    "onnx_embedding_execution_provider",
    settings.onnxEmbeddingExecutionProvider,
    settings.onnxEmbeddingExecutionProvider !== previous.onnxEmbeddingExecutionProvider
  );
  add("onnx_embedding_model_id", settings.onnxEmbeddingModelId, settings.onnxEmbeddingModelId !== previous.onnxEmbeddingModelId);
  add(
    "onnx_embedding_max_batch_size",
    settings.onnxEmbeddingMaxBatchSize,
    settings.onnxEmbeddingMaxBatchSize !== previous.onnxEmbeddingMaxBatchSize
  );
  add("image_embedding_backend", settings.imageEmbeddingBackend, settings.imageEmbeddingBackend !== previous.imageEmbeddingBackend);
  add(
    "onnx_image_embedding_model_path",
    settings.onnxImageEmbeddingModelPath,
    settings.onnxImageEmbeddingModelPath !== previous.onnxImageEmbeddingModelPath
  );
  add(
    "onnx_image_embedding_execution_provider",
    settings.onnxImageEmbeddingExecutionProvider,
    settings.onnxImageEmbeddingExecutionProvider !== previous.onnxImageEmbeddingExecutionProvider
  );
  add(
    "onnx_image_embedding_model_id",
    settings.onnxImageEmbeddingModelId,
    settings.onnxImageEmbeddingModelId !== previous.onnxImageEmbeddingModelId
  );
  add(
    "onnx_image_embedding_max_batch_size",
    settings.onnxImageEmbeddingMaxBatchSize,
    settings.onnxImageEmbeddingMaxBatchSize !== previous.onnxImageEmbeddingMaxBatchSize
  );
  add("ocr_backend", settings.ocrBackend, settings.ocrBackend !== previous.ocrBackend);
  add("ocr_execution_provider", settings.ocrExecutionProvider, settings.ocrExecutionProvider !== previous.ocrExecutionProvider);
  add("ocr_openvino_model_dir", settings.ocrOpenvinoModelDir, settings.ocrOpenvinoModelDir !== previous.ocrOpenvinoModelDir);
  add("ocr_openvino_device", settings.ocrOpenvinoDevice, settings.ocrOpenvinoDevice !== previous.ocrOpenvinoDevice);
  add("ocr_lang", settings.ocrLang, settings.ocrLang !== previous.ocrLang);
  add("ocr_min_confidence", settings.ocrMinConfidence, settings.ocrMinConfidence !== previous.ocrMinConfidence);
  add("ocr_batch_size", settings.ocrBatchSize, settings.ocrBatchSize !== previous.ocrBatchSize);
  add("mode", settings.mode, settings.mode !== previous.mode);
  add("permission_mode", settings.permissionMode, settings.permissionMode !== previous.permissionMode);
  add("allow_cloud_context", settings.allowCloudContext, settings.allowCloudContext !== previous.allowCloudContext);
  add(
    "allow_file_content_upload",
    settings.allowFileContentUpload,
    settings.allowFileContentUpload !== previous.allowFileContentUpload
  );
  const mcpServers = settings.mcpServers.map(mapMcpServerForBackend).filter(hasPersistableMcpServerTarget);
  const previousMcpServers = previous.mcpServers.map(mapMcpServerForBackend).filter(hasPersistableMcpServerTarget);
  add("mcp_servers", mcpServers, JSON.stringify(mcpServers) !== JSON.stringify(previousMcpServers));

  return body;
}

function mergeDesktopOnlySettings(settings: AppSettings, source: AppSettings | null): AppSettings {
  if (!source) return settings;
  return {
    ...settings,
    autoStartBackend: source.autoStartBackend,
    telemetryEnabled: source.telemetryEnabled,
    compactMode: source.compactMode,
    theme: source.theme
  };
}

function allowedDirectoriesForSettings(settings: AppSettings, baseline?: AppSettings | null): string[] {
  const directories = settings.allowedDirectories?.length
    ? settings.allowedDirectories.filter(Boolean)
    : settings.workspaceRoot
      ? [settings.workspaceRoot]
      : [];
  if (!settings.workspaceRoot) return directories;
  if (!directories.length) return [settings.workspaceRoot];
  if (directories[0] === settings.workspaceRoot) return directories;

  const baselinePrimary = baseline?.workspaceRoot || baseline?.allowedDirectories?.[0] || directories[0];
  if (settings.workspaceRoot !== baselinePrimary) {
    return [settings.workspaceRoot, ...directories.slice(1).filter((directory) => directory !== settings.workspaceRoot)];
  }

  return [settings.workspaceRoot, ...directories.filter((directory) => directory !== settings.workspaceRoot)];
}

function sameStringArray(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function mapMcpServerForBackend(server: AppSettings["mcpServers"][number]): NonNullable<BackendSettings["mcp_servers"]>[number] {
  const result: NonNullable<BackendSettings["mcp_servers"]>[number] = {
    ...server,
    name: String(server.name ?? "").trim(),
    url: String(server.url ?? "").trim(),
    enabled: server.enabled !== false
  };
  if (server.command !== undefined) result.command = String(server.command);
  if (Array.isArray(server.args)) result.args = server.args.map(String);
  if (server.transport !== undefined) result.transport = String(server.transport);
  if (server.auth && typeof server.auth === "object") result.auth = server.auth;
  return result;
}

function hasPersistableMcpServerTarget(server: NonNullable<BackendSettings["mcp_servers"]>[number]): boolean {
  return Boolean(String(server.url ?? "").trim() || String(server.command ?? "").trim());
}

function mapSettings(settings: BackendSettings): AppSettings {
  const rawMode = (settings.mode ?? "efficiency").toLowerCase();
  const mode: AppSettings["mode"] = rawMode === "efficiency" || rawMode === "hybrid" ? rawMode : "privacy";
  const mcpServers = (settings.mcp_servers ?? [])
    .map((server) => ({
      ...server,
      id: typeof server?.id === "string" ? server.id : undefined,
      name: String(server?.name ?? "").trim(),
      url: String(server?.url ?? "").trim(),
      command: typeof server?.command === "string" ? server.command : undefined,
      args: Array.isArray(server?.args) ? server.args.map(String) : undefined,
      transport: typeof server?.transport === "string" ? server.transport : undefined,
      auth: server?.auth && typeof server.auth === "object" ? server.auth : undefined,
      enabled: server?.enabled !== false
    }))
    .filter((server) => server.url.length > 0 || Boolean(server.command));
  const allowedDirectories = settings.allowed_directories ?? [];
  return {
    apiBaseUrl: settings.base_url ?? "http://127.0.0.1:8000",
    autoStartBackend: false,
    telemetryEnabled: false,
    compactMode: false,
    theme: "system",
    providerName: settings.provider_name ?? "openai_compatible",
    model: settings.model ?? "gpt-4o-mini",
    reviewModel: settings.review_model ?? "",
    wireApi: settings.wire_api === "responses" ? "responses" : "chat_completions",
    requiresOpenAiAuth: settings.requires_openai_auth !== false,
    modelReasoningEffort: settings.model_reasoning_effort ?? "medium",
    disableResponseStorage: Boolean(settings.disable_response_storage),
    temperature: Number(settings.temperature ?? 0.2),
    maxTokens: Number(settings.max_tokens ?? 1600),
    timeout: Number(settings.timeout ?? 30),
    llmApiMaxRetries: Number(settings.llm_api_max_retries ?? 2),
    llmApiRetryBackoffSeconds: Number(settings.llm_api_retry_backoff_seconds ?? 0.25),
    llmApiCircuitFailureThreshold: Number(settings.llm_api_circuit_failure_threshold ?? 5),
    llmApiCircuitCooldownSeconds: Number(settings.llm_api_circuit_cooldown_seconds ?? 30),
    modelContextWindow: Number(settings.model_context_window ?? 128000),
    modelAutoCompactTokenLimit: Number(settings.model_auto_compact_token_limit ?? 96000),
    workspaceRoot: allowedDirectories[0] ?? "",
    allowedDirectories,
    allowBrowserNetwork: Boolean(settings.allow_browser_network),
    remoteDesktopEnabled: Boolean(settings.remote_desktop_enabled),
    appAllowlist: settings.app_allowlist ?? [],
    browserMaxPageBytes: settings.browser_max_page_bytes ?? 250000,
    browserScreenshotDir: settings.browser_screenshot_dir ?? "",
    onnxModelPath: settings.onnx_model_path ?? "",
    onnxExecutionProvider: normalizeExecutionProvider(settings.onnx_execution_provider ?? ""),
    onnxProviderPreference: settings.onnx_provider_preference ?? "winml,directml,openvino,cpu",
    onnxDirectmlDeviceId: settings.onnx_directml_device_id ?? "",
    onnxOpenvinoDevice: settings.onnx_openvino_device ?? "AUTO",
    onnxOpenvinoCacheDir: settings.onnx_openvino_cache_dir ?? "",
    onnxWarmOnStartup: Boolean(settings.onnx_warm_on_startup),
    onnxModelFamily: settings.onnx_model_family ?? "",
    onnxEmbeddingBackend: settings.embedding_backend ?? "auto",
    onnxEmbeddingModelPath: settings.onnx_embedding_model_path ?? "",
    onnxEmbeddingExecutionProvider: settings.onnx_embedding_execution_provider ?? "",
    onnxEmbeddingModelId: settings.onnx_embedding_model_id ?? "intfloat/multilingual-e5-small",
    onnxEmbeddingMaxBatchSize: Number(settings.onnx_embedding_max_batch_size ?? 32),
    imageEmbeddingBackend: settings.image_embedding_backend ?? "auto",
    onnxImageEmbeddingModelPath: settings.onnx_image_embedding_model_path ?? "",
    onnxImageEmbeddingExecutionProvider: settings.onnx_image_embedding_execution_provider ?? "",
    onnxImageEmbeddingModelId: settings.onnx_image_embedding_model_id ?? "openai/clip-vit-base-patch32",
    onnxImageEmbeddingMaxBatchSize: Number(settings.onnx_image_embedding_max_batch_size ?? 8),
    ocrBackend: settings.ocr_backend ?? "auto",
    ocrExecutionProvider: settings.ocr_execution_provider ?? "",
    ocrOpenvinoModelDir: settings.ocr_openvino_model_dir ?? "",
    ocrOpenvinoDevice: settings.ocr_openvino_device ?? "AUTO",
    ocrLang: settings.ocr_lang ?? "multi",
    ocrMinConfidence: Number(settings.ocr_min_confidence ?? 0),
    ocrBatchSize: Number(settings.ocr_batch_size ?? 1),
    mode,
    permissionMode: normalizePermissionMode(settings.permission_mode),
    allowCloudContext: Boolean(settings.allow_cloud_context),
    allowFileContentUpload: Boolean(settings.allow_file_content_upload),
    mcpServers
  };
}

function normalizePermissionMode(value?: string): AppSettings["permissionMode"] {
  const normalized = String(value ?? "default").toLowerCase();
  if (normalized === "plan" || normalized === "trusted_edits" || normalized === "auto_review" || normalized === "dont_ask") {
    return normalized;
  }
  return "default";
}

function mapLlmHealth(health: BackendLlmHealth): LLMHealthStatus {
  return {
    active: {
      available: Boolean(health.active?.available),
      degraded: Boolean(health.active?.degraded),
      provider: String(health.active?.provider ?? ""),
      model: String(health.active?.model ?? ""),
      profile: mapLlmProfile(health.active?.profile),
      error: String(health.active?.error ?? "")
    },
    retry: {
      maxRetries: Number(health.retry?.max_retries ?? 0),
      backoffSeconds: Number(health.retry?.backoff_seconds ?? 0),
      circuitFailureThreshold: Number(health.retry?.circuit_failure_threshold ?? 0),
      circuitCooldownSeconds: Number(health.retry?.circuit_cooldown_seconds ?? 0),
      circuit: {
        state: String(health.retry?.circuit?.state ?? "closed"),
        failures: Number(health.retry?.circuit?.failures ?? 0),
        retryAfterSeconds: Number(health.retry?.circuit?.retry_after_seconds ?? 0)
      }
    }
  };
}

function mapLlmProfile(profile?: BackendLlmProfile): LLMProfile {
  const caps = profile?.capabilities ?? {};
  const modelProfile = profile?.model_profile ?? {};
  return {
    providerName: String(profile?.provider_name ?? ""),
    model: String(profile?.model ?? modelProfile.model ?? ""),
    baseUrl: String(profile?.base_url ?? ""),
    wireApi: String(profile?.wire_api ?? "chat_completions"),
    location: String(profile?.location ?? ""),
    activeBackend: String(profile?.active_backend ?? profile?.provider_name ?? ""),
    capabilities: {
      tools: Boolean(caps.tools),
      structuredJson: caps.structured_json !== false,
      vision: Boolean(caps.vision),
      embeddings: Boolean(caps.embeddings),
      promptCache: Boolean(caps.prompt_cache),
      responsesApi: Boolean(caps.responses_api),
      reasoningEffort: Boolean(caps.reasoning_effort),
      usageBreakdown: Boolean(caps.usage_breakdown),
      local: Boolean(caps.local),
      cloud: Boolean(caps.cloud)
    },
    modelProfile: {
      model: String(modelProfile.model ?? profile?.model ?? ""),
      contextWindow: Number(modelProfile.context_window ?? 0),
      maxOutputTokens: Number(modelProfile.max_output_tokens ?? 0),
      known: Boolean(modelProfile.known),
      family: String(modelProfile.family ?? "")
    }
  };
}

function mapLlmCostSummary(summary: BackendLlmCostSummary): LLMCostSummary {
  return {
    windowHours: Number(summary.window_hours ?? 24),
    calls: Number(summary.calls ?? 0),
    promptTokens: Number(summary.prompt_tokens ?? 0),
    completionTokens: Number(summary.completion_tokens ?? 0),
    totalTokens: Number(summary.total_tokens ?? 0),
    totalCostUsd: typeof summary.total_cost_usd === "number" ? summary.total_cost_usd : null,
    estimated: Boolean(summary.estimated),
    lastEventAt: String(summary.last_event_at ?? ""),
    byModel: (summary.by_model ?? []).map((item) => ({
      provider: String(item.provider ?? ""),
      model: String(item.model ?? ""),
      calls: Number(item.calls ?? 0),
      promptTokens: Number(item.prompt_tokens ?? 0),
      completionTokens: Number(item.completion_tokens ?? 0),
      totalTokens: Number(item.total_tokens ?? 0),
      totalCostUsd: Number(item.total_cost_usd ?? 0),
      estimated: Boolean(item.estimated)
    }))
  };
}

function mapContextUsage(usage: BackendContextUsage): ContextUsage {
  const warning = usage.warning ?? {};
  const projection = usage.projection ?? {};
  const projectionSummary = projection.summary ?? {};
  const effectiveContextWindow = Number(usage.effective_context_window ?? usage.model_context_window ?? 0);
  const usedTokens = Number(usage.used_tokens ?? warning.token_count ?? 0);
  const projectedTokens = Number(projectionSummary.projected_tokens ?? projection.projected_tokens ?? usedTokens);
  const freeTokens = Number(usage.free_tokens ?? Math.max(0, effectiveContextWindow - usedTokens));
  const usedPercent = effectiveContextWindow > 0 ? Math.round((usedTokens / effectiveContextWindow) * 10000) / 100 : 0;
  const projectedPercent =
    effectiveContextWindow > 0 ? Math.round((projectedTokens / effectiveContextWindow) * 10000) / 100 : usedPercent;
  const fallbackSeverity = warning.is_at_blocking_limit || warning.is_above_error_threshold
    ? "error"
    : warning.is_above_warning_threshold
      ? "warning"
      : "ok";
  const fallbackStatus = fallbackSeverity === "error" ? "critical" : fallbackSeverity === "warning" ? "watch" : "healthy";
  const health = usage.health ?? {};
  const lineage = usage.lineage ?? {};
  const lineageProjection = lineage.projection ?? {};

  return {
    totalTokens: Number(usage.total_tokens ?? usedTokens + freeTokens),
    usedTokens,
    freeTokens,
    effectiveContextWindow,
    modelContextWindow: Number(usage.model_context_window ?? effectiveContextWindow),
    autoCompactThreshold: Number(usage.auto_compact_threshold ?? warning.threshold ?? 0),
    manualCompactLimit: Number(usage.manual_compact_limit ?? 0),
    reservedOutputTokens: Number(usage.reserved_output_tokens ?? 0),
    warning: {
      tokenCount: Number(warning.token_count ?? usedTokens),
      threshold: Number(warning.threshold ?? 0),
      percentLeft: Number(warning.percent_left ?? Math.max(0, 100 - usedPercent)),
      isAboveWarningThreshold: Boolean(warning.is_above_warning_threshold),
      isAboveErrorThreshold: Boolean(warning.is_above_error_threshold),
      isAboveAutoCompactThreshold: Boolean(warning.is_above_auto_compact_threshold),
      isAtBlockingLimit: Boolean(warning.is_at_blocking_limit)
    },
    health: {
      status: contextHealthStatus(health.status, fallbackStatus),
      severity: contextHealthSeverity(health.severity, fallbackSeverity),
      reason: String(health.reason ?? contextHealthFallbackReason(fallbackSeverity)),
      usedPercent: Number(health.used_percent ?? usedPercent),
      freePercent: Number(health.free_percent ?? Math.max(0, 100 - usedPercent)),
      freeTokens: Number(health.free_tokens ?? freeTokens),
      projectedTokens: Number(health.projected_tokens ?? projectedTokens),
      projectedPercent: Number(health.projected_percent ?? projectedPercent),
      projectedFreeTokens: Number(health.projected_free_tokens ?? Math.max(0, effectiveContextWindow - projectedTokens)),
      isHealthy: health.is_healthy === undefined ? fallbackSeverity === "ok" : Boolean(health.is_healthy)
    },
    projection: {
      enabled: Boolean(projectionSummary.enabled ?? projection.enabled),
      strategy: String(projectionSummary.strategy ?? projection.strategy ?? "none"),
      compacted: Boolean(projectionSummary.compacted ?? projection.compacted),
      originalTokens: Number(projectionSummary.original_tokens ?? projection.original_tokens ?? usedTokens),
      projectedTokens,
      tokensSaved: Number(
        projectionSummary.tokens_saved ??
          Math.max(0, Number(projection.original_tokens ?? usedTokens) - Number(projection.projected_tokens ?? usedTokens))
      ),
      messagesRemoved: Number(
        projectionSummary.messages_removed ??
          Math.max(0, Number(projection.original_count ?? 0) - Number(projection.projected_count ?? 0))
      ),
      adjustments: Array.isArray(projectionSummary.adjustments)
        ? projectionSummary.adjustments.map((item) => String(item))
        : [],
      description: String(projectionSummary.description ?? "Projection summary is unavailable.")
    },
    lineage: {
      taskId: String(lineage.task_id ?? ""),
      historySource: String(lineage.history_source ?? "unknown"),
      messageCount: Number(lineage.message_count ?? 0),
      systemMessageCount: Number(lineage.system_message_count ?? 0),
      agentMessageCount: Number(lineage.agent_message_count ?? 0),
      messageRoles: objectRecord(lineage.message_roles),
      localToolCount: Number(lineage.local_tool_count ?? 0),
      mcpToolCount: Number(lineage.mcp_tool_count ?? 0),
      sessionMemoryItemCount: Number(lineage.session_memory_item_count ?? 0),
      includeRegisteredTools: lineage.include_registered_tools !== false,
      includeSessionMemory: lineage.include_session_memory !== false,
      includeProjection: lineage.include_projection !== false,
      projection: {
        source: String(lineageProjection.source ?? "context_usage"),
        strategy: String(lineageProjection.strategy ?? projection.strategy ?? "none"),
        boundaryId: String(lineageProjection.boundary_id ?? projection.boundary_id ?? ""),
        retainedTailCount: Number(
          lineageProjection.retained_tail_count ??
            (Array.isArray(projection.retained_tail_message_ids) ? projection.retained_tail_message_ids.length : 0)
        )
      }
    }
  };
}

function contextHealthStatus(value: unknown, fallback: ContextUsage["health"]["status"]): ContextUsage["health"]["status"] {
  if (value === "healthy" || value === "managed" || value === "watch" || value === "critical" || value === "blocked") {
    return value;
  }
  return fallback;
}

function contextHealthSeverity(
  value: unknown,
  fallback: ContextUsage["health"]["severity"]
): ContextUsage["health"]["severity"] {
  if (value === "ok" || value === "warning" || value === "error") return value;
  return fallback;
}

function contextHealthFallbackReason(severity: ContextUsage["health"]["severity"]): string {
  if (severity === "error") return "Context is close to its limit.";
  if (severity === "warning") return "Context is getting busy.";
  return "Context has room for the next step.";
}

function objectRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, Number(item ?? 0)])
  );
}

function mapLocalLlmHealth(health: BackendLocalLlmHealth): LocalLLMHealth {
  const fallbackBackend =
    health.available && health.kind
      ? {
          kind: health.kind,
          base_url: health.base_url,
          models: health.models,
          model: health.model
        }
      : null;
  const selected = health.selected_backend ?? fallbackBackend;
  const models = Array.isArray(selected?.models)
    ? selected.models.map(String)
    : Array.isArray(health.models)
      ? health.models.map(String)
      : [];
  const model = selected?.model ? String(selected.model) : models[0];

  return {
    available: Boolean(health.available),
    selectedBackend: selected
      ? {
          kind: String(selected.kind ?? health.kind ?? "local"),
          baseUrl: String(selected.base_url ?? health.base_url ?? ""),
          models,
          ...(model ? { model } : {})
        }
      : null,
    probeOrder: (health.probe_order ?? []).map(String),
    error: typeof health.error === "string" ? health.error : "",
    readiness: mapLocalModelReadiness(health.readiness)
  };
}

function mapLocalModelReadiness(readiness?: BackendLocalModelReadiness): LocalModelReadiness | undefined {
  if (!readiness || typeof readiness !== "object") return undefined;
  return {
    canInstall: Boolean(readiness.can_install),
    recommendedModel: String(readiness.recommended_model ?? ""),
    reason: String(readiness.reason ?? ""),
    checks: (readiness.checks ?? []).map((check) => ({
      key: String(check.key ?? ""),
      label: String(check.label ?? ""),
      ok: Boolean(check.ok),
      actual: String(check.actual ?? ""),
      required: String(check.required ?? "")
    })),
    memoryTotalBytes: Number(readiness.memory_total_bytes ?? 0),
    diskFreeBytes: Number(readiness.disk_free_bytes ?? 0),
    cpuLogicalCores: Number(readiness.cpu_logical_cores ?? 0),
    gpuSummary: readiness.gpu_summary ? String(readiness.gpu_summary) : ""
  };
}

function mapLocalModelSetupPlan(plan: BackendLocalModelSetupPlan): LocalModelSetupPlan {
  return {
    ready: Boolean(plan.ready),
    canInstall: Boolean(plan.can_install),
    model: String(plan.model ?? ""),
    readiness: mapLocalModelReadiness(plan.readiness),
    installed: Boolean(plan.installed),
    running: Boolean(plan.running),
    models: (plan.models ?? []).map(String),
    hasModel: Boolean(plan.has_model),
    runtimeSource: String(plan.runtime_source ?? ""),
    bundledRuntimeAvailable: Boolean(plan.bundled_runtime_available),
    bundledRuntimePath: String(plan.bundled_runtime_path ?? ""),
    bundledModelsAvailable: Boolean(plan.bundled_models_available),
    bundledModelsPath: String(plan.bundled_models_path ?? ""),
    bundledModelAvailable: Boolean(plan.bundled_model_available),
    bundledModelConfigured: Boolean(plan.bundled_model_configured),
    bundleManifest: mapLocalModelBundleManifest(plan.bundle_manifest),
    steps: (plan.steps ?? []).map((step) => ({
      key: String(step.key ?? ""),
      label: String(step.label ?? ""),
      state: mapLocalModelSetupStepState(step.state),
      detail: String(step.detail ?? "")
    })),
    nextAction: String(plan.next_action ?? "")
  };
}

function mapLocalModelBundleManifest(manifest?: BackendLocalModelBundleManifest): LocalModelSetupPlan["bundleManifest"] {
  if (!manifest || typeof manifest !== "object") return { present: false };
  return {
    present: Boolean(manifest.present),
    valid: manifest.valid === undefined ? undefined : Boolean(manifest.valid),
    path: optionalString(manifest.path),
    model: optionalString(manifest.model),
    acceptedLicenses: manifest.accepted_licenses === undefined ? undefined : Boolean(manifest.accepted_licenses),
    runtimeSha256: optionalString(manifest.runtime_sha256),
    modelsSha256: optionalString(manifest.models_sha256),
    runtimeFiles: numberOrUndefined(manifest.runtime_files),
    modelsFiles: numberOrUndefined(manifest.models_files),
    error: optionalString(manifest.error)
  };
}

function mapLocalModelSetupStepState(value: unknown): LocalModelSetupPlan["steps"][number]["state"] {
  if (value === "pending" || value === "current" || value === "done" || value === "blocked") {
    return value;
  }
  return "pending";
}

function mapInstalledApp(app: BackendInstalledApp): InstalledApp {
  return {
    id: String(app.id ?? app.name ?? ""),
    name: String(app.name ?? app.id ?? ""),
    path: app.path,
    command: app.command,
    source: String(app.source ?? "unknown"),
    allowlisted: Boolean(app.allowlisted)
  };
}

function mapFileRevealResult(result: BackendFileRevealResult): FileRevealResult {
  return {
    ok: result.ok !== false,
    path: optionalString(result.path),
    revealed: Boolean(result.revealed),
    shown: Boolean(result.shown ?? result.revealed),
    error: optionalString(result.error)
  };
}

function mapSkillsCatalog(data: BackendSkillsCatalog): SkillsCatalog {
  return {
    skills: (data.skills ?? []).map(mapInstalledSkill),
    count: Number(data.count ?? data.skills?.length ?? 0),
    directories: (data.directories ?? []).map(String),
    installDirectory: String(data.install_directory ?? "")
  };
}

function mapSkillImportResult(data: BackendSkillImportResult): SkillImportResult {
  return {
    skill: mapInstalledSkill(data.skill),
    refresh: {
      ok: Boolean(data.refresh?.ok),
      toolCount: Number(data.refresh?.tool_count ?? 0),
      skillCount: Number(data.refresh?.skill_count ?? 0)
    }
  };
}

function mapInstalledSkill(skill: BackendInstalledSkill): InstalledSkill {
  return {
    name: String(skill.name ?? ""),
    version: String(skill.version ?? ""),
    agentOwner: String(skill.agent_owner ?? ""),
    risk: String(skill.risk ?? ""),
    root: String(skill.root ?? ""),
    manifestPath: String(skill.manifest_path ?? ""),
    status: String(skill.status ?? "error"),
    tools: (skill.tools ?? []).map((tool) => ({
      name: String(tool.name ?? ""),
      description: String(tool.description ?? ""),
      agentOwner: String(tool.agent_owner ?? ""),
      risk: String(tool.risk ?? ""),
      permissions: Array.isArray(tool.permissions) ? tool.permissions.map(String) : [],
      executionType: String(tool.execution_type ?? ""),
      entry: String(tool.entry ?? ""),
      supportsDryRun: Boolean(tool.supports_dry_run),
      requiresAuthorizedPath: Boolean(tool.requires_authorized_path),
      rollbackHint: String(tool.rollback_hint ?? "")
    })),
    safety: {
      ok: Boolean(skill.safety?.ok),
      issues: (skill.safety?.issues ?? []).map((issue) => ({
        severity: issue.severity === "warning" ? "warning" : "error",
        location: String(issue.location ?? ""),
        message: String(issue.message ?? "")
      }))
    },
    error: skill.error ? String(skill.error) : undefined
  };
}

function mapProcess(process: BackendProcess): SystemProcess {
  return {
    pid: Number(process.pid ?? 0),
    name: String(process.name ?? "未知进程"),
    username: process.username,
    cpuPercent: Number(process.cpu_percent ?? 0),
    memoryBytes: Number(process.memory_bytes ?? 0),
    status: process.status
  };
}

function mapChatMessage(message: BackendChatMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    author: message.author,
    content: zhBackendText(message.content),
    createdAt: normalizeTimestamp(message.created_at ?? message.createdAt),
    status: message.status === "failed" ? "failed" : "sent"
  };
}

function normalizeTimestamp(value: unknown, fallback = new Date().toISOString()): string {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? fallback : new Date(timestamp).toISOString();
}

function mapIntentSuggestion(suggestion: BackendIntentSuggestion): IntentSuggestion {
  return {
    id: suggestion.id,
    title: suggestion.title,
    prompt: zhBackendText(suggestion.prompt),
    confidence: Number(suggestion.confidence ?? 0),
    agentHint: suggestion.agent_hint,
    reason: suggestion.reason ? zhBackendText(suggestion.reason) : undefined
  };
}

function mapSuggestionLaunchResponse(
  data: BackendSuggestionLaunchResponse,
  fallbackPrompt: string
): PerceptionSuggestionLaunchResponse {
  const runId = data.run_id ?? data.run?.run_id;
  const engine = data.engine ?? data.run?.engine;
  const phase = data.phase ?? data.run?.phase ?? "queued";
  const message = data.message ?? data.run?.message ?? fallbackPrompt;
  const createdAt = data.run?.created_at ?? new Date().toISOString();
  const updatedAt = data.run?.updated_at ?? createdAt;

  return {
    runId,
    engine,
    message: {
      id: `${runId ?? crypto.randomUUID()}-suggestion-launched`,
      role: "assistant" as const,
      author: "Lengrvis",
      content: runId ? `已根据建议启动任务：${zhBackendText(message)}` : zhBackendText(message),
      createdAt: new Date().toISOString(),
      status: "sent" as const
    },
    taskUpdates: runId
      ? [
          {
            id: runId,
            runId,
            title: zhBackendText(message),
            description: `状态：${zhBackendTaskStatus(phase)}`,
            state: mapTaskState(phase),
            agent: runEngineAgentName(engine),
            createdAt,
            updatedAt
          }
        ]
      : []
  };
}

function mapStartupItem(item: BackendStartupItem): StartupItem {
  return {
    name: String(item.name ?? "启动项"),
    path: item.path,
    command: item.command,
    source: String(item.source ?? "unknown")
  };
}

function mapDiagnostic(data: BackendSystemDiagnostics, startupItems?: BackendStartupItem[]): SystemDiagnostic {
  return {
    info: data.info ?? {},
    disks: (data.disks ?? []).map((disk) => ({
      device: String(disk.device ?? ""),
      mountpoint: String(disk.mountpoint ?? ""),
      fstype: disk.fstype,
      usage: disk.usage
    })),
    network: data.network ?? {},
    battery: data.battery,
    topProcesses: (data.top_processes ?? []).map(mapProcess),
    startupItems: (startupItems ?? []).map(mapStartupItem),
    suggestions: (data.suggestions ?? []).map(zhBackendText),
    product: data.product
      ? {
          name: data.product.name ? String(data.product.name) : undefined,
          version: data.product.version ? String(data.product.version) : undefined
        }
      : undefined,
    updateChannel: data.update_channel
      ? {
          configured: Boolean(data.update_channel.configured),
          status: data.update_channel.status ? String(data.update_channel.status) : undefined,
          label: data.update_channel.label ? String(data.update_channel.label) : undefined,
          detail: data.update_channel.detail ? String(data.update_channel.detail) : undefined,
          checkAction: data.update_channel.check_action ? String(data.update_channel.check_action) : undefined,
          offlineOnly: data.update_channel.offline_only === undefined ? undefined : Boolean(data.update_channel.offline_only)
        }
      : undefined,
    localPaths: data.local_paths
      ? {
          dataDir: data.local_paths.data_dir ? String(data.local_paths.data_dir) : undefined,
          database: data.local_paths.database ? String(data.local_paths.database) : undefined,
          logDirs: (data.local_paths.log_dirs ?? []).map(String)
        }
      : undefined,
    audit: data.audit
      ? {
          verification: plainRecord(data.audit.verification),
          latestEvent: plainRecord(data.audit.latest_event) ?? null
        }
      : undefined,
    lanTransport: plainRecord(data.lan_transport),
    recentCounts: numberRecord(data.recent_counts),
    recentFailureCounts: numberRecord(data.recent_failure_counts),
    diagnosticHints: (data.diagnostic_hints ?? []).map(zhBackendText),
    diagnosticScope: data.diagnostic_scope ? String(data.diagnostic_scope) : undefined
  };
}

function mapDiagnosticExportResult(data: BackendDiagnosticExportResult): DiagnosticExportResult {
  return {
    ok: data.ok !== false,
    path: String(data.path ?? ""),
    filename: String(data.filename ?? ""),
    createdAt: String(data.created_at ?? ""),
    bytes: Number(data.bytes ?? 0),
    scope: String(data.scope ?? "local_only"),
    error: data.error ? String(data.error) : undefined
  };
}

function plainRecord(value: Record<string, unknown> | null | undefined): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value : undefined;
}

function numberRecord(value: Record<string, unknown> | undefined): Record<string, number> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, item]) => [key, Number(item)] as const)
      .filter(([, item]) => Number.isFinite(item))
  );
}

function formatDiffPreview(diffPreview: unknown): string {
  if (!diffPreview || typeof diffPreview !== "object") {
    return String(diffPreview ?? "无预览内容");
  }
  return JSON.stringify(localizeDiffPreview(diffPreview), null, 2);
}

function localizeDiffPreview(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(localizeDiffPreview);
  }
  if (!value || typeof value !== "object") {
    if (typeof value === "string") {
      return zhBackendText(value);
    }
    return value;
  }
  const labels: Record<string, string> = {
    dry_run: "试运行",
    operation: "操作",
    query: "查询",
    diff_preview: "变更预览",
    message: "说明",
    action: "动作",
    from: "来源",
    to: "目标",
    path: "路径",
    bytes: "字节数",
    would_create: "将创建",
    changed_paths: "变更路径",
    rollback_info: "回滚信息",
    error: "错误"
  };
  const actions: Record<string, string> = {
    preview: "预览",
    copy: "复制",
    move: "移动",
    rename: "重命名",
    trash: "移入回收站",
    write_text: "写入文本",
    generate_markdown_report: "生成 Markdown 报告",
    organize_files: "整理文件"
  };
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      const translatedKey = labels[key] ?? key;
      const translatedValue = typeof item === "string" && key === "action" ? actions[item] ?? item : localizeDiffPreview(item);
      return [translatedKey, translatedValue];
    })
  );
}

function mapBrowserLink(link: BackendBrowserLink): BrowserLinkResult {
  return {
    title: String(link.title ?? link.url ?? ""),
    url: String(link.url ?? "")
  };
}

function mapBrowserPage(page: BackendBrowserPage): BrowserPageSnapshot {
  return {
    ok: Boolean(page.ok),
    url: String(page.url ?? ""),
    title: String(page.title ?? ""),
    text: String(page.text ?? ""),
    links: (page.links ?? []).map(mapBrowserLink),
    truncated: page.truncated,
    adapter: page.adapter,
    error: page.error
  };
}

function mapBrowserSession(session: BackendBrowserSession): BrowserSession {
  return {
    id: String(session.id ?? ""),
    task_id: optionalString(session.task_id),
    current_url: String(session.current_url ?? session.url ?? ""),
    title: String(session.title ?? ""),
    status: String(session.status ?? "idle"),
    mode: String(session.mode ?? "watch"),
    created_at: String(session.created_at ?? new Date().toISOString()),
    updated_at: String(session.updated_at ?? new Date().toISOString()),
    paused: Boolean(session.paused),
    takeover: Boolean(session.takeover),
    last_observation: session.last_observation ?? null
  };
}

function mapBrowserActivityEvent(event: BackendBrowserActivityEvent): BrowserActivityEvent {
  return {
    id: String(event.id ?? crypto.randomUUID()),
    session_id: String(event.session_id ?? ""),
    task_id: optionalString(event.task_id),
    step_id: optionalString(event.step_id),
    type: String(event.type ?? "event"),
    action: isBrowserAction(event.action) ? event.action : undefined,
    url: optionalString(event.url),
    title: optionalString(event.title),
    risk_level: optionalString(event.risk_level),
    verdict: optionalString(event.verdict),
    ok: event.ok !== false,
    error: optionalString(event.error),
    screenshot_url: optionalString(event.screenshot_url),
    created_at: String(event.created_at ?? new Date().toISOString())
  };
}

function mapBrowserActivityEnvelope(data: BackendBrowserActivityEnvelope): BrowserActivityEvent {
  return mapBrowserActivityEvent(data.event ?? {
    id: crypto.randomUUID(),
    session_id: data.session?.id,
    type: data.ok === false ? "observe.failed" : "observe",
    action: { kind: "observe" },
    url: data.url ?? data.session?.current_url ?? data.session?.url,
    title: data.title ?? data.session?.title,
    ok: data.ok !== false,
    error: data.error,
    created_at: new Date().toISOString()
  });
}

function mapBrowserReplayExport(data: BackendBrowserReplayExport): BrowserReplayExport {
  return {
    ok: data.ok !== false,
    url: optionalString(data.url),
    path: optionalString(data.path),
    session: data.session ? mapBrowserSession(data.session) : undefined,
    events: Array.isArray(data.events) ? data.events.map(mapBrowserActivityEvent) : undefined,
    error: optionalString(data.error)
  };
}

function isBrowserAction(value: unknown): value is BrowserAction {
  return Boolean(value && typeof value === "object" && typeof (value as { kind?: unknown }).kind === "string");
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function numberOrUndefined(value: unknown): number | undefined {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? number : undefined;
}

function numberOrZero(value: unknown): number {
  return numberOrUndefined(value) ?? 0;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function arrayOfObjects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

function recordOrUndefined(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function tableRowsFromUnknown(value: unknown): string[][] {
  if (!Array.isArray(value)) return [];
  return value.map((row) => {
    if (Array.isArray(row)) {
      return row.map((cell) => String(cell ?? ""));
    }
    if (row && typeof row === "object") {
      return Object.values(row).map((cell) => String(cell ?? ""));
    }
    return [String(row ?? "")];
  });
}

function fileNameFromPath(path: string): string | undefined {
  const normalized = path.replace(/\\/g, "/");
  const name = normalized.split("/").filter(Boolean).pop();
  return name || undefined;
}

function emptyBrowserHostSnapshot(hostAvailable: boolean): BrowserHostSnapshot {
  return {
    sessions: [],
    events: [],
    activeSessionId: null,
    visible: false,
    hostAvailable
  };
}

function mergeBrowserSessionArrays(primary: BrowserSession[], secondary: BrowserSession[]): BrowserSession[] {
  const byId = new Map<string, BrowserSession>();
  for (const session of primary) byId.set(session.id, session);
  for (const session of secondary) byId.set(session.id, { ...byId.get(session.id), ...session });
  return [...byId.values()].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

function emptyPlan(): Plan {
  return {
    id: "empty",
    title: "暂无活动计划",
    objective: "提交一个任务后会在这里生成计划。",
    updatedAt: new Date().toISOString(),
    steps: []
  };
}

function emptySafetyReview(): SafetyReview {
  return {
    id: "empty",
    status: "clear",
    updatedAt: new Date().toISOString(),
    findings: []
  };
}

interface BackendChatRequest {
  message: string;
  mode: string;
}

interface BackendChatMessage {
  id: string;
  role: "system" | "developer" | "user" | "assistant" | "tool";
  author: string;
  content: string;
  created_at?: string;
  createdAt?: string;
  status?: string;
}

interface BackendChatResponse {
  task_id?: string | null;
  status?: string | null;
  message: string;
  delegated?: boolean;
  agent?: string;
}

interface BackendRunCreateRequest {
  message: string;
  mode: "privacy" | "efficiency" | "hybrid";
  engine: "auto" | "os" | "developer";
}

interface BackendRunCreateResponse {
  run_id: string;
  engine: "os" | "developer";
  phase: string;
}

interface BackendSuggestionLaunchRequest {
  suggestion_id: string;
  prompt?: string;
  mode: string;
}

interface BackendSuggestionLaunchResponse {
  run_id?: string;
  engine?: "auto" | "os" | "developer" | string;
  phase?: string;
  message?: string;
  run?: BackendRunState;
}

interface BackendRunState {
  run_id: string;
  engine: "os" | "developer" | string;
  phase: string;
  task_id?: string | null;
  message: string;
  mode: string;
  requested_engine: "auto" | "os" | "developer" | string;
  error?: string;
  created_at: string;
  updated_at: string;
  cleanup_plan?: unknown;
  cleanupPlan?: unknown;
  diff_preview?: unknown;
}

interface BackendRunEvent extends RunEventPayload {
  name: string;
}

interface BackendRunTimeline {
  run: BackendRunState;
  events: BackendRunEvent[];
  count: number;
}

export type BackendRunStreamEvent =
  | { type: "connected"; run_id: string; engine?: string; phase?: string }
  | { type: "replay.completed"; run_id: string; last_sequence: number }
  | { type: "heartbeat"; run_id: string }
  | BackendRealtimeStatusEvent
  | (RunEventPayload & { type: "run_event"; event: string });

interface BackendIntentSuggestion {
  id: string;
  title: string;
  prompt: string;
  confidence?: number;
  agent_hint?: string;
  reason?: string;
}

interface BackendTask {
  id: string;
  user_goal: string;
  status: string;
  mode: string;
  final_summary: string;
  created_at: string;
  updated_at: string;
  cleanup_plan?: unknown;
  cleanupPlan?: unknown;
  diff_preview?: unknown;
  boundary_events?: BackendBoundaryEvent[];
}

interface BackendTimeline {
  task: string;
  messages: BackendAgentMessage[];
  reviews: BackendSafetyReview[];
  recordings?: BackendStepRecording[];
  cleanup_plan?: unknown;
  cleanupPlan?: unknown;
  boundary_events?: BackendBoundaryEvent[];
}

interface BackendBoundaryEvent {
  id?: string;
  kind?: string;
  title?: string;
  detail?: string;
  severity?: string;
  step_id?: string;
  stepId?: string;
  created_at?: string;
  createdAt?: string;
  payload?: Record<string, unknown>;
}

interface BackendStepRecording {
  step_id?: string;
  tool_name?: string;
  agent?: string;
  frames?: BackendStepRecordingFrame[];
}

interface BackendStepRecordingPayload extends BackendStepRecording {
  kind?: string;
}

interface BackendStepRecordingFrame {
  phase?: string;
  ok?: boolean;
  captured_at?: string;
  url?: string;
  width?: number;
  height?: number;
  error?: string;
}

export type BackendTaskStreamEvent =
  | {
      type: "connected" | "heartbeat" | "agent_message";
      task_id: string;
      message?: BackendAgentMessage;
    }
  | BackendRealtimeStatusEvent;

export interface BackendRealtimeStatusEvent {
  type: "stream_status";
  status: "open" | "reconnecting" | "closed" | "error" | "malformed";
  endpoint: string;
  message: string;
  raw?: string;
  code?: number;
  reason?: string;
}

interface BackendAgentMessage {
  id: string;
  role?: "system" | "developer" | "user" | "assistant" | "tool";
  name?: string;
  from_agent?: string;
  message_type?: string;
  content: string;
  tool_calls?: AgentConversation["messages"][number]["toolCalls"];
  tool_call_id?: string;
  metadata?: {
    from_agent?: string;
    to_agent?: string;
    message_type?: string;
    structured_payload?: unknown;
    [key: string]: unknown;
  };
  structured_payload?: unknown;
  created_at: string;
}

interface BackendPlan {
  id: string;
  goal: string;
  assumptions?: string[];
  steps: Array<{
    id: string;
    agent_name: string;
    tool_name: string;
    description: string;
    status: string;
    risk_level?: string;
    requires_approval?: boolean;
    tool_effects?: string[];
    resource_kinds?: string[];
    trust_tier?: string;
    deferred_tool?: boolean;
  }>;
}

interface BackendSafetyReview {
  id: string;
  target_type: string;
  verdict: string;
  risk_level: string;
  reasons: string[];
  safe_alternative: string;
  created_at: string;
}

interface BackendTaskExplainEvidence {
  source?: string;
  id?: string;
  created_at?: string;
  actor?: string;
  event_type?: string;
  step_id?: string;
  summary?: string;
}

interface BackendTaskExplainReview {
  id?: string;
  step_id?: string | null;
  target_type?: string;
  verdict?: string;
  risk_level?: string;
  reasons?: string[];
  required_changes?: string[];
  user_confirmation_message?: string;
  safe_alternative?: string;
  created_at?: string;
  evidence?: BackendTaskExplainEvidence[];
}

interface BackendTaskExplainMessage {
  id?: string;
  step_id?: string | null;
  from_agent?: string;
  to_agent?: string | null;
  message_type?: string;
  content?: string;
  created_at?: string;
  evidence?: BackendTaskExplainEvidence[];
  action?: {
    kind?: string;
    tool_name?: string;
    rationale?: string;
    follow_up_question?: string;
  };
}

interface BackendTaskExplainStep {
  id?: string;
  step_id?: string;
  order?: number;
  agent_name?: string;
  tool_name?: string;
  description?: string;
  status?: string;
  risk_level?: string;
  requires_approval?: boolean;
  expected_observation?: string;
  rollback_strategy?: string;
  planner_reason?: string;
  safety_reviews?: BackendTaskExplainReview[];
  subagent_suggestions?: BackendTaskExplainMessage[];
  observations?: BackendTaskExplainMessage[];
}

interface BackendTaskExplainChainItem {
  stage?: string;
  title?: string;
  summary?: string;
  evidence?: BackendTaskExplainEvidence[];
}

interface BackendTaskExplain {
  task_id?: string;
  user_goal?: string;
  status?: string;
  mode?: string;
  generated_at?: string;
  complete?: boolean;
  missing_sections?: string[];
  data_sources?: Record<string, number>;
  user_goal_record?: {
    text?: string;
    evidence?: BackendTaskExplainEvidence[];
  };
  supervisor_judgment?: {
    summary?: string;
    delegate?: boolean;
    agent_hint?: string;
    inferred?: boolean;
    evidence?: BackendTaskExplainEvidence[];
  };
  planner_reasoning?: {
    summary?: string;
    plan_id?: string;
    goal?: string;
    assumptions?: string[];
    step_count?: number;
    global_risk_level?: string;
    requires_user_approval?: boolean;
    evidence?: BackendTaskExplainEvidence[];
  };
  global_safety_reviews?: BackendTaskExplainReview[];
  steps?: BackendTaskExplainStep[];
  subagent_suggestions?: BackendTaskExplainMessage[];
  final_result?: {
    status?: string;
    summary?: string;
    safety_reviews?: BackendTaskExplainReview[];
    evidence?: BackendTaskExplainEvidence[];
  };
  chain?: BackendTaskExplainChainItem[];
}

interface BackendApproval {
  id: string;
  task_id?: string | null;
  step_id?: string | null;
  approval_type: string;
  message: string;
  diff_preview: unknown;
  tool_name?: string;
  risk_level?: string;
  tool_trust_tier?: string;
  tool_effects?: string[];
  resource_kinds?: string[];
  policy_mode?: string;
  permission_mode?: string;
  dry_run_summary?: string;
  model_action?: unknown;
  runtime_control_fields?: unknown;
  runtime_fields?: unknown;
  engineering_boundary?: unknown;
  status: string;
  created_at: string;
}

interface BackendDocumentParseRequest {
  path: string;
  include_text?: boolean;
}

interface BackendDocumentAskRequest {
  path?: string;
  document_id?: string;
  question: string;
  top_k?: number;
}

interface BackendDocumentCompareRequest {
  paths: string[];
  focus?: string;
}

interface BackendDocumentBlock {
  id?: string;
  block_id?: string;
  type?: string;
  kind?: string;
  text?: string;
  content?: string;
  level?: number | string;
  page?: number | string;
  order?: number | string;
  index?: number | string;
  columns?: unknown;
  rows?: unknown;
  metadata?: unknown;
}

interface BackendDocumentTable {
  id?: string;
  table_id?: string;
  title?: string;
  name?: string;
  columns?: unknown;
  rows?: unknown;
  page?: number | string;
  source_block_id?: string;
  sourceBlockId?: string;
}

interface BackendDocumentCitation {
  id?: string;
  label?: string;
  text?: string;
  snippet?: string;
  content?: string;
  path?: string;
  block_id?: string;
  blockId?: string;
  page?: number | string;
  score?: number | string;
}

interface BackendDocumentIR {
  id?: string;
  document_id?: string;
  path?: string;
  title?: string;
  name?: string;
  mime_type?: string;
  mimeType?: string;
  language?: string;
  summary?: string;
  text?: string;
  truncated?: boolean;
  blocks?: BackendDocumentBlock[];
  tables?: BackendDocumentTable[];
  citations?: BackendDocumentCitation[];
  metadata?: unknown;
  created_at?: string;
  createdAt?: string;
}

interface BackendDocumentAskResponse {
  answer?: string;
  summary?: string;
  citations?: unknown;
  citation_items?: BackendDocumentCitation[];
  citations_detail?: BackendDocumentCitation[];
  source_chunks?: BackendDocumentCitation[];
  sources?: BackendDocumentCitation[];
  note?: string;
}

interface BackendDocumentCompareDifference {
  id?: string;
  title?: string;
  field?: string;
  detail?: string;
  summary?: string;
  text?: string;
  severity?: string;
  citations?: BackendDocumentCitation[];
}

interface BackendDocumentCompareResponse {
  summary?: string;
  documents?: BackendDocumentIR[];
  differences?: BackendDocumentCompareDifference[];
  items?: BackendDocumentCompareDifference[];
  tables?: BackendDocumentTable[];
  note?: string;
}

interface BackendCleanupScanRequest {
  roots?: string[];
  threshold_mb?: number;
  include_caches?: boolean;
}

interface BackendCleanupPlanRequest extends BackendCleanupScanRequest {
  item_ids?: string[];
  prefer_trash?: boolean;
}

interface BackendCleanupExecuteRequest {
  roots?: string[];
  plan_id?: string;
  content_hash?: string;
  selected_item_ids?: string[];
  dry_run?: boolean;
  approved?: boolean;
  approval_id?: string;
}

interface BackendCleanupRollbackRequest {
  plan_id?: string;
  execution_id?: string;
}

interface BackendCleanupItem {
  id?: string;
  path?: string;
  name?: string;
  action?: string;
  disposition?: string;
  mode?: string;
  delete_mode?: string;
  bucket?: string;
  size_bytes?: number;
  sizeBytes?: number;
  bytes?: number;
  size_mb?: number;
  sizeMb?: number;
  category?: string;
  detail?: string;
  description?: string;
  reason?: string;
  risk_level?: string;
  riskLevel?: string;
  can_rollback?: boolean;
  canRollback?: boolean;
  selected?: boolean;
  modified_at?: string;
  modifiedAt?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

interface BackendCleanupPlan {
  id?: string;
  plan_id?: string;
  content_hash?: string;
  contentHash?: string;
  title?: string;
  summary?: string;
  detail?: string;
  status?: string;
  created_at?: string;
  createdAt?: string;
  updated_at?: string;
  updatedAt?: string;
  total_bytes?: number;
  totalBytes?: number;
  reclaimable_bytes?: number;
  reclaimableBytes?: number;
  freed_bytes?: number;
  freedBytes?: number;
  permanent_delete_bytes?: number;
  permanentDeleteBytes?: number;
  trash_bytes?: number;
  trashBytes?: number;
  risk_warnings?: unknown;
  riskWarnings?: unknown;
  warnings?: unknown;
  items?: BackendCleanupItem[];
  buckets?: Record<string, unknown>;
  cleanup_plan?: unknown;
  plan?: unknown;
}

interface BackendCleanupExecutionResult {
  ok?: boolean;
  plan_id?: string;
  planId?: string;
  execution_id?: string;
  executionId?: string;
  freed_bytes?: number;
  freedBytes?: number;
  executed?: unknown;
  rolled_back?: unknown;
  rolledBack?: unknown;
  errors?: unknown;
}

export interface MobilePairingCode extends DesktopMobilePairingCode {}

export interface MobileDevice {
  device_id: string;
  device_name: string;
  status?: string;
  created_at: string;
  updated_at: string;
  revoked_at?: string;
  remote_input_grants?: RemoteInputGrant[];
}

export interface MobileDeviceList {
  devices: MobileDevice[];
}

export interface RemoteInputGrant {
  id: string;
  status?: string;
  scope?: "remote:input" | string;
  created_at?: string;
  expires_at?: string;
  revoked_at?: string;
}

export interface RemoteInputGrantIssueResult {
  grant_id: string;
  device_id: string;
  expires_at: string;
  expires_in: number;
  device?: MobileDevice;
}

interface BackendFileSearchResponse {
  index_results?: Array<{ file_id?: string; path: string; snippet?: string }>;
  name_results?: Array<{ path: string; name?: string }>;
  name_search?: {
    count?: number | string;
    scanned?: number | string;
    truncated?: boolean;
    status?: string;
  };
}

interface BackendLocalLibraryItem {
  id: string;
  path: string;
  name: string;
  parent: string;
  kind: string;
  extension: string;
  mime_type?: string;
  size?: number;
  created_at?: number;
  modified_at?: number;
  preview_url?: string;
  group_label?: string;
  icon_url?: string;
  width?: number;
  height?: number;
}

interface BackendLocalLibraryResponse {
  section: string;
  roots?: string[];
  items?: BackendLocalLibraryItem[];
  count?: number;
  total?: number;
  scanned?: number;
  truncated?: boolean;
  stats?: {
    size?: number;
    by_extension?: Record<string, number>;
  };
}

export interface BackendClusterEntry {
  cluster_id: number | string;
  size: number;
  preview: string[];
  suggested_name?: string;
  group_by?: string;
  group_value?: string;
}

export interface BackendClusterResponse {
  ok: boolean;
  clusters: BackendClusterEntry[];
  count?: number;
  total?: number;
  method?: string;
  group_by?: string;
  cluster_by?: string;
  error?: string;
}

export interface FileClusterOptions {
  k?: number;
  groupBy?: string;
  group_by?: string;
  clusterBy?: string;
  cluster_by?: string;
  paths?: string[];
  imagePaths?: string[];
  image_paths?: string[];
  images?: string[];
  limit?: number;
  metadataWeight?: number;
  metadata_weight?: number;
}

interface BackendClusterRequest {
  k?: number;
  group_by?: string;
  cluster_by?: string;
  paths?: string[];
  image_paths?: string[];
  images?: string[];
  limit?: number;
  metadata_weight?: number;
}

interface BackendSettings {
  provider_name?: string;
  base_url?: string;
  model?: string;
  review_model?: string;
  wire_api?: string;
  requires_openai_auth?: boolean;
  model_reasoning_effort?: string;
  disable_response_storage?: boolean;
  temperature?: number;
  max_tokens?: number;
  timeout?: number;
  llm_api_max_retries?: number;
  llm_api_retry_backoff_seconds?: number;
  llm_api_circuit_failure_threshold?: number;
  llm_api_circuit_cooldown_seconds?: number;
  model_context_window?: number;
  model_auto_compact_token_limit?: number;
  allowed_directories?: string[];
  allow_browser_network?: boolean;
  remote_desktop_enabled?: boolean;
  app_allowlist?: string[];
  browser_max_page_bytes?: number;
  browser_screenshot_dir?: string;
  onnx_model_path?: string;
  onnx_execution_provider?: string;
  onnx_provider_preference?: string;
  onnx_directml_device_id?: string;
  onnx_openvino_device?: string;
  onnx_openvino_cache_dir?: string;
  onnx_warm_on_startup?: boolean;
  onnx_model_family?: string;
  embedding_backend?: string;
  onnx_embedding_model_path?: string;
  onnx_embedding_execution_provider?: string;
  onnx_embedding_model_id?: string;
  onnx_embedding_max_batch_size?: number;
  image_embedding_backend?: string;
  onnx_image_embedding_model_path?: string;
  onnx_image_embedding_execution_provider?: string;
  onnx_image_embedding_model_id?: string;
  onnx_image_embedding_max_batch_size?: number;
  ocr_backend?: string;
  ocr_execution_provider?: string;
  ocr_openvino_model_dir?: string;
  ocr_openvino_device?: string;
  ocr_lang?: string;
  ocr_min_confidence?: number;
  ocr_batch_size?: number;
  mode?: string;
  permission_mode?: string;
  allow_cloud_context?: boolean;
  allow_file_content_upload?: boolean;
  confirmation_nonce?: string;
  mcp_servers?: Array<{
    id?: string;
    name?: string;
    url?: string;
    command?: string;
    args?: string[];
    enabled?: boolean;
    transport?: string;
    auth?: Record<string, unknown>;
    [key: string]: unknown;
  }>;
}

interface SensitiveChangeConfirmation {
  required?: boolean;
  nonce?: string;
  expires_at?: string;
  changes?: Array<Record<string, unknown>>;
}

interface BackendPermissionPolicy {
  rules?: BackendPermissionRule[];
  updated_at?: string;
}

interface BackendPermissionRule {
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

interface BackendLlmCapabilities {
  tools?: boolean;
  structured_json?: boolean;
  vision?: boolean;
  embeddings?: boolean;
  prompt_cache?: boolean;
  responses_api?: boolean;
  reasoning_effort?: boolean;
  usage_breakdown?: boolean;
  local?: boolean;
  cloud?: boolean;
}

interface BackendLlmProfile {
  provider_name?: string;
  model?: string;
  base_url?: string;
  wire_api?: string;
  location?: string;
  active_backend?: string;
  capabilities?: BackendLlmCapabilities;
  model_profile?: {
    model?: string;
    context_window?: number;
    max_output_tokens?: number;
    known?: boolean;
    family?: string;
  };
}

interface BackendLlmProfileResponse {
  mode?: string;
  task?: string;
  profile?: BackendLlmProfile;
  degraded?: boolean;
  error?: string;
}

interface BackendLlmHealth {
  active?: {
    available?: boolean;
    degraded?: boolean;
    provider?: string;
    model?: string;
    profile?: BackendLlmProfile;
    error?: string;
  };
  retry?: {
    max_retries?: number;
    backoff_seconds?: number;
    circuit_failure_threshold?: number;
    circuit_cooldown_seconds?: number;
    circuit?: {
      state?: string;
      failures?: number;
      retry_after_seconds?: number;
    };
  };
}

interface BackendLlmCostSummary {
  window_hours?: number;
  calls?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  total_cost_usd?: number | null;
  estimated?: boolean;
  last_event_at?: string;
  by_model?: Array<{
    provider?: string;
    model?: string;
    calls?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    total_cost_usd?: number;
    estimated?: boolean;
  }>;
}

interface BackendContextUsageWarning {
  token_count?: number;
  threshold?: number;
  percent_left?: number;
  is_above_warning_threshold?: boolean;
  is_above_error_threshold?: boolean;
  is_above_auto_compact_threshold?: boolean;
  is_at_blocking_limit?: boolean;
}

interface BackendContextProjectionSummary {
  enabled?: boolean;
  strategy?: string;
  compacted?: boolean;
  original_tokens?: number;
  projected_tokens?: number;
  tokens_saved?: number;
  messages_removed?: number;
  adjustments?: unknown[];
  description?: string;
}

interface BackendContextUsageProjection {
  enabled?: boolean;
  original_count?: number;
  projected_count?: number;
  original_tokens?: number;
  projected_tokens?: number;
  compacted?: boolean;
  micro_compacted?: boolean;
  history_snipped?: boolean;
  session_summary_added?: boolean;
  strategy?: string;
  source?: string;
  boundary_id?: string;
  retained_tail_message_ids?: string[];
  summary?: BackendContextProjectionSummary;
}

interface BackendContextUsageHealth {
  status?: string;
  severity?: string;
  reason?: string;
  used_percent?: number;
  free_percent?: number;
  free_tokens?: number;
  projected_tokens?: number;
  projected_percent?: number;
  projected_free_tokens?: number;
  is_healthy?: boolean;
}

interface BackendContextUsageLineage {
  task_id?: string;
  history_source?: string;
  message_count?: number;
  system_message_count?: number;
  agent_message_count?: number;
  message_roles?: Record<string, unknown>;
  local_tool_count?: number;
  mcp_tool_count?: number;
  session_memory_item_count?: number;
  include_registered_tools?: boolean;
  include_session_memory?: boolean;
  include_projection?: boolean;
  projection?: {
    source?: string;
    strategy?: string;
    boundary_id?: string;
    retained_tail_count?: number;
  };
}

interface BackendContextUsage {
  total_tokens?: number;
  used_tokens?: number;
  free_tokens?: number;
  effective_context_window?: number;
  model_context_window?: number;
  auto_compact_threshold?: number;
  manual_compact_limit?: number;
  reserved_output_tokens?: number;
  warning?: BackendContextUsageWarning;
  projection?: BackendContextUsageProjection;
  health?: BackendContextUsageHealth;
  lineage?: BackendContextUsageLineage;
}

interface BackendLocalLlmBackend {
  kind?: string;
  base_url?: string;
  models?: string[];
  model?: string;
}

interface BackendLocalModelReadinessCheck {
  key?: string;
  label?: string;
  ok?: boolean;
  actual?: string;
  required?: string;
}

interface BackendLocalModelReadiness {
  can_install?: boolean;
  recommended_model?: string;
  reason?: string;
  checks?: BackendLocalModelReadinessCheck[];
  memory_total_bytes?: number;
  disk_free_bytes?: number;
  cpu_logical_cores?: number;
  gpu_summary?: string;
}

interface BackendLocalLlmHealth {
  available?: boolean;
  selected_backend?: BackendLocalLlmBackend | null;
  probe_order?: string[];
  error?: string;
  kind?: string;
  base_url?: string;
  models?: string[];
  model?: string;
  readiness?: BackendLocalModelReadiness;
}

interface BackendLocalModelSetupStep {
  key?: string;
  label?: string;
  state?: string;
  detail?: string;
}

interface BackendLocalModelSetupPlan {
  ready?: boolean;
  can_install?: boolean;
  model?: string;
  readiness?: BackendLocalModelReadiness;
  installed?: boolean;
  running?: boolean;
  models?: string[];
  has_model?: boolean;
  runtime_source?: string;
  bundled_runtime_available?: boolean;
  bundled_runtime_path?: string;
  bundled_models_available?: boolean;
  bundled_models_path?: string;
  bundled_model_available?: boolean;
  bundled_model_configured?: boolean;
  bundle_manifest?: BackendLocalModelBundleManifest;
  steps?: BackendLocalModelSetupStep[];
  next_action?: string;
}

interface BackendLocalModelBundleManifest {
  present?: boolean;
  valid?: boolean;
  path?: string;
  model?: string;
  accepted_licenses?: boolean;
  runtime_sha256?: string;
  models_sha256?: string;
  runtime_files?: number;
  models_files?: number;
  error?: string;
}

interface BackendHardwareAccelerationStatus {
  available?: boolean;
  kind?: string;
  model_path?: string;
  execution_provider?: string;
  available_providers?: string[];
  generation_runtime?: string;
  runtime_package?: string;
  configured_provider?: string;
  selected_provider?: string;
  runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: {
    available?: boolean;
    provider?: string;
    provider_available?: boolean;
    packages?: string[];
    errors?: Record<string, string>;
  };
  errors?: string[];
  error?: string;
  llm?: {
    runtime?: string;
    available?: boolean;
    model_path?: string;
    configured_provider?: string;
    selected_provider?: string;
    runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
    winml?: {
      available?: boolean;
      provider?: string;
      provider_available?: boolean;
      packages?: string[];
      errors?: Record<string, string>;
    };
    errors?: string[];
  };
  text_embedding?: BackendHardwareAccelerationComponentStatus;
  image_embedding?: BackendHardwareAccelerationComponentStatus;
  ocr?: BackendHardwareAccelerationComponentStatus;
}

interface BackendHardwareAccelerationComponentStatus {
  available?: boolean;
  component?: string;
  kind?: string;
  model_path?: string;
  execution_provider?: string;
  available_providers?: string[];
  runtime_package?: string;
  configured_provider?: string;
  selected_provider?: string;
  runtime_packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>;
  winml?: BackendHardwareAccelerationStatus["winml"];
  selected_backend?: string;
  runtime?: string;
  model?: string;
  errors?: string[];
  error?: string;
}

interface BackendHardwareAccelerationSmoke {
  ok?: boolean;
  available?: boolean;
  status?: "ready" | "unavailable";
  operation?: "warmup" | "test_generate" | "test_embedding" | "test_ocr" | "test_image_embedding";
  error?: string;
  errors?: string[];
  message?: string;
  count?: number;
  dim?: number;
  source?: string;
  backend?: {
    kind?: string;
    model_path?: string;
    execution_provider?: string;
    available_providers?: string[];
    generation_runtime?: string;
    runtime_package?: string;
    model_family?: string;
    provider_options?: Record<string, string>;
  };
  llm?: BackendHardwareAccelerationStatus["llm"];
  text_embedding?: BackendHardwareAccelerationComponentStatus;
  image_embedding?: BackendHardwareAccelerationComponentStatus;
  ocr?: BackendHardwareAccelerationComponentStatus;
}

interface HardwareAccelerationSmokeRequest {
  operation?: "warmup" | "test_generate" | "test_embedding" | "test_ocr" | "test_image_embedding";
  prompt?: string;
  maxTokens?: number;
  modelPath?: string;
  texts?: string[];
  imagePath?: string;
}

type HardwareAccelerationSmokeRequestBody = {
  model_path?: string;
  prompt?: string;
  max_tokens?: number;
  texts?: string[];
  image_path?: string;
};

function mapHardwareAccelerationStatus(status: BackendHardwareAccelerationStatus): HardwareAccelerationStatusPayload {
  return {
    available: Boolean(status.available),
    kind: String(status.kind ?? "onnx"),
    modelPath: String(status.model_path ?? ""),
    executionProvider: String(status.execution_provider ?? ""),
    availableProviders: (status.available_providers ?? []).map(String),
    generationRuntime: String(status.generation_runtime ?? ""),
    runtimePackage: status.runtime_package ? String(status.runtime_package) : undefined,
    configuredProvider: status.configured_provider ? String(status.configured_provider) : undefined,
    selectedProvider: status.selected_provider ? String(status.selected_provider) : undefined,
    runtimePackages: mapRuntimePackages(status.runtime_packages),
    winml: status.winml ? mapWinmlStatus(status.winml) : undefined,
    errors: Array.isArray(status.errors) ? status.errors.map(String) : status.error ? [String(status.error)] : [],
    error: status.error ? String(status.error) : undefined,
    llm: status.llm ? mapHardwareAccelerationLlm(status.llm) : undefined,
    textEmbedding: status.text_embedding ? mapHardwareAccelerationComponent(status.text_embedding) : undefined,
    imageEmbedding: status.image_embedding ? mapHardwareAccelerationComponent(status.image_embedding) : undefined,
    ocr: status.ocr ? mapHardwareAccelerationComponent(status.ocr) : undefined
  };
}

function mapHardwareAccelerationSmoke(data: BackendHardwareAccelerationSmoke): HardwareAccelerationSmokePayload {
  return {
    ok: Boolean(data.ok),
    available: Boolean(data.available),
    status: data.status === "ready" ? "ready" : "unavailable",
    operation: mapHardwareAccelerationOperation(data.operation),
    error: data.error ? String(data.error) : undefined,
    errors: Array.isArray(data.errors) ? data.errors.map(String) : [],
    message: data.message ? String(data.message) : undefined,
    count: data.count !== undefined ? Number(data.count) : undefined,
    dim: data.dim !== undefined ? Number(data.dim) : undefined,
    source: data.source ? String(data.source) : undefined,
    backend: data.backend
      ? {
          kind: String(data.backend.kind ?? ""),
          model_path: String(data.backend.model_path ?? ""),
          execution_provider: String(data.backend.execution_provider ?? ""),
          available_providers: (data.backend.available_providers ?? []).map(String),
          generation_runtime: String(data.backend.generation_runtime ?? ""),
          runtime_package: data.backend.runtime_package ? String(data.backend.runtime_package) : undefined,
          model_family: data.backend.model_family ? String(data.backend.model_family) : undefined,
          provider_options: data.backend.provider_options ? objectStringRecord(data.backend.provider_options) : {}
        }
      : undefined,
    llm: data.llm ? mapHardwareAccelerationLlm(data.llm) : undefined
  };
}

function mapHardwareAccelerationLlm(llm: NonNullable<BackendHardwareAccelerationStatus["llm"]>): HardwareAccelerationStatusPayload["llm"] {
  return {
    runtime: String(llm.runtime ?? ""),
    available: Boolean(llm.available),
    modelPath: String(llm.model_path ?? ""),
    configuredProvider: llm.configured_provider ? String(llm.configured_provider) : undefined,
    selectedProvider: llm.selected_provider ? String(llm.selected_provider) : undefined,
    runtimePackages: mapRuntimePackages(llm.runtime_packages),
    winml: llm.winml ? mapWinmlStatus(llm.winml) : undefined,
    errors: Array.isArray(llm.errors) ? llm.errors.map(String) : []
  };
}

function mapHardwareAccelerationOperation(
  operation?: BackendHardwareAccelerationSmoke["operation"]
): HardwareAccelerationSmokePayload["operation"] {
  if (
    operation === "test_generate" ||
    operation === "test_embedding" ||
    operation === "test_ocr" ||
    operation === "test_image_embedding"
  ) {
    return operation;
  }
  return "warmup";
}

function mapHardwareAccelerationComponent(
  component: BackendHardwareAccelerationComponentStatus
): NonNullable<HardwareAccelerationStatusPayload["textEmbedding"]> {
  return {
    available: Boolean(component.available),
    component: component.component ? String(component.component) : undefined,
    kind: component.kind ? String(component.kind) : undefined,
    modelPath: String(component.model_path ?? ""),
    executionProvider: String(component.execution_provider ?? ""),
    availableProviders: (component.available_providers ?? []).map(String),
    runtimePackage: component.runtime_package ? String(component.runtime_package) : undefined,
    configuredProvider: component.configured_provider ? String(component.configured_provider) : undefined,
    selectedProvider: component.selected_provider ? String(component.selected_provider) : undefined,
    runtimePackages: mapRuntimePackages(component.runtime_packages),
    winml: component.winml ? mapWinmlStatus(component.winml) : undefined,
    selectedBackend: component.selected_backend ? String(component.selected_backend) : undefined,
    runtime: component.runtime ? String(component.runtime) : undefined,
    model: component.model ? String(component.model) : undefined,
    errors: Array.isArray(component.errors) ? component.errors.map(String) : component.error ? [String(component.error)] : [],
    error: component.error ? String(component.error) : undefined
  };
}

function mapWinmlStatus(winml: NonNullable<BackendHardwareAccelerationStatus["winml"]>): NonNullable<HardwareAccelerationStatusPayload["winml"]> {
  return {
    available: Boolean(winml.available),
    provider: String(winml.provider ?? "WindowsMLExecutionProvider"),
    providerAvailable: Boolean(winml.provider_available),
    packages: (winml.packages ?? []).map(String),
    errors: Object.fromEntries(Object.entries(winml.errors ?? {}).map(([key, value]) => [key, String(value)]))
  };
}

function mapRuntimePackages(
  packages?: Record<string, { available?: boolean; module?: string; version?: string; error?: string }>
): Record<string, { available?: boolean; module?: string; version?: string; error?: string }> | undefined {
  if (!packages) return undefined;
  return Object.fromEntries(
    Object.entries(packages).map(([key, value]) => [
      key,
      {
        available: Boolean(value.available),
        module: value.module ? String(value.module) : undefined,
        version: value.version ? String(value.version) : undefined,
        error: value.error ? String(value.error) : undefined
      }
    ])
  );
}

function objectStringRecord(value: Record<string, string> | undefined): Record<string, string> | undefined {
  if (!value) return undefined;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item)]));
}

function normalizeExecutionProvider(value: string): AppSettings["onnxExecutionProvider"] {
  const lowered = value.trim().toLowerCase();
  if (!lowered) return "";
  if (["auto", "winml", "directml", "openvino", "cpu"].includes(lowered)) {
    return lowered === "auto" ? "" : (lowered[0].toUpperCase() + lowered.slice(1)) as AppSettings["onnxExecutionProvider"];
  }
  if (lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "dml") return "DirectML";
  return value;
}

function normalizeHardwareRuntime(value: string): string {
  const lowered = String(value ?? "").trim().toLowerCase();
  if (!lowered || lowered === "auto") return "";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "directml" || lowered === "dml") return "DirectML";
  if (lowered === "openvino") return "OpenVINO";
  if (lowered === "cpu") return "CPU";
  return value;
}

interface BackendAuditEvent {
  id: string;
  task_id?: string;
  event_type: string;
  actor: string;
  created_at: string;
}

interface BackendSystemInfo {
  platform?: string;
  system?: string;
  machine?: string;
}

interface BackendInstalledApp {
  id?: string;
  name?: string;
  path?: string;
  command?: string;
  source?: string;
  allowlisted?: boolean;
}

interface BackendAppsResponse {
  apps: BackendInstalledApp[];
}

interface BackendFileRevealResult {
  ok?: boolean;
  path?: string;
  revealed?: boolean;
  shown?: boolean;
  error?: string;
}

interface BackendProcess {
  pid?: number;
  name?: string;
  username?: string;
  cpu_percent?: number;
  memory_bytes?: number;
  status?: string;
}

interface BackendProcessesResponse {
  processes: BackendProcess[];
  count?: number;
}

interface BackendStartupItem {
  name?: string;
  path?: string;
  command?: string;
  source?: string;
}

interface BackendStartupResponse {
  startup_items: BackendStartupItem[];
  count?: number;
}

interface BackendDisk {
  device?: string;
  mountpoint?: string;
  fstype?: string;
  usage?: {
    total?: number;
    used?: number;
    free?: number;
    percent?: number;
  };
}

interface BackendSystemDiagnostics {
  info?: Record<string, unknown>;
  disks?: BackendDisk[];
  network?: Record<string, unknown>;
  battery?: Record<string, unknown> | null;
  top_processes?: BackendProcess[];
  suggestions?: string[];
  product?: {
    name?: string;
    version?: string;
  };
  update_channel?: {
    configured?: boolean;
    status?: string;
    label?: string;
    detail?: string;
    check_action?: string;
    offline_only?: boolean;
  };
  local_paths?: {
    data_dir?: string;
    database?: string;
    log_dirs?: string[];
  };
  audit?: {
    verification?: Record<string, unknown>;
    latest_event?: Record<string, unknown> | null;
  };
  lan_transport?: Record<string, unknown>;
  recent_counts?: Record<string, unknown>;
  recent_failure_counts?: Record<string, unknown>;
  diagnostic_hints?: string[];
  diagnostic_scope?: string;
}

interface BackendDiagnosticExportResult {
  ok?: boolean;
  path?: string;
  filename?: string;
  created_at?: string;
  bytes?: number;
  scope?: string;
  error?: string;
}

interface BackendBrowserLink {
  title?: string;
  url?: string;
}

interface BackendBrowserPage {
  ok?: boolean;
  url?: string;
  title?: string;
  text?: string;
  links?: BackendBrowserLink[];
  truncated?: boolean;
  adapter?: string;
  error?: string;
}

interface BackendBrowserSession {
  id?: string;
  task_id?: string | null;
  current_url?: string;
  url?: string;
  title?: string;
  status?: string;
  mode?: string;
  created_at?: string;
  updated_at?: string;
  paused?: boolean;
  takeover?: boolean;
  last_observation?: string | Record<string, unknown> | null;
}

interface BackendBrowserActivityEvent {
  id?: string;
  session_id?: string;
  task_id?: string | null;
  step_id?: string | null;
  type?: string;
  action?: unknown;
  url?: string;
  title?: string;
  risk_level?: string;
  verdict?: string;
  ok?: boolean;
  error?: string;
  screenshot_url?: string;
  created_at?: string;
}

interface BackendBrowserActivityEnvelope extends BackendBrowserActivityEvent {
  ok?: boolean;
  event?: BackendBrowserActivityEvent;
  session?: BackendBrowserSession;
}

interface BackendBrowserSessions {
  ok?: boolean;
  sessions?: BackendBrowserSession[];
  error?: string;
}

interface BackendBrowserEvents {
  ok?: boolean;
  events?: BackendBrowserActivityEvent[];
  error?: string;
}

interface BackendBrowserReplayExport {
  ok?: boolean;
  url?: string;
  path?: string;
  events?: BackendBrowserActivityEvent[];
  session?: BackendBrowserSession;
  error?: string;
}

export type BackendBrowserSessionStreamEvent =
  | { type: "connected"; session_id: string }
  | { type: "heartbeat"; session_id?: string }
  | { type: "session"; session: BackendBrowserSession }
  | { type: "event"; event: BackendBrowserActivityEvent }
  | BackendBrowserActivityEvent;

export interface BrowserReplayExport {
  ok?: boolean;
  url?: string;
  path?: string;
  events?: BrowserActivityEvent[];
  session?: BrowserSession;
  error?: string;
}

interface BackendBrowserLinks {
  ok?: boolean;
  url?: string;
  title?: string;
  links: BackendBrowserLink[];
  error?: string;
}

interface BackendSkillTool {
  name?: string;
  description?: string;
  agent_owner?: string;
  risk?: string;
  permissions?: unknown[];
  input_schema?: unknown;
  execution_type?: string;
  entry?: string;
  supports_dry_run?: boolean;
  requires_authorized_path?: boolean;
  rollback_hint?: string;
}

interface BackendSkillSafetyIssue {
  severity?: string;
  location?: string;
  message?: string;
}

interface BackendInstalledSkill {
  name?: string;
  version?: string;
  agent_owner?: string;
  risk?: string;
  root?: string;
  manifest_path?: string;
  status?: string;
  tools?: BackendSkillTool[];
  safety?: {
    ok?: boolean;
    issues?: BackendSkillSafetyIssue[];
  };
  error?: string;
}

interface BackendSkillsCatalog {
  skills?: BackendInstalledSkill[];
  count?: number;
  directories?: string[];
  install_directory?: string;
}

interface BackendSkillImportResult {
  skill: BackendInstalledSkill;
  refresh?: BackendSkillRefresh;
}

interface BackendSkillRefresh {
  ok?: boolean;
  tool_count?: number;
  skill_count?: number;
}

interface BackendCommandInfo {
  name?: string;
  title?: string;
  description?: string;
  category?: string;
  input_schema?: unknown;
}

interface BackendCommandsResponse {
  commands?: BackendCommandInfo[];
  count?: number;
}

interface BackendCommandExecutionResult {
  ok?: boolean;
  command?: string;
  title?: string;
  result?: unknown;
  diagnostics?: unknown[];
  error?: string;
  next_action?: string;
}
