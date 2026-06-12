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
  zhAgentName,
  zhApprovalType,
  zhBackendTaskStatus,
  zhBackendText,
  zhRiskLevel,
  zhSafetyVerdict,
  zhToolName,
  zhUserFacingError
} from "../zh";
import { absoluteRendererLoopbackBackendUrl, getBackendBaseUrl } from "./transport";
import type { LocalModelInstallRequest } from "./transport";
import type { BackendAgentMessage, BackendApproval, BackendBrowserActivityEnvelope, BackendBrowserActivityEvent, BackendBrowserLink, BackendBrowserPage, BackendBrowserReplayExport, BackendBrowserSession, BackendChatMessage, BackendCleanupExecutionResult, BackendCleanupItem, BackendCleanupPlan, BackendCleanupScanRequest, BackendCommandExecutionResult, BackendCommandInfo, BackendContextUsage, BackendDiagnosticExportResult, BackendDocumentAskResponse, BackendDocumentBlock, BackendDocumentCitation, BackendDocumentCompareResponse, BackendDocumentIR, BackendDocumentTable, BackendFileRevealResult, BackendHardwareAccelerationComponentStatus, BackendHardwareAccelerationSmoke, BackendHardwareAccelerationStatus, BackendIndexStatus, BackendInstalledApp, BackendInstalledSkill, BackendIntentSuggestion, BackendLlmCostSummary, BackendLlmHealth, BackendLlmProfile, BackendLocalLibraryItem, BackendLocalLibraryResponse, BackendLocalLlmHealth, BackendLocalModelBundleManifest, BackendLocalModelEvidenceItem, BackendLocalModelReadiness, BackendLocalModelRepairAction, BackendLocalModelSetupPlan, BackendLocalModelVerification, BackendPlan, BackendProcess, BackendRunCreateResponse, BackendRunEvent, BackendRunState, BackendRunTimeline, BackendSettings, BackendSkillImportResult, BackendSkillsCatalog, BackendStartupItem, BackendStepRecordingFrame, BackendStepRecordingPayload, BackendSuggestionLaunchResponse, BackendSupportPackageRedaction, BackendSystemDiagnostics, BackendTask, BackendTaskCompletionEvidenceFallback, BackendTaskExplain, BackendTaskExplainChainItem, BackendTaskExplainEvidence, BackendTaskExplainMessage, BackendTaskExplainReview, BackendTaskExplainStep, BackendTimeline, BrowserReplayExport } from "./backendTypes";

export function compactLocalModelRequest(request: LocalModelInstallRequest): LocalModelInstallRequest {
  const model = String(request.model ?? "").trim();
  return model ? { model } : {};
}

export function mapTaskState(status: string): TaskEvent["state"] {
  if (status === "completed") return "completed";
  if (status === "failed" || status === "denied" || status === "cancelled") return "failed";
  if (status === "paused") return "paused";
  if (status === "waiting_user_approval" || status === "awaiting_approval") return "blocked";
  return "running";
}

export function mapRunCreateResponse(data: BackendRunCreateResponse | BackendSuggestionLaunchResponse, fallbackTitle: string): PerceptionSuggestionLaunchResponse {
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
        agent: runEngineAgentName(engine, data.engine_capabilities),
        createdAt: run?.created_at ?? new Date().toISOString(),
        updatedAt: run?.updated_at ?? run?.created_at ?? new Date().toISOString()
      }
    ]
  };
}

export function mapRunTaskEvent(run: BackendRunState): TaskEvent {
  const cleanupPlan = cleanupPlanFromApprovalPayload(run.cleanup_plan ?? run.cleanupPlan ?? run.diff_preview);
  return {
    id: run.run_id,
    runId: run.run_id,
    title: run.message || run.run_id,
    description: runDescription(run),
    state: mapTaskState(run.phase),
    agent: runEngineAgentName(run.engine, run.engine_capabilities),
    createdAt: run.created_at || new Date().toISOString(),
    updatedAt: run.updated_at || run.created_at || new Date().toISOString(),
    recordings: [],
    cleanupPlan,
    completionEvidence: mapOptionalTaskCompletionEvidence(run.completion_evidence, {
      resultVerified: run.result_verified,
      completedResult: run.completed_result
    })
  };
}

