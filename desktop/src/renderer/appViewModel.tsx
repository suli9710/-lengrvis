import type { ApprovalRequest, BackendStatus, ChatMessage, LocalLLMHealth, TaskEvent } from "../shared/types";
import type { HomeReadinessItem, HomeTrustItem } from "./features/office";
import type { RealtimeConnectionStatus } from "./lib/apiClient";
import { zhBackendText, zhRealtimeBadMessageSummary, zhRealtimeConnectionStatus, zhUserFacingError } from "./lib/zh";
import type { AssistantMode, ConnectionState } from "./store";

export function mergeTaskSnapshots(runTasks: TaskEvent[], legacyTasks: TaskEvent[]): TaskEvent[] {
  if (!runTasks.length) return legacyTasks;
  if (!legacyTasks.length) return runTasks;

  const legacyById = new Map(legacyTasks.map((task) => [task.id, task]));
  const merged = runTasks.map((runTask) => {
    const legacyTask = legacyById.get(runTask.id);
    if (!legacyTask) return runTask;
    legacyById.delete(runTask.id);
    return {
      ...legacyTask,
      ...runTask,
      recordings: runTask.recordings?.length ? runTask.recordings : legacyTask.recordings
    };
  });

  return [...merged, ...legacyById.values()];
}

export function RouteLoading() {
  return (
    <div className="route-loading" role="status" aria-live="polite">
      <span className="spin-icon" aria-hidden="true" />
      <span>正在载入</span>
    </div>
  );
}

export function selectedPendingApproval(approvals: ApprovalRequest[], taskId?: string | null): ApprovalRequest | null {
  if (taskId) {
    const taskApproval = approvals.find((approval) => approval.taskId === taskId);
    if (taskApproval) return taskApproval;
  }
  return approvals[0] ?? null;
}

export function latestLegacyTaskIdFromEvents(
  tasks: TaskEvent[],
  chatStartedTaskIds: Set<string>,
  announcedTerminalTaskIds: Set<string>
): string | null {
  const active = tasks.find(
    (task) =>
      !task.runId &&
      chatStartedTaskIds.has(task.id) &&
      (task.state === "running" || task.state === "queued" || task.state === "blocked")
  );
  if (active) return active.id;
  const pendingReplay = tasks.find(
    (task) => !task.runId && chatStartedTaskIds.has(task.id) && !announcedTerminalTaskIds.has(task.id)
  );
  return pendingReplay?.id ?? null;
}

export function requiresLocalLlmHealth(mode: AssistantMode): boolean {
  return mode === "privacy" || mode === "hybrid";
}

export function isBackendTaskSubmitReady(status: BackendStatus | null): status is BackendStatus {
  return Boolean(status?.health?.ok || (status?.state === "running" && !status?.health));
}

export function isReadOnlySystemDiagnosticsPrompt(content: string): boolean {
  const normalized = content.trim().toLowerCase();
  if (!normalized) return false;
  const hasDiagnosticsAction =
    normalized.includes("\u68c0\u67e5") ||
    normalized.includes("\u67e5\u770b") ||
    normalized.includes("\u8bca\u65ad") ||
    normalized.includes("diagnostics") ||
    normalized.includes("system status") ||
    normalized.includes("computer status");
  const hasComputerTarget =
    normalized.includes("\u8fd9\u53f0\u7535\u8111") ||
    normalized.includes("\u7535\u8111") ||
    normalized.includes("\u7cfb\u7edf") ||
    normalized.includes("computer") ||
    normalized.includes("system");
  return hasDiagnosticsAction && hasComputerTarget;
}

