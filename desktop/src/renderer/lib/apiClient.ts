import type {
  AgentConversation,
  ApiRequest,
  ApiResponse,
  AppSettings,
  ApprovalDecision,
  ApprovalRequest,
  AuditLogEntry,
  BackendStatus,
  BrowserLinkResult,
  BrowserPageSnapshot,
  CommandExecutionResult,
  CommandInfo,
  ChatMessage,
  ChatRequest,
  ChatResponse,
  ContextUsage,
  FileSearchResult,
  InstalledApp,
  InstalledSkill,
  IntentSuggestion,
  LLMCostSummary,
  LLMHealthStatus,
  LLMProfile,
  LocalLLMHealth,
  Plan,
  SafetyReview,
  SkillImportResult,
  SkillsCatalog,
  StartupItem,
  SystemDiagnostic,
  SystemInfo,
  SystemProcess,
  TaskEvent,
  RunEventPayload,
  TaskExplain,
  TaskExplainChainItem,
  TaskExplainEvidence,
  TaskExplainMessage,
  TaskExplainReview,
  TaskExplainStep
} from "../../shared/types";
import {
  zhApprovalType,
  zhBackendTaskStatus,
  zhBackendText,
  zhRiskLevel,
  zhSafetyVerdict,
  zhToolName
} from "./zh";

const FALLBACK_BACKEND_URL = "http://127.0.0.1:8000";
const DEFAULT_TIMEOUT_MS = 30_000;
const WS_RETRY_DELAY_MS = 2_500;

export class MavrisApiClient {
  async request<TResponse, TBody = unknown>(request: ApiRequest<TBody>): Promise<ApiResponse<TResponse>> {
    if (!window.mavris) {
      return requestBackendDirect<TResponse, TBody>(FALLBACK_BACKEND_URL, request);
    }

    return window.mavris.api.request<TResponse, TBody>(request);
  }

