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
  ChatMessage,
  ChatRequest,
  ChatResponse,
  FileSearchResult,
  InstalledApp,
  InstalledSkill,
  LocalLLMHealth,
  Plan,
  SafetyReview,
  SkillImportResult,
  SkillsCatalog,
  StartupItem,
  SystemDiagnostic,
  SystemInfo,
  SystemProcess,
  TaskEvent
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
        mode: body.mode ?? "privacy"
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

  getCurrentPlan(): Promise<ApiResponse<Plan>> {
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

  listAgentConversations(): Promise<ApiResponse<AgentConversation[]>> {
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

  saveSettings(settings: AppSettings): Promise<ApiResponse<AppSettings>> {
    return this.request<BackendSettings, Partial<BackendSettings>>({
      endpoint: "/api/settings",
      method: "POST",
      body: {
        base_url: settings.apiBaseUrl,
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

  clusterFiles(options: { k?: number } = {}): Promise<ApiResponse<BackendClusterResponse>> {
    return this.request<BackendClusterResponse, { k?: number }>({
      endpoint: "/api/files/cluster",
      method: "POST",
      body: options.k ? { k: options.k } : {},
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
  if (status === "waiting_user_approval" || status === "paused") return "blocked";
  return "running";
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
  const rawMode = (settings.mode ?? "privacy").toLowerCase();
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
  cluster_id: number;
  size: number;
  preview: string[];
  suggested_name?: string;
}

export interface BackendClusterResponse {
  ok: boolean;
  clusters: BackendClusterEntry[];
  count?: number;
  error?: string;
}

interface BackendSettings {
  base_url?: string;
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