export function buildHomeReadinessItems({
  connectionState,
  realtimeStatus,
  mode,
  localLlmHealth,
  allowedDirectories,
  workspaceRoot
}: {
  connectionState: ConnectionState;
  realtimeStatus: RealtimeConnectionStatus | null;
  mode: AssistantMode;
  localLlmHealth: LocalLLMHealth | null;
  allowedDirectories?: string[];
  workspaceRoot: string;
}): HomeReadinessItem[] {
  const primaryScope = allowedDirectories?.[0] || workspaceRoot || "";
  const privacyReady = mode === "privacy" && Boolean(localLlmHealth?.available);
  const privacyAction = mode === "efficiency" ? "开启" : localLlmHealth?.available ? "查看" : "准备";
  const realtimeDetail =
    realtimeStatus && realtimeStatus.state !== "open" ? zhRealtimeConnectionStatus(realtimeStatus) : "";
  const realtimeNeedsAction = Boolean(
    realtimeStatus && ["unauthorized", "policy_violation", "error", "closed"].includes(realtimeStatus.state)
  );

  return [
    {
      id: "connection",
      label: "Lengrvis 连接",
      detail: realtimeDetail || (connectionState === "online" ? "服务已连接，输入一句话即可开始" : connectionState === "checking" ? "正在确认本机服务" : "服务离线，输入会保留，恢复后可重试"),
      state: realtimeNeedsAction ? "action" : connectionState === "online" ? "ready" : connectionState === "checking" ? "warning" : "action",
      actionLabel: connectionState === "online" ? "刷新连接" : "检查连接"
    },
    {
      id: "privacy",
      label: "隐私与本地 AI",
      detail: privacyReady
        ? "隐私模式和本地 AI 已就绪"
        : mode === "efficiency"
          ? "当前是高效模式，可一键切到隐私"
          : "隐私模式已开，继续准备本地 AI",
      state: privacyReady ? "ready" : "action",
      actionLabel: privacyAction,
      targetView: "settings"
    },
    {
      id: "scope",
      label: "文件范围",
      detail: primaryScope ? `${compactPath(primaryScope)} · 只看授权范围` : "先选择桌面、下载或指定文件夹",
      state: primaryScope ? "ready" : "action",
      actionLabel: primaryScope ? "查看" : "选择",
      targetView: "files"
    },
    {
      id: "document",
      label: "文档操作",
      detail: "选择文件后再读取正文和生成引用",
      state: "action",
      actionLabel: "打开",
      targetView: "files"
    }
  ];
}

export function buildHomeTrustItems({
  mode,
  localLlmHealth,
  allowedDirectories,
  workspaceRoot,
  allowCloudContext,
  allowFileContentUpload
}: {
  mode: AssistantMode;
  localLlmHealth: LocalLLMHealth | null;
  allowedDirectories?: string[];
  workspaceRoot: string;
  allowCloudContext: boolean;
  allowFileContentUpload: boolean;
}): HomeTrustItem[] {
  const primaryScope = allowedDirectories?.[0] || workspaceRoot || "";
  const localReady = mode === "privacy" && Boolean(localLlmHealth?.available);
  const localPreparing = mode !== "efficiency" && !localLlmHealth?.available;
  const privacyMode = mode === "privacy";

  return [
    {
      id: "ai",
      label: "AI 运行",
      value: localReady ? "本机可用" : privacyMode ? "本机优先" : localPreparing ? "本机准备中" : "高效模式",
      detail: localReady
        ? "隐私任务优先留在这台电脑"
        : privacyMode
          ? "健康未读取时不会静默退云端"
        : localPreparing
          ? "不会静默退回云端"
        : allowCloudContext
            ? "可使用云端辅助，受权限约束"
            : "云端上下文关闭，优先本机范围",
      state: localReady || !allowCloudContext ? "ready" : "warning"
    },
    {
      id: "files",
      label: "文件范围",
      value: primaryScope ? compactPath(primaryScope) : "未授权",
      detail: primaryScope ? "文件工具只看授权目录" : "先选择桌面、下载或文件夹",
      state: primaryScope ? "ready" : "warning"
    },
    {
      id: "upload",
      label: "文件内容上传",
      value: allowFileContentUpload ? "需确认" : "已关闭",
      detail: allowFileContentUpload ? "上传正文前仍会遵守权限" : "默认不上传文件正文",
      state: allowFileContentUpload ? "warning" : "ready"
    },
    {
      id: "approval",
      label: "危险操作",
      value: "先审查",
      detail: "删除、移动、写入前暂停等待确认",
      state: "ready"
    }
  ];
}

export function compactPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return `${parts.at(-2)}/${parts.at(-1)}`;
}

export interface RealtimeBadMessageNotice {
  count: number;
  messageId: string;
  samples: string[];
}

export function connectionStateFromBackendAndRealtime(
  backendStatus: { state: string },
  realtimeStatus: RealtimeConnectionStatus | null
): ConnectionState {
  if (backendStatus.state === "starting") return "checking";
  if (backendStatus.state !== "running") return "offline";
  if (!realtimeStatus) return "online";
  if (realtimeStatus.state === "connecting" || realtimeStatus.state === "reconnecting") return "checking";
  return "online";
}

export function shouldShowRealtimeStatusMessage(status: RealtimeConnectionStatus): boolean {
  return ["reconnecting", "error", "unauthorized", "policy_violation"].includes(status.state);
}

export function realtimeStatusChatMessage(status: RealtimeConnectionStatus): ChatMessage {
  return {
    id: `realtime-status-${status.endpoint}-${status.state}`,
    role: "assistant",
    author: "Lengrvis",
    content: zhRealtimeConnectionStatus(status),
    createdAt: status.at,
    status: status.state === "reconnecting" ? "streaming" : "failed"
  };
}