  async getBackendStatus(): Promise<BackendStatus> {
    if (!window.mavris) {
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
    return window.mavris.backend.getStatus();
  }

  startBackend(): Promise<BackendStatus> {
    if (!window.mavris) {
      return this.getBackendStatus();
    }
    return window.mavris.backend.start();
  }

  stopBackend(): Promise<BackendStatus> {
    if (!window.mavris) {
      return this.getBackendStatus();
    }
    return window.mavris.backend.stop();
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
          content: data.task_id && data.status
            ? `${zhBackendText(data.message)} 状态：${zhBackendTaskStatus(data.status)}。`
            : zhBackendText(data.message),
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

  startRun(body: ChatRequest): Promise<ApiResponse<ChatResponse>> {
    return this.request<BackendRunCreateResponse, BackendRunCreateRequest>({
      endpoint: "/api/runs",
      method: "POST",
      body: {
        message: body.content,
        mode: body.mode ?? "efficiency",
        engine: "auto"
      }
    }).then((response) =>
      mapResponse(response, (data) => ({
        runId: data.run_id,
        engine: data.engine,
        message: {
          id: `${data.run_id}-run-started`,
          role: "assistant" as const,
          author: "Marvis",
          content: `Run ${data.engine} started: ${zhBackendTaskStatus(data.phase)}.`,
          createdAt: new Date().toISOString(),
          status: "sent" as const
        },
        taskUpdates: [
          {
            id: data.run_id,
            title: body.content,
            description: `Run status: ${zhBackendTaskStatus(data.phase)}`,
            state: mapTaskState(data.phase),
            agent: data.engine === "developer" ? "DeveloperExecutionEngine" : "OSExecutionEngine",
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
    const tasks = await Promise.all(response.data.map((task) => this.mapTaskEventWithRecordings(task)));
    return {
      ok: true,
      status: response.status,
      data: tasks,
      receivedAt: response.receivedAt
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
        const plannerMessage = data.messages.find((message) => agentNameFor(message) === "PlannerAgent");
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
            owner: step.agent_name
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
    handlers: {
      onMessage: (message: BackendTaskStreamEvent) => void;
      onError?: (error: Event) => void;
      onOpen?: () => void;
    }
  ): () => void {
    if (!taskId || typeof WebSocket === "undefined") {
      return () => undefined;
    }

    let socket: WebSocket | null = null;
    let closedByCaller = false;
    let retryId: number | undefined;

    const connect = () => {
      socket = new WebSocket(buildTaskWebSocketUrl(getBackendBaseUrl(), taskId));

      socket.onopen = () => handlers.onOpen?.();
      socket.onmessage = (event) => {
        try {
          handlers.onMessage(JSON.parse(String(event.data)) as BackendTaskStreamEvent);
        } catch {
          // Ignore malformed stream events and keep the polling fallback alive.
        }
      };
      socket.onerror = (event) => {
        handlers.onError?.(event);
      };
      socket.onclose = () => {
        socket = null;
        if (!closedByCaller) {
          retryId = window.setTimeout(connect, WS_RETRY_DELAY_MS);
        }
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
    return this.request<BackendCommandExecutionResult, { name: string; args: Record<string, unknown> }>({
      endpoint: "/api/commands/execute",
      method: "POST",
      body: { name, args }
    }).then((response) => mapResponse(response, mapCommandExecutionResult));
  }

  createMobilePairingCode(): Promise<ApiResponse<MobilePairingCode>> {
    return this.request<MobilePairingCode>({
      endpoint: "/api/pair/request",
      method: "POST"
    });
  }

  listMobileDevices(): Promise<ApiResponse<MobileDeviceList>> {
    return this.request<MobileDeviceList>({
      endpoint: "/api/pair/devices"
    });
  }

  searchFiles(query: string): Promise<ApiResponse<FileSearchResult[]>> {
    return this.request<BackendFileSearchResponse>({
      endpoint: "/api/files/search",
      query: { q: query },
      timeoutMs: 10_000
    }).then((response) =>
      mapResponse(response, (data) => [
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
      ])
    );
  }

  getSettings(): Promise<ApiResponse<AppSettings>> {
    return this.request<BackendSettings>({ endpoint: "/api/settings" }).then((response) =>
      mapResponse(response, mapSettings)
    );
  }

  getLocalLlmHealth(): Promise<ApiResponse<LocalLLMHealth>> {
    return this.request<BackendLocalLlmHealth>({
      endpoint: "/api/settings/local-llm/health",
      timeoutMs: 2500
    }).then((response) => mapResponse(response, mapLocalLlmHealth));
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

  getContextUsage(taskId?: string): Promise<ApiResponse<ContextUsage>> {
    return this.request<BackendContextUsage>({
      endpoint: "/api/context/usage",
      query: taskId ? { task_id: taskId } : undefined,
      timeoutMs: 2500
    }).then((response) => mapResponse(response, mapContextUsage));
  }

  saveSettings(settings: AppSettings): Promise<ApiResponse<AppSettings>> {
    return this.request<BackendSettings, Partial<BackendSettings>>({
      endpoint: "/api/settings",
      method: "POST",
      body: {
        provider_name: settings.providerName,
        base_url: settings.apiBaseUrl,
        model: settings.model,
        review_model: settings.reviewModel,
        wire_api: settings.wireApi,
        requires_openai_auth: settings.requiresOpenAiAuth,
        model_reasoning_effort: settings.modelReasoningEffort,
        disable_response_storage: settings.disableResponseStorage,
        temperature: settings.temperature,
        max_tokens: settings.maxTokens,
        timeout: settings.timeout,
        llm_api_max_retries: settings.llmApiMaxRetries,
        llm_api_retry_backoff_seconds: settings.llmApiRetryBackoffSeconds,
        llm_api_circuit_failure_threshold: settings.llmApiCircuitFailureThreshold,
        llm_api_circuit_cooldown_seconds: settings.llmApiCircuitCooldownSeconds,
        model_context_window: settings.modelContextWindow,
        model_auto_compact_token_limit: settings.modelAutoCompactTokenLimit,
        allowed_directories: settings.workspaceRoot ? [settings.workspaceRoot] : [],
        allow_browser_network: settings.allowBrowserNetwork,
        remote_desktop_enabled: settings.remoteDesktopEnabled,
        app_allowlist: settings.appAllowlist,
        browser_max_page_bytes: settings.browserMaxPageBytes,
        browser_screenshot_dir: settings.browserScreenshotDir,
        onnx_model_path: settings.onnxModelPath,
        onnx_execution_provider: settings.onnxExecutionProvider,
        mode: settings.mode,
        allow_cloud_context: settings.allowCloudContext,
        allow_file_content_upload: settings.allowFileContentUpload,
        mcp_servers: settings.mcpServers
          .filter((server) => server.url.trim() && server.name.trim())
          .map((server) => ({
            name: server.name.trim(),
            url: server.url.trim(),
            enabled: server.enabled,
            transport: "http"
          }))
      }
    }).then((response) => mapResponse(response, mapSettings));
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
        appVersion: window.mavris?.versions.app ?? "0.1.0",
        electronVersion: window.mavris?.versions.electron ?? "未知",
        chromeVersion: window.mavris?.versions.chrome ?? "未知",
        nodeVersion: window.mavris?.versions.node ?? "未知",
        platform: info.system ?? info.platform ?? window.mavris?.platform ?? "未知",
        arch: info.machine ?? "未知",
        backendBaseUrl: "http://127.0.0.1:8000",
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

  listApps(): Promise<ApiResponse<InstalledApp[]>> {
    return this.request<BackendAppsResponse>({ endpoint: "/api/apps" }).then((response) =>
      mapResponse(response, (data) => data.apps.map(mapInstalledApp))
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
    handlers: {
      onMessage: (message: BackendRunStreamEvent) => void;
      onError?: (error: Event) => void;
      onOpen?: () => void;
    }
  ): () => void {
    if (!runId || typeof WebSocket === "undefined") {
      return () => undefined;
    }

    let socket: WebSocket | null = null;
    let closedByCaller = false;
    let retryId: number | undefined;

    const connect = () => {
      socket = new WebSocket(buildRunWebSocketUrl(getBackendBaseUrl(), runId));

      socket.onopen = () => handlers.onOpen?.();
      socket.onmessage = (event) => {
        try {
          handlers.onMessage(JSON.parse(String(event.data)) as BackendRunStreamEvent);
        } catch {
          // Ignore malformed stream events and keep the polling fallback alive.
        }
      };
      socket.onerror = (event) => {
        handlers.onError?.(event);
      };
      socket.onclose = () => {
        socket = null;
        if (!closedByCaller) {
          retryId = window.setTimeout(connect, WS_RETRY_DELAY_MS);
        }
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
    return {
      ...base,
      recordings: mapTaskRecordings(timeline.data)
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
    return this.request<BackendSkillImportResult, { path: string }>({
      endpoint: "/api/skills/import",
      method: "POST",
      body: { path },
      timeoutMs: 30_000
    }).then((response) => mapResponse(response, mapSkillImportResult));
  }

  refreshSkills(): Promise<ApiResponse<{ ok: boolean; toolCount: number; skillCount: number }>> {
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
          message: getErrorMessage(data, response.statusText),
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
        message: error instanceof Error ? error.message : "Backend request failed"
      },
      receivedAt
    };
  }
}

function buildRequestUrl(baseUrl: string, request: ApiRequest): URL {
  if (/^https?:\/\//i.test(request.endpoint)) {
    throw new Error("Renderer API requests must use backend-relative endpoints");
  }

  const normalizedEndpoint = request.endpoint.startsWith("/") ? request.endpoint : `/${request.endpoint}`;
  const url = new URL(normalizedEndpoint, baseUrl);
  for (const [key, value] of Object.entries(request.query ?? {})) {
    if (value !== null && value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }
  return url;
}

function getBackendBaseUrl(): string {
  return window.mavris?.backendBaseUrl ?? FALLBACK_BACKEND_URL;
}

function buildTaskWebSocketUrl(baseUrl: string, taskId: string): string {
  const url = new URL(`/ws/tasks/${encodeURIComponent(taskId)}`, baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

function buildRunWebSocketUrl(baseUrl: string, runId: string): string {
  const url = new URL(`/ws/runs/${encodeURIComponent(runId)}`, baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
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

function mapTaskState(status: string): TaskEvent["state"] {
  if (status === "completed") return "completed";
  if (status === "failed" || status === "denied" || status === "cancelled") return "failed";
  if (status === "waiting_user_approval" || status === "awaiting_approval" || status === "paused") return "blocked";
  return "running";
}

function mapRunTaskEvent(run: BackendRunState): TaskEvent {
  return {
    id: run.run_id,
    title: run.message || run.run_id,
    description: run.error || `Run status: ${zhBackendTaskStatus(run.phase)} (${run.engine})`,
    state: mapTaskState(run.phase),
    agent: run.engine === "developer" ? "DeveloperExecutionEngine" : "OSExecutionEngine",
    createdAt: run.created_at || new Date().toISOString(),
    updatedAt: run.updated_at || run.created_at || new Date().toISOString(),
    recordings: []
  };
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
      objective: run.error || `Run status: ${zhBackendTaskStatus(run.phase)}`,
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
      owner: step.agent_name
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
      const agent = String(payload.from_agent ?? (run.engine === "developer" ? "DeveloperExecutionEngine" : "OSExecutionEngine"));
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

function mapTaskEvent(task: BackendTask): TaskEvent {
  return {
    id: task.id,
    title: task.user_goal,
    description: zhBackendText(task.final_summary) || `当前后端状态：${zhBackendTaskStatus(task.status)}`,
    state: mapTaskState(task.status),
    agent: "调度 Agent",
    createdAt: task.created_at,
    updatedAt: task.updated_at,
    recordings: []
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

function absoluteBackendUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return new URL(path, getBackendBaseUrl()).toString();
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
  return {
    id: approval.id,
    title: zhApprovalType(approval.approval_type),
    reason: zhBackendText(approval.message),
    requester: "HumanGateAgent",
    riskLevel: "medium",
    createdAt: approval.created_at,
    proposedAction: formatDiffPreview(approval.diff_preview),
    status: approval.status === "rejected" ? "denied" : approval.status === "approved" ? "approved" : "pending"
  };
}

function mapSettings(settings: BackendSettings): AppSettings {
  const rawMode = (settings.mode ?? "efficiency").toLowerCase();
  const mode: AppSettings["mode"] = rawMode === "efficiency" || rawMode === "hybrid" ? rawMode : "privacy";
  const mcpServers = (settings.mcp_servers ?? [])
    .map((server) => ({
      name: String(server?.name ?? "").trim(),
      url: String(server?.url ?? "").trim(),
      enabled: server?.enabled !== false
    }))
    .filter((server) => server.url.length > 0);
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
    workspaceRoot: settings.allowed_directories?.[0] ?? "",
    allowBrowserNetwork: Boolean(settings.allow_browser_network),
    remoteDesktopEnabled: Boolean(settings.remote_desktop_enabled),
    appAllowlist: settings.app_allowlist ?? [],
    browserMaxPageBytes: settings.browser_max_page_bytes ?? 250000,
    browserScreenshotDir: settings.browser_screenshot_dir ?? "",
    onnxModelPath: settings.onnx_model_path ?? "",
    onnxExecutionProvider: settings.onnx_execution_provider ?? "",
    mode,
    allowCloudContext: Boolean(settings.allow_cloud_context),
    allowFileContentUpload: Boolean(settings.allow_file_content_upload),
    mcpServers
  };
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
    error: typeof health.error === "string" ? health.error : ""
  };
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
      executionType: String(tool.execution_type ?? ""),
      entry: String(tool.entry ?? "")
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
    createdAt: message.created_at,
    status: message.status === "failed" ? "failed" : "sent"
  };
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
    suggestions: (data.suggestions ?? []).map(zhBackendText)
  };
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
  created_at: string;
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
  mode: string;
  engine: "auto" | "os" | "developer";
}

interface BackendRunCreateResponse {
  run_id: string;
  engine: "os" | "developer";
  phase: string;
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
}

interface BackendTimeline {
  task: string;
  messages: BackendAgentMessage[];
  reviews: BackendSafetyReview[];
  recordings?: BackendStepRecording[];
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

export interface BackendTaskStreamEvent {
  type: "connected" | "heartbeat" | "agent_message";
  task_id: string;
  message?: BackendAgentMessage;
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
  approval_type: string;
  message: string;
  diff_preview: unknown;
  status: string;
  created_at: string;
}

export interface MobilePairingCode {
  code: string;
  expires_at: string;
  expires_in: number;
  server: {
    host: string;
    port: number;
  };
}

export interface MobileDevice {
  device_id: string;
  device_name: string;
  created_at: string;
  updated_at: string;
}

export interface MobileDeviceList {
  devices: MobileDevice[];
}

interface BackendFileSearchResponse {
  index_results?: Array<{ file_id?: string; path: string; snippet?: string }>;
  name_results?: Array<{ path: string; name?: string }>;
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
  mode?: string;
  allow_cloud_context?: boolean;
  allow_file_content_upload?: boolean;
  mcp_servers?: Array<{ name?: string; url?: string; enabled?: boolean; transport?: string }>;
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

interface BackendLocalLlmHealth {
  available?: boolean;
  selected_backend?: BackendLocalLlmBackend | null;
  probe_order?: string[];
  error?: string;
  kind?: string;
  base_url?: string;
  models?: string[];
  model?: string;
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
  input_schema?: unknown;
  execution_type?: string;
  entry?: string;
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