export function mapBoundaryEvents(value: unknown): TaskBoundaryEvent[] {
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

export function zhRunEngine(engine?: string): string {
  if (engine === "developer") return "开发执行";
  if (engine === "os") return "电脑执行";
  if (engine === "auto") return "自动选择";
  return engine || "未知执行";
}

export function runEngineAgentName(
  engine?: string,
  capabilities?: { writes_enabled?: boolean; mode?: string; supervisor_agent_hint?: string }
): string {
  const hint = capabilities?.supervisor_agent_hint?.trim();
  if (hint) {
    return zhAgentName(hint);
  }
  if (engine === "developer") {
    return capabilities?.writes_enabled ? "开发执行引擎" : "开发引擎（只读）";
  }
  if (engine === "os") return "电脑执行引擎";
  return "执行引擎";
}

function runDescription(run: BackendRunState): string {
  const status = zhBackendText(run.error) || `状态：${zhBackendTaskStatus(run.phase)}（${zhRunEngine(run.engine)}）`;
  const disclosure = run.engine_capabilities?.disclosure;
  if (disclosure && run.engine === "developer" && run.engine_capabilities?.writes_enabled === false) {
    return `${status} · ${zhBackendText(disclosure)}`;
  }
  return status;
}

export function latestRunState(runs: BackendRunState[]): BackendRunState | null {
  return [...runs].sort((left, right) => {
    const leftTime = Date.parse(left.updated_at || left.created_at || "");
    const rightTime = Date.parse(right.updated_at || right.created_at || "");
    return (Number.isNaN(rightTime) ? 0 : rightTime) - (Number.isNaN(leftTime) ? 0 : leftTime);
  })[0] ?? null;
}

export function hasRunTimelineEvents(timeline: BackendRunTimeline): boolean {
  return Boolean(timeline.events?.length);
}

export function mapRunPlan(run: BackendRunState, timeline: BackendRunTimeline): Plan {
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

export function mapRunConversation(run: BackendRunState, events: BackendRunEvent[]): AgentConversation {
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

export function mapRunEventKind(name: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (name === "tool.result" || name === "run.completed") return "result";
  if (name === "approval.needed" || name === "run.waiting_approval") return "handoff";
  if (name === "tool.progress") return "observation";
  return "action";
}

export function mapCommandInfo(command: BackendCommandInfo): CommandInfo {
  return {
    name: String(command.name ?? ""),
    title: String(command.title ?? command.name ?? ""),
    description: String(command.description ?? ""),
    category: String(command.category ?? ""),
    inputSchema: (command.input_schema && typeof command.input_schema === "object" ? command.input_schema : {}) as Record<string, unknown>
  };
}

export function mapCommandExecutionResult(result: BackendCommandExecutionResult): CommandExecutionResult {
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

export function mapIndexStatus(status?: BackendIndexStatus | null): IndexStatus | undefined {
  if (!status) return undefined;
  const failure = status.latest_failure;
  return {
    status: String(status.status ?? "empty"),
    filesIndexed: numberOrZero(status.files_indexed),
    chunksIndexed: numberOrZero(status.chunks_indexed),
    embeddingsIndexed: numberOrZero(status.embeddings_indexed),
    bytesIndexed: numberOrZero(status.bytes_indexed),
    lastIndexedAt: String(status.last_indexed_at ?? ""),
    lastModifiedAt: String(status.last_modified_at ?? ""),
    retryHint: String(status.retry_hint ?? ""),
    latestFailure: failure
      ? {
          at: String(failure.at ?? ""),
          pathLabel: String(failure.path_label ?? failure.path ?? ""),
          message: String(failure.message ?? "")
        }
      : null
  };
}

export function mapLocalLibraryResponse(data: BackendLocalLibraryResponse): LocalLibraryResponse {
  const rootCount = numberOrZero(data.scope_summary?.root_count ?? data.roots?.length ?? 0);
  return {
    section: String(data.section ?? "gallery"),
    roots: data.roots ?? [],
    scopeSummary: {
      rootCount,
      rootLabels: (data.scope_summary?.root_labels ?? []).map(String),
      hasAuthorizedRoots: Boolean(data.scope_summary?.has_authorized_roots ?? rootCount > 0),
      displayLabel: String(data.scope_summary?.display_label ?? (rootCount ? `${rootCount} 个授权范围` : "未选择授权目录")),
      rawPathsAvailableForLocalActions: Boolean(data.scope_summary?.raw_paths_available_for_local_actions ?? true),
      shareableSummaryHasRawPaths: Boolean(data.scope_summary?.shareable_summary_has_raw_paths ?? false)
    },
    items: (data.items ?? []).map(mapLocalLibraryItem),
    count: Number(data.count ?? data.items?.length ?? 0),
    total: Number(data.total ?? data.items?.length ?? 0),
    scanned: Number(data.scanned ?? 0),
    truncated: Boolean(data.truncated),
    stats: {
      size: Number(data.stats?.size ?? 0),
      byExtension: data.stats?.by_extension ?? {}
    },
    indexStatus: mapIndexStatus(data.index_status)
  };
}

export function mapLocalLibraryItem(item: BackendLocalLibraryItem): LocalLibraryItem {
  return {
    id: String(item.id ?? item.path),
    path: String(item.path ?? ""),
    pathLabel: String(item.path_label ?? item.name ?? ""),
    name: String(item.name ?? item.path ?? ""),
    parent: String(item.parent ?? ""),
    parentLabel: String(item.parent_label ?? ""),
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

export function mapDocumentIR(data: BackendDocumentIR): DocumentIR {
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

export function mapDocumentBlock(block: BackendDocumentBlock): DocumentIR["blocks"][number] {
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

export function mapDocumentTable(table: BackendDocumentTable): DocumentTable {
  return {
    id: String(table.id ?? table.table_id ?? crypto.randomUUID()),
    title: optionalString(table.title ?? table.name),
    columns: stringArray(table.columns),
    rows: tableRowsFromUnknown(table.rows),
    page: numberOrUndefined(table.page),
    sourceBlockId: optionalString(table.source_block_id ?? table.sourceBlockId)
  };
}

export function mapDocumentCitation(citation: BackendDocumentCitation, index = 0): DocumentCitation {
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

export function mapDocumentAskResponse(data: BackendDocumentAskResponse): DocumentAskResponse {
  const sourceChunks = (data.source_chunks ?? data.sources ?? []).map(mapDocumentCitation);
  const citationItems = arrayOfObjects(data.citation_items ?? data.citations_detail ?? data.citations);
  return {
    answer: String(data.answer ?? data.summary ?? ""),
    citations: citationItems.length ? citationItems.map(mapDocumentCitation) : sourceChunks,
    sourceChunks,
    note: optionalString(data.note)
  };
}

export function mapDocumentCompareResponse(data: BackendDocumentCompareResponse): DocumentCompareResponse {
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

export function cleanupScanRequestFor(body: CleanupScanRequest): BackendCleanupScanRequest {
  return {
    roots: body.roots,
    threshold_mb: body.thresholdMb,
    include_caches: body.includeCaches
  };
}

export function mapCleanupPlan(input: BackendCleanupPlan): CleanupPlan {
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

export function normalizeCleanupPlan(input: BackendCleanupPlan): BackendCleanupPlan {
  if (input && typeof input === "object" && input.cleanup_plan && typeof input.cleanup_plan === "object") {
    return input.cleanup_plan as BackendCleanupPlan;
  }
  if (input && typeof input === "object" && input.plan && typeof input.plan === "object") {
    return input.plan as BackendCleanupPlan;
  }
  return input;
}

export function cleanupItemsForPlan(plan: BackendCleanupPlan): CleanupItem[] {
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

export function mapCleanupItem(item: BackendCleanupItem, fallbackBucket: string): CleanupItem {
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

export function cleanupDispositionFor(item: BackendCleanupItem, bucket: string, action: string): CleanupItem["disposition"] {
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

export function mapCleanupExecutionResult(result: BackendCleanupExecutionResult): CleanupExecutionResult {
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

export function mapTaskEvent(task: BackendTask): TaskEvent {
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
    boundaryEvents: mapBoundaryEvents(task.boundary_events),
    completionEvidence: mapOptionalTaskCompletionEvidence(task.completion_evidence, {
      resultVerified: task.result_verified,
      completedResult: task.completed_result
    })
  };
}

export function mapTaskRecordings(timeline: BackendTimeline): NonNullable<TaskEvent["recordings"]> {
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

export function cleanupPlanFromTimeline(timeline: BackendTimeline): CleanupPlan | undefined {
  const direct = cleanupPlanFromApprovalPayload(timeline.cleanup_plan ?? timeline.cleanupPlan);
  if (direct) return direct;

  for (const message of timeline.messages) {
    const payload = metadataPayloadFor<unknown>(message);
    const plan = cleanupPlanFromApprovalPayload(payload);
    if (plan) return plan;
  }
  return undefined;
}

export function mapTaskExplain(data: BackendTaskExplain): TaskExplain {
  const finalResult = data.final_result ?? {};
  const completionEvidence = mapTaskCompletionEvidence(finalResult.completion_evidence ?? data.completion_evidence, {
    resultVerified: finalResult.result_verified ?? data.result_verified,
    completedResult: finalResult.completed_result ?? data.completed_result,
    evidenceKind: finalResult.evidence_kind ?? data.evidence_kind
  });
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
    completionEvidence,
    finalResult: {
      status: String(finalResult.status ?? ""),
      summary: zhBackendText(String(finalResult.summary ?? "")),
      safetyReviews: (finalResult.safety_reviews ?? []).map(mapExplainReview),
      evidence: (finalResult.evidence ?? []).map(mapExplainEvidence),
      completionEvidence
    },
    chain: (data.chain ?? []).map(mapExplainChainItem)
  };
}

export function mapExplainStep(step: BackendTaskExplainStep): TaskExplainStep {
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

export function mapExplainReview(review: BackendTaskExplainReview): TaskExplainReview {
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

export function mapExplainMessage(message: BackendTaskExplainMessage): TaskExplainMessage {
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

export function mapExplainChainItem(item: BackendTaskExplainChainItem): TaskExplainChainItem {
  return {
    stage: String(item.stage ?? ""),
    title: String(item.title ?? ""),
    summary: zhBackendText(String(item.summary ?? "")),
    evidence: (item.evidence ?? []).map(mapExplainEvidence)
  };
}

export function mapExplainEvidence(item: BackendTaskExplainEvidence): TaskExplainEvidence {
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

export function mapOptionalTaskCompletionEvidence(
  value: unknown,
  fallback: BackendTaskCompletionEvidenceFallback = {}
): TaskCompletionEvidence | undefined {
  if (!hasTaskCompletionEvidenceInput(value, fallback)) return undefined;
  return mapTaskCompletionEvidence(value, fallback);
}

export function mapTaskCompletionEvidence(
  value: unknown,
  fallback: BackendTaskCompletionEvidenceFallback = {}
): TaskCompletionEvidence {
  const record = recordOrUndefined(value);
  const evidenceKind = firstNonEmptyString(
    record?.level,
    record?.evidence_kind,
    record?.evidenceKind,
    record?.kind,
    record?.type,
    record?.status,
    typeof value === "string" ? value : undefined,
    fallback.evidenceKind
  ) ?? "";
  const normalizedKind = normalizeCompletionEvidenceKind(evidenceKind);
  const level = taskCompletionEvidenceLevelFromValue(record?.level ?? normalizedKind);
  const resultVerified = booleanOrUndefined(record?.result_verified ?? record?.resultVerified ?? fallback.resultVerified) === true;
  const completedResult = record?.completed_result ?? record?.completedResult ?? fallback.completedResult;
  const hasCompletedResult = hasCompletedResultEvidence(completedResult);
  const status = normalizeTaskCompletionEvidenceStatus(level, normalizedKind, resultVerified, hasCompletedResult);
  const resultArtifacts = arrayOfObjects(record?.result_artifacts ?? record?.resultArtifacts).map((item) => ({
    kind: String(item.kind ?? ""),
    label: zhBackendText(String(item.label ?? "")),
    redacted: item.redacted !== false,
    count: Number.isFinite(Number(item.count)) ? Number(item.count) : undefined
  }));
  const missing = Array.isArray(record?.missing)
    ? record.missing.map((item) => zhBackendText(String(item))).filter(Boolean)
    : taskCompletionEvidenceMissing(status, resultVerified, hasCompletedResult);
  const signoff = Boolean(record?.signoff);
  return {
    level,
    status,
    evidenceKind: normalizedKind,
    resultVerified,
    resultArtifacts: resultArtifacts.length ? resultArtifacts : taskCompletionEvidenceArtifacts(status),
    missing,
    signoff,
    summary: taskCompletionEvidenceSummary(status, missing),
    privacyNote: "仅展示证据状态，不展示原始证据内容。"
  };
}

export function hasTaskCompletionEvidenceInput(value: unknown, fallback: BackendTaskCompletionEvidenceFallback): boolean {
  return Boolean(
    recordOrUndefined(value) ||
      (typeof value === "string" && value.trim()) ||
      fallback.resultVerified !== undefined ||
      fallback.completedResult !== undefined ||
      fallback.evidenceKind !== undefined
  );
}

export function taskCompletionEvidenceLevelFromValue(value: unknown): TaskCompletionEvidence["level"] {
  const kind = normalizeCompletionEvidenceKind(String(value ?? ""));
  if (kind === "completed_result" || kind === "verified_completed_result") return "completed_result";
  if (kind === "safe_failure" || kind === "failed_safely" || kind === "safe_failed") return "safe_failure";
  if (kind === "visible_progress" || kind === "progress" || kind === "tool_progress") return "visible_progress";
  if (kind === "task_created" || kind === "task_evidence" || kind === "task_evidence_only") return "task_created";
  return "submission";
}

export function normalizeTaskCompletionEvidenceStatus(
  level: TaskCompletionEvidence["level"],
  kind: string,
  resultVerified: boolean,
  hasCompletedResult: boolean
): TaskCompletionEvidence["status"] {
  if (level === "completed_result" && resultVerified) return "verified_completed_result";
  if (level === "safe_failure") return "safe_failure";
  if (level === "visible_progress") return "visible_progress";
  if (level === "submission" || level === "task_created") return "task_evidence_only";
  if (kind === "safe_failure" || kind === "failed_safely" || kind === "safe_failed") return "safe_failure";
  if (kind === "visible_progress" || kind === "progress" || kind === "tool_progress") return "visible_progress";
  if (
    kind === "task_evidence_only" ||
    kind === "task_evidence" ||
    kind === "evidence_only" ||
    kind === "submission" ||
    kind === "task_submission" ||
    kind === "command_submission" ||
    kind.includes("submission") ||
    kind.includes("task_evidence")
  ) {
    return "task_evidence_only";
  }
  if ((kind === "completed_result" || kind === "verified_completed_result" || hasCompletedResult) && resultVerified) {
    return "verified_completed_result";
  }
  return "unverified";
}

export function taskCompletionEvidenceSummary(status: TaskCompletionEvidence["status"], missing: string[] = []): string {
  switch (status) {
    case "verified_completed_result":
      return "已看到可复核的最终结果记录，系统确认它不是仅提交或过程进度。";
    case "task_evidence_only":
      return "只记录到提交或任务过程证据，不能当作最终结果。";
    case "visible_progress":
      return missing.length
        ? `能看到任务有进展，但还缺少 ${missing.slice(0, 2).join("、")}。`
        : "能看到任务有进展，但还没有最终结果验证。";
    case "safe_failure":
      return "任务安全失败，没有可验证的最终结果。";
    default:
      return "还没有可验证的最终结果证据。";
  }
}

export function taskCompletionEvidenceArtifacts(status: TaskCompletionEvidence["status"]): TaskCompletionEvidence["resultArtifacts"] {
  if (status === "verified_completed_result") {
    return [{ kind: "completed_result", label: "最终结果证据已脱敏记录", redacted: true }];
  }
  if (status === "visible_progress") {
    return [{ kind: "visible_progress", label: "可见进度证据已脱敏记录", redacted: true }];
  }
  if (status === "task_evidence_only") {
    return [{ kind: "task_evidence", label: "任务过程证据已脱敏记录", redacted: true }];
  }
  if (status === "safe_failure") {
    return [{ kind: "safe_failure", label: "安全失败记录已脱敏", redacted: true }];
  }
  return [];
}

export function taskCompletionEvidenceMissing(
  status: TaskCompletionEvidence["status"],
  resultVerified: boolean,
  hasCompletedResult: boolean
): string[] {
  if (status === "verified_completed_result") return [];
  const missing = [];
  if (!hasCompletedResult) missing.push("最终结果记录");
  if (!resultVerified) missing.push("结果复核确认");
  return missing;
}

export function normalizeCompletionEvidenceKind(value: string): string {
  return value.trim().toLowerCase().replace(/[\s.-]+/g, "_");
}

export function hasCompletedResultEvidence(value: unknown): boolean {
  if (value === undefined || value === null || value === false) return false;
  return !(typeof value === "string" && !value.trim());
}

export function firstNonEmptyString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

export function booleanOrUndefined(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes"].includes(normalized)) return true;
    if (["false", "0", "no"].includes(normalized)) return false;
  }
  return undefined;
}

export function mergeRecording(
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

export function mapRecordingFrame(frame: BackendStepRecordingFrame): NonNullable<TaskEvent["recordings"]>[number]["frames"][number] {
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

export function dedupeFrames<TFrame extends { phase: string; capturedAt: string; url?: string }>(frames: TFrame[]): TFrame[] {
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

export function absoluteBackendUrl(path: string): string | undefined {
  return absoluteRendererLoopbackBackendUrl(path, getBackendBaseUrl()) || undefined;
}

export function mapAgentKind(kind?: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (kind === "observation") return "observation";
  if (kind === "review" || kind === "critique") return "handoff";
  if (kind === "final") return "result";
  return "action";
}

export function agentNameFor(message?: BackendAgentMessage): string {
  return message?.name ?? message?.metadata?.from_agent ?? message?.from_agent ?? "assistant";
}

export function metadataPayloadFor<TPayload>(message?: BackendAgentMessage): TPayload | undefined {
  const payload = message?.metadata?.structured_payload ?? message?.structured_payload;
  return payload as TPayload | undefined;
}

export function mapRiskSeverity(risk: string): SafetyReview["findings"][number]["severity"] {
  if (risk.startsWith("R4")) return "critical";
  if (risk.startsWith("R3")) return "high";
  if (risk.startsWith("R2")) return "medium";
  return "low";
}

export function mapApproval(approval: BackendApproval): ApprovalRequest {
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

export function optionalObjectRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

export function cleanupPlanFromApprovalPayload(payload: unknown): CleanupPlan | undefined {
  const candidate = findCleanupPayload(payload);
  if (!candidate) return undefined;
  const plan = mapCleanupPlan(candidate);
  return plan.items.length ? plan : undefined;
}

export function findCleanupPayload(value: unknown): BackendCleanupPlan | undefined {
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

export function looksLikeCleanupPlan(record: Record<string, unknown>): boolean {
  if (Array.isArray(record.items) && record.items.some(looksLikeCleanupItem)) return true;
  const buckets = record.buckets;
  return Boolean(
    buckets &&
      typeof buckets === "object" &&
      ["direct_delete", "permanent_delete", "recycle_bin", "trash", "suggestion_only", "info_only", "immediate", "approval"]
        .some((key) => Array.isArray((buckets as Record<string, unknown>)[key]))
  );
}

export function looksLikeCleanupItem(value: unknown): value is BackendCleanupItem {
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

export function settingsPatchFor(settings: AppSettings, baseline: AppSettings | null): Partial<BackendSettings> {
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

export function mergeDesktopOnlySettings(settings: AppSettings, source: AppSettings | null): AppSettings {
  if (!source) return settings;
  return {
    ...settings,
    autoStartBackend: source.autoStartBackend,
    telemetryEnabled: source.telemetryEnabled,
    compactMode: source.compactMode,
    theme: source.theme
  };
}

export function allowedDirectoriesForSettings(settings: AppSettings, baseline?: AppSettings | null): string[] {
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

export function sameStringArray(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

export function mapMcpServerForBackend(server: AppSettings["mcpServers"][number]): NonNullable<BackendSettings["mcp_servers"]>[number] {
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

export function hasPersistableMcpServerTarget(server: NonNullable<BackendSettings["mcp_servers"]>[number]): boolean {
  return Boolean(String(server.url ?? "").trim() || String(server.command ?? "").trim());
}

export function mapSettings(settings: BackendSettings): AppSettings {
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

export function normalizePermissionMode(value?: string): AppSettings["permissionMode"] {
  const normalized = String(value ?? "default").toLowerCase();
  if (normalized === "plan" || normalized === "trusted_edits" || normalized === "auto_review" || normalized === "dont_ask") {
    return normalized;
  }
  return "default";
}

export function mapLlmHealth(health: BackendLlmHealth): LLMHealthStatus {
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

export function mapLlmProfile(profile?: BackendLlmProfile): LLMProfile {
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

export function mapLlmCostSummary(summary: BackendLlmCostSummary): LLMCostSummary {
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

export function mapContextUsage(usage: BackendContextUsage): ContextUsage {
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

export function contextHealthStatus(value: unknown, fallback: ContextUsage["health"]["status"]): ContextUsage["health"]["status"] {
  if (value === "healthy" || value === "managed" || value === "watch" || value === "critical" || value === "blocked") {
    return value;
  }
  return fallback;
}

export function contextHealthSeverity(
  value: unknown,
  fallback: ContextUsage["health"]["severity"]
): ContextUsage["health"]["severity"] {
  if (value === "ok" || value === "warning" || value === "error") return value;
  return fallback;
}

export function contextHealthFallbackReason(severity: ContextUsage["health"]["severity"]): string {
  if (severity === "error") return "Context is close to its limit.";
  if (severity === "warning") return "Context is getting busy.";
  return "Context has room for the next step.";
}

export function objectRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, Number(item ?? 0)])
  );
}

export function mapLocalLlmHealth(health: BackendLocalLlmHealth): LocalLLMHealth {
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

export function mapLocalModelReadiness(readiness?: BackendLocalModelReadiness): LocalModelReadiness | undefined {
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

export function mapLocalModelSetupPlan(plan: BackendLocalModelSetupPlan): LocalModelSetupPlan {
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
    nextAction: String(plan.next_action ?? ""),
    repairAction: mapLocalModelRepairAction(plan.repair_action),
    verification: mapLocalModelVerification(plan.verification),
    evidence: (plan.evidence ?? []).map(mapLocalModelEvidenceItem)
  };
}

export function mapLocalModelRepairAction(action?: BackendLocalModelRepairAction): LocalModelSetupPlan["repairAction"] {
  if (!action || typeof action !== "object") return undefined;
  return {
    code: String(action.code ?? ""),
    label: String(action.label ?? ""),
    detail: String(action.detail ?? "")
  };
}

export function mapLocalModelVerification(verification?: BackendLocalModelVerification): LocalModelSetupPlan["verification"] {
  if (!verification || typeof verification !== "object") return undefined;
  return {
    ready: Boolean(verification.ready),
    nextAction: String(verification.next_action ?? ""),
    pathsRedacted: verification.paths_redacted !== false,
    privacyFallback: String(verification.privacy_fallback ?? "")
  };
}

export function mapLocalModelEvidenceItem(item: BackendLocalModelEvidenceItem): LocalModelSetupPlan["evidence"][number] {
  return {
    key: String(item.key ?? ""),
    ok: Boolean(item.ok),
    detail: String(item.detail ?? ""),
    valueLabel: localModelEvidenceValueLabel(item)
  };
}

export function localModelEvidenceValueLabel(item: BackendLocalModelEvidenceItem): string {
  if (item.value !== undefined && item.value !== null && typeof item.value !== "object") {
    return String(item.value);
  }
  if (Array.isArray(item.failed_checks) && item.failed_checks.length) {
    return `${item.failed_checks.length} checks need attention`;
  }
  if (item.configured !== undefined) {
    return item.configured ? "configured" : "not configured";
  }
  return item.ok ? "ok" : "needs attention";
}

export function mapLocalModelBundleManifest(manifest?: BackendLocalModelBundleManifest): LocalModelSetupPlan["bundleManifest"] {
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

export function mapLocalModelSetupStepState(value: unknown): LocalModelSetupPlan["steps"][number]["state"] {
  if (value === "pending" || value === "current" || value === "done" || value === "blocked") {
    return value;
  }
  return "pending";
}

export function mapInstalledApp(app: BackendInstalledApp): InstalledApp {
  return {
    id: String(app.id ?? app.name ?? ""),
    name: String(app.name ?? app.id ?? ""),
    path: app.path,
    command: app.command,
    source: String(app.source ?? "unknown"),
    allowlisted: Boolean(app.allowlisted)
  };
}

export function mapFileRevealResult(result: BackendFileRevealResult): FileRevealResult {
  return {
    ok: result.ok !== false,
    path: optionalString(result.path),
    revealed: Boolean(result.revealed),
    shown: Boolean(result.shown ?? result.revealed),
    error: optionalString(result.error)
  };
}

export function mapSkillsCatalog(data: BackendSkillsCatalog): SkillsCatalog {
  return {
    skills: (data.skills ?? []).map(mapInstalledSkill),
    count: Number(data.count ?? data.skills?.length ?? 0),
    directories: (data.directories ?? []).map(String),
    installDirectory: String(data.install_directory ?? "")
  };
}

export function mapSkillImportResult(data: BackendSkillImportResult): SkillImportResult {
  return {
    skill: mapInstalledSkill(data.skill),
    refresh: {
      ok: Boolean(data.refresh?.ok),
      toolCount: Number(data.refresh?.tool_count ?? 0),
      skillCount: Number(data.refresh?.skill_count ?? 0)
    }
  };
}

export function mapInstalledSkill(skill: BackendInstalledSkill): InstalledSkill {
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

export function mapProcess(process: BackendProcess): SystemProcess {
  return {
    pid: Number(process.pid ?? 0),
    name: String(process.name ?? "未知进程"),
    username: process.username,
    cpuPercent: Number(process.cpu_percent ?? 0),
    memoryBytes: Number(process.memory_bytes ?? 0),
    status: process.status
  };
}

export function mapChatMessage(message: BackendChatMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role,
    author: message.author,
    content: zhBackendText(message.content),
    createdAt: normalizeTimestamp(message.created_at ?? message.createdAt),
    status: message.status === "failed" ? "failed" : "sent"
  };
}

export function normalizeTimestamp(value: unknown, fallback = new Date().toISOString()): string {
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? fallback : new Date(timestamp).toISOString();
}

export function mapIntentSuggestion(suggestion: BackendIntentSuggestion): IntentSuggestion {
  return {
    id: suggestion.id,
    title: suggestion.title,
    prompt: zhBackendText(suggestion.prompt),
    confidence: Number(suggestion.confidence ?? 0),
    agentHint: suggestion.agent_hint,
    reason: suggestion.reason ? zhBackendText(suggestion.reason) : undefined
  };
}

export function mapSuggestionLaunchResponse(
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
            agent: runEngineAgentName(engine, data.engine_capabilities),
            createdAt,
            updatedAt
          }
        ]
      : []
  };
}

export function mapStartupItem(item: BackendStartupItem): StartupItem {
  return {
    name: String(item.name ?? "启动项"),
    path: item.path,
    command: item.command,
    source: String(item.source ?? "unknown")
  };
}

export function mapDiagnostic(data: BackendSystemDiagnostics, startupItems?: BackendStartupItem[]): SystemDiagnostic {
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
          offlineOnly: data.update_channel.offline_only === undefined ? undefined : Boolean(data.update_channel.offline_only),
          userActionLabel: data.update_channel.user_action_label ? String(data.update_channel.user_action_label) : undefined,
          nextSteps: Array.isArray(data.update_channel.next_steps) ? data.update_channel.next_steps.map(String) : undefined,
          releaseNotes: data.update_channel.release_notes
            ? {
                available: Boolean(data.update_channel.release_notes.available),
                label: data.update_channel.release_notes.label ? String(data.update_channel.release_notes.label) : undefined,
                detail: data.update_channel.release_notes.detail ? String(data.update_channel.release_notes.detail) : undefined,
                path: data.update_channel.release_notes.path ? String(data.update_channel.release_notes.path) : undefined,
                source: data.update_channel.release_notes.source ? String(data.update_channel.release_notes.source) : undefined
              }
            : undefined
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
    diagnosticScope: data.diagnostic_scope ? String(data.diagnostic_scope) : undefined,
    supportPackageRedaction: mapSupportPackageRedaction(data.support_package_redaction)
  };
}

export function mapDiagnosticExportResult(data: BackendDiagnosticExportResult): DiagnosticExportResult {
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

export function mapSupportPackageRedaction(
  redaction?: BackendSupportPackageRedaction
): SystemDiagnostic["supportPackageRedaction"] {
  if (!redaction || typeof redaction !== "object") return undefined;
  const currentResponse =
    redaction.current_response && typeof redaction.current_response === "object"
      ? {
          publicSafe: redaction.current_response.public_safe === true,
          containsLocalPaths: redaction.current_response.contains_local_paths === true,
          externalReviewRequired: redaction.current_response.external_review_required !== false
        }
      : undefined;
  const externalReview =
    redaction.external_review && typeof redaction.external_review === "object" ? redaction.external_review : undefined;
  const externalReviewStatus = String(externalReview?.status ?? "manual_review_required");
  const packagePublicSafe = redaction.public_safe === true;
  const packageReviewRequired = redaction.review_before_external_sharing !== false;
  const packageExternalSharingAllowed = redaction.external_sharing_allowed === true;
  const packageFailClosed = redaction.fail_closed !== false;
  const responsePublicSafe = currentResponse?.publicSafe === true;
  const responseReviewRequired = currentResponse?.externalReviewRequired !== false;
  const responseContainsLocalPaths = currentResponse?.containsLocalPaths === true;
  const reviewPublicSafe = externalReview?.public_safe === true;
  const reviewRequired = externalReview?.required_before_external_sharing !== false;
  const reviewExternalSharingAllowed = externalReview?.external_sharing_allowed === true;
  const reviewFailClosed = externalReview?.fail_closed !== false;
  const reviewStatusAllowsSharing = externalReviewStatusAllowsSharing(externalReviewStatus);
  const publicSafeSignals = [
    packagePublicSafe,
    ...(currentResponse ? [responsePublicSafe] : []),
    ...(externalReview ? [reviewPublicSafe] : [])
  ];
  const reviewRequiredSignals = [
    packageReviewRequired,
    ...(currentResponse ? [responseReviewRequired] : []),
    ...(externalReview ? [reviewRequired] : [])
  ];
  const safetySignalsConsistent =
    Boolean(currentResponse) &&
    Boolean(externalReview) &&
    allBooleanSignalsMatch(publicSafeSignals) &&
    allBooleanSignalsMatch(reviewRequiredSignals) &&
    !(responseContainsLocalPaths && publicSafeSignals.some(Boolean)) &&
    !(!reviewStatusAllowsSharing && !reviewRequired && reviewPublicSafe);
  const blockingReasons = [
    !packagePublicSafe ? "package_public_safe_false" : "",
    packageReviewRequired ? "package_review_required" : "",
    !packageExternalSharingAllowed ? "package_external_sharing_allowed_false" : "",
    packageFailClosed ? "package_fail_closed" : "",
    !currentResponse ? "current_response_missing" : "",
    currentResponse && !responsePublicSafe ? "current_response_public_safe_false" : "",
    responseContainsLocalPaths ? "current_response_contains_local_paths" : "",
    responseReviewRequired ? "current_response_review_required" : "",
    !externalReview ? "external_review_missing" : "",
    externalReview && !reviewPublicSafe ? "external_review_public_safe_false" : "",
    reviewRequired ? "external_review_required" : "",
    externalReview && !reviewExternalSharingAllowed ? "external_review_external_sharing_allowed_false" : "",
    externalReview && reviewFailClosed ? "external_review_fail_closed" : "",
    !reviewStatusAllowsSharing ? "external_review_status_not_approved" : "",
    !safetySignalsConsistent ? "safety_signals_inconsistent_or_incomplete" : ""
  ].filter(Boolean);
  const externalSharingSafe = blockingReasons.length === 0;
  return {
    appliesTo: redaction.applies_to ? String(redaction.applies_to) : undefined,
    scope: String(redaction.scope ?? "local_only"),
    intendedAudience: String(redaction.intended_audience ?? "trusted_support"),
    publicSafe: packagePublicSafe,
    reviewBeforeExternalSharing: packageReviewRequired,
    externalSharingAllowed: packageExternalSharingAllowed,
    failClosed: packageFailClosed,
    guidance: redaction.guidance ? zhBackendText(String(redaction.guidance)) : "",
    currentResponse,
    externalReview: externalReview
      ? {
          status: externalReviewStatus,
          requiredBeforeExternalSharing: reviewRequired,
          publicSafe: externalReview.public_safe === true,
          externalSharingAllowed: reviewExternalSharingAllowed,
          failClosed: reviewFailClosed,
          checklistCount: Array.isArray(externalReview.checklist) ? externalReview.checklist.length : 0
        }
      : undefined,
    externalSharingSafe,
    safetySignalsConsistent,
    blockingReasons
  };
}

export function allBooleanSignalsMatch(values: boolean[]): boolean {
  if (values.length <= 1) return true;
  return values.every((value) => value === values[0]);
}

export function externalReviewStatusAllowsSharing(status: string): boolean {
  return [
    "approved",
    "clear",
    "cleared",
    "external_sharing_approved",
    "reviewed",
    "safe_to_share"
  ].includes(status.trim().toLowerCase());
}

export function plainRecord(value: Record<string, unknown> | null | undefined): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value : undefined;
}

export function numberRecord(value: Record<string, unknown> | undefined): Record<string, number> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, item]) => [key, Number(item)] as const)
      .filter(([, item]) => Number.isFinite(item))
  );
}

export function formatDiffPreview(diffPreview: unknown): string {
  if (!diffPreview || typeof diffPreview !== "object") {
    return String(diffPreview ?? "无预览内容");
  }
  return JSON.stringify(localizeDiffPreview(diffPreview), null, 2);
}

export function localizeDiffPreview(value: unknown): unknown {
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

export function mapBrowserLink(link: BackendBrowserLink): BrowserLinkResult {
  return {
    title: String(link.title ?? link.url ?? ""),
    url: String(link.url ?? "")
  };
}

export function mapBrowserPage(page: BackendBrowserPage): BrowserPageSnapshot {
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

export function mapBrowserSession(session: BackendBrowserSession): BrowserSession {
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

export function mapBrowserActivityEvent(event: BackendBrowserActivityEvent): BrowserActivityEvent {
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

export function mapBrowserActivityEnvelope(data: BackendBrowserActivityEnvelope): BrowserActivityEvent {
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

export function mapBrowserReplayExport(data: BackendBrowserReplayExport): BrowserReplayExport {
  return {
    ok: data.ok !== false,
    url: optionalString(data.url),
    path: optionalString(data.path),
    session: data.session ? mapBrowserSession(data.session) : undefined,
    events: Array.isArray(data.events) ? data.events.map(mapBrowserActivityEvent) : undefined,
    error: optionalString(data.error)
  };
}

export function isBrowserAction(value: unknown): value is BrowserAction {
  return Boolean(value && typeof value === "object" && typeof (value as { kind?: unknown }).kind === "string");
}

export function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

export function numberOrUndefined(value: unknown): number | undefined {
  const number = typeof value === "number" ? value : typeof value === "string" && value.trim() ? Number(value) : NaN;
  return Number.isFinite(number) ? number : undefined;
}

export function numberOrZero(value: unknown): number {
  return numberOrUndefined(value) ?? 0;
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

export function arrayOfObjects(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)))
    : [];
}

export function recordOrUndefined(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

export function tableRowsFromUnknown(value: unknown): string[][] {
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

export function fileNameFromPath(path: string): string | undefined {
  const normalized = path.replace(/\\/g, "/");
  const name = normalized.split("/").filter(Boolean).pop();
  return name || undefined;
}

export function emptyBrowserHostSnapshot(hostAvailable: boolean): BrowserHostSnapshot {
  return {
    sessions: [],
    events: [],
    activeSessionId: null,
    visible: false,
    hostAvailable
  };
}

export function mergeBrowserSessionArrays(primary: BrowserSession[], secondary: BrowserSession[]): BrowserSession[] {
  const byId = new Map<string, BrowserSession>();
  for (const session of primary) byId.set(session.id, session);
  for (const session of secondary) byId.set(session.id, { ...byId.get(session.id), ...session });
  return [...byId.values()].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function emptyPlan(): Plan {
  return {
    id: "empty",
    title: "暂无活动计划",
    objective: "提交一个任务后会在这里生成计划。",
    updatedAt: new Date().toISOString(),
    steps: []
  };
}

export function emptySafetyReview(): SafetyReview {
  return {
    id: "empty",
    status: "clear",
    updatedAt: new Date().toISOString(),
    findings: []
  };
}


export function mapHardwareAccelerationStatus(status: BackendHardwareAccelerationStatus): HardwareAccelerationStatusPayload {
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

export function mapHardwareAccelerationSmoke(data: BackendHardwareAccelerationSmoke): HardwareAccelerationSmokePayload {
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

export function mapHardwareAccelerationLlm(llm: NonNullable<BackendHardwareAccelerationStatus["llm"]>): HardwareAccelerationStatusPayload["llm"] {
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

export function mapHardwareAccelerationOperation(
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

export function mapHardwareAccelerationComponent(
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

export function mapWinmlStatus(winml: NonNullable<BackendHardwareAccelerationStatus["winml"]>): NonNullable<HardwareAccelerationStatusPayload["winml"]> {
  return {
    available: Boolean(winml.available),
    provider: String(winml.provider ?? "WindowsMLExecutionProvider"),
    providerAvailable: Boolean(winml.provider_available),
    packages: (winml.packages ?? []).map(String),
    errors: Object.fromEntries(Object.entries(winml.errors ?? {}).map(([key, value]) => [key, String(value)]))
  };
}

export function mapRuntimePackages(
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

export function objectStringRecord(value: Record<string, string> | undefined): Record<string, string> | undefined {
  if (!value) return undefined;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item)]));
}

export function normalizeExecutionProvider(value: string): AppSettings["onnxExecutionProvider"] {
  const lowered = value.trim().toLowerCase();
  if (!lowered) return "";
  if (["auto", "winml", "directml", "openvino", "cpu"].includes(lowered)) {
    return lowered === "auto" ? "" : (lowered[0].toUpperCase() + lowered.slice(1)) as AppSettings["onnxExecutionProvider"];
  }
  if (lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "dml") return "DirectML";
  return value;
}

export function normalizeHardwareRuntime(value: string): string {
  const lowered = String(value ?? "").trim().toLowerCase();
  if (!lowered || lowered === "auto") return "";
  if (lowered === "winml" || lowered === "windowsml" || lowered === "windows_ml") return "WinML";
  if (lowered === "directml" || lowered === "dml") return "DirectML";
  if (lowered === "openvino") return "OpenVINO";
  if (lowered === "cpu") return "CPU";
  return value;
}