export function upsertRealtimeBadMessageNotice(
  current: ChatMessage[],
  notice: RealtimeBadMessageNotice,
  status: RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string }
): ChatMessage[] {
  notice.count += 1;
  const sample = safeRealtimeBadMessageSample();
  if (!notice.samples.includes(sample)) {
    notice.samples = [sample];
  }
  const nextMessage: ChatMessage = {
    id: notice.messageId,
    role: "assistant",
    author: "Lengrvis",
    content: zhRealtimeBadMessageSummary(notice.count, notice.samples),
    createdAt: status.at,
    status: "streaming"
  };
  return [...current.filter((message) => message.id !== notice.messageId), nextMessage];
}

function safeRealtimeBadMessageSample(): string {
  return "原始内容已隐藏，避免显示本机路径、文件名、连接地址、提示词或凭据。";
}

export function isActiveTask(task: TaskEvent): boolean {
  return task.state === "running" || task.state === "queued" || task.state === "blocked" || task.state === "paused";
}

export function wasUpdatedRecently(task: TaskEvent): boolean {
  const timestamp = Date.parse(task.updatedAt || task.createdAt || "");
  if (Number.isNaN(timestamp)) return true;
  return Date.now() - timestamp < 15 * 60 * 1000;
}

export function chatMessageFromRunTerminalEvent(event: {
  id: string;
  event?: string;
  name?: string;
  created_at?: string;
  payload?: Record<string, unknown>;
}): ChatMessage | null {
  const name = event.event ?? event.name ?? "";
  if (!["run.completed", "run.failed", "run.cancelled", "run.denied", "run.waiting_approval"].includes(name)) {
    return null;
  }
  const payload = event.payload ?? {};
  const content = terminalRunMessage(name, payload);
  return {
    id: `${event.id}-chat`,
    role: "assistant",
    author: "Lengrvis",
    content,
    createdAt: event.created_at ?? new Date().toISOString(),
    status: name === "run.failed" ? "failed" : "sent"
  };
}

export function chatMessageFromTaskAgentMessage(
  taskId: string,
  message: {
    id: string;
    role?: ChatMessage["role"];
    name?: string;
    content: string;
    created_at: string;
    metadata?: Record<string, unknown>;
    from_agent?: string;
    message_type?: string;
  }
): ChatMessage | null {
  if (!isUserVisibleTaskMessage(message)) return null;
  const author = zhAgentAuthor(message.name ?? message.from_agent ?? String(message.metadata?.from_agent ?? "Agent"));
  return {
    id: `${message.id}-chat`,
    role: "assistant",
    author,
    content: summarizeTaskAgentContent(message.content, author),
    createdAt: message.created_at,
    status: "sent"
  };
}

export function chatMessageFromLegacyTaskTerminal(task: TaskEvent): ChatMessage | null {
  const content = task.state === "completed"
    ? terminalLegacyTaskMessage("completed", task.description)
    : terminalLegacyTaskMessage("failed", task.description);
  return {
    id: `${task.id}-terminal-chat`,
    role: "assistant",
    author: "主管 Agent",
    content,
    createdAt: task.updatedAt || new Date().toISOString(),
    status: task.state === "failed" ? "failed" : "sent"
  };
}

export function isUserVisibleTaskMessage(message: {
  role?: ChatMessage["role"];
  content: string;
  metadata?: Record<string, unknown>;
  from_agent?: string;
  message_type?: string;
}): boolean {
  if (message.role === "user" || message.role === "system" || message.role === "developer") return false;
  const agent = String(message.metadata?.from_agent ?? message.from_agent ?? "");
  const type = String(message.metadata?.message_type ?? message.message_type ?? "");
  const content = message.content.trim();
  if (!content) return false;
  if (agent === "SafetyReviewAgent" || agent === "MemoryAgent" || agent === "ToolRuntime") return false;
  if (type === "review") return false;
  if (isInternalTaskMessageContent(content)) return false;
  return /清理预览|cleanup plan|dry-run preview|已生成|等待.*审批|审批后|没有删除|没有继续|完成|失败/.test(content);
}

export function summarizeTaskAgentContent(content: string, author: string): string {
  const translated = zhBackendText(content).replace(/\s*Large output persisted to .+$/u, "").trim();
  if (translated.includes("已生成清理预览")) {
    return `${author} 已生成清理预览。你可以在任务面板查看候选项；不会直接删除文件，需要你审批后才会执行。`;
  }
  if (translated.includes("Explicit absolute path is required")) {
    return `${author} 没有拿到明确路径，所以没有继续清理。请说具体目录，比如 D:\\Downloads 或 D:\\Temp。`;
  }
  return translated;
}

export function isInternalTaskMessageContent(content: string): boolean {
  const value = content.trim();
  if (!value) return true;
  const internalPatterns = [
    /^propose_tool\b/i,
    /^request_revision\b/i,
    /^Calling tool\b/i,
    /^Starting [\w.]+\.?$/i,
    /^Completed [\w.]+\.?$/i,
    /^Recorded before\/after screenshots/i,
    /^tool_observation:/i,
    /^Remembered:/i,
    /^[\w.]+\s+已完成。?$/i,
    /^[\w.]+\s+执行失败。?$/i,
    /accepted the planned tool call via deterministic fast path/i
  ];
  if (internalPatterns.some((pattern) => pattern.test(value))) return true;
  return [
    "正在调用工具",
    "文件路径必须保持在授权目录内",
    "系统检查默认只读",
    "应用操作仅限",
    "浏览器操作默认只读",
    "外部搜索结果必须保留"
  ].some((fragment) => value.includes(fragment));
}

export function terminalLegacyTaskMessage(status: "completed" | "failed", description: string): string {
  const detail = stripTerminalPrefix(zhBackendText(description));
  if (status === "completed") {
    if (detail.includes("只读") || detail.includes("清理预览")) {
      return "清理预览已完成。我没有删除任何文件；需要真正清理时，会先让你确认要处理哪些项目。";
    }
    return detail ? `任务已完成：${detail}` : "任务已完成。";
  }
  if (detail.includes("Explicit absolute path is required") || detail.includes("绝对路径")) {
    return "这次没有继续执行，因为没有拿到明确目录。请告诉我具体要清理的位置，比如 D:\\Downloads 或 D:\\Temp。";
  }
  return detail ? `任务执行失败：${detail}` : "任务执行失败。";
}

export function zhAgentAuthor(agent: string): string {
  if (agent === "FileAgent") return "文件 Agent";
  if (agent === "DocumentAgent") return "文档 Agent";
  if (agent === "ComputerAgent") return "电脑 Agent";
  if (agent === "BrowserAgent") return "浏览器 Agent";
  if (agent === "SearchAgent") return "搜索 Agent";
  if (agent === "PlannerAgent") return "规划 Agent";
  if (agent === "OrchestratorAgent") return "调度 Agent";
  return agent || "Agent";
}

export function terminalRunMessage(name: string, payload: Record<string, unknown>): string {
  const detail = String(
    payload.final_summary ?? payload.message ?? payload.transition_reason ?? payload.reason ?? payload.error ?? ""
  ).trim();
  const normalizedDetail = stripTerminalPrefix(zhBackendText(detail));
  const suffix = normalizedDetail ? `：${normalizedDetail}` : "。";
  if (name === "run.completed") return `任务已完成${suffix}`;
  if (name === "run.waiting_approval") return `任务正在等待你的审批${suffix}`;
  if (name === "run.cancelled") return `任务已取消${suffix}`;
  if (name === "run.denied") return `任务已被拦截${suffix}`;
  return `任务执行失败${suffix}`;
}

export function stripTerminalPrefix(value: string): string {
  return value
    .replace(/^任务执行失败[：:]\s*/u, "")
    .replace(/^执行失败[：:]\s*/u, "")
    .trim();
}

export function recentReadableChatMessages(messages: ChatMessage[], limit: number): ChatMessage[] {
  return messages
    .filter((message) => {
      const content = typeof message.content === "string" ? message.content : "";
      if (!content.trim()) return false;
      if (isMojibakeLike(content)) return false;
      if (content.length > 480) return false;
      return message.role === "user" || message.role === "assistant";
    })
    .slice(-limit);
}

export function isMojibakeLike(value: string): boolean {
  const compact = value.replace(/\s+/g, "");
  if (!compact) return false;
  const questionMarks = (compact.match(/\?/g) ?? []).length;
  if (questionMarks >= 3 && questionMarks / compact.length > 0.2) return true;
  return /�|Ã|Â|ä¸|ç®|å·|æ/.test(compact);
}

export function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), timeoutMs);
    promise
      .then(resolve)
      .catch(reject)
      .finally(() => window.clearTimeout(timer));
  });
}

export function readableError(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : fallback;
  return zhUserFacingError(message) || fallback;
}

export function appendUniqueMessage(current: ChatMessage[], message: ChatMessage): ChatMessage[] {
  if (current.some((item) => item.id === message.id)) return current;
  const messageTime = Date.parse(message.createdAt || "");
  if (
    current.some((item) => {
      if (item.role !== message.role || item.author !== message.author || item.content !== message.content) return false;
      const itemTime = Date.parse(item.createdAt || "");
      if (Number.isNaN(itemTime) || Number.isNaN(messageTime)) return true;
      return Math.abs(itemTime - messageTime) < 30_000;
    })
  ) {
    return current;
  }
  return [...current, message];
}
