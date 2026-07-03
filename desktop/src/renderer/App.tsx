import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import type {
  AppSettings,
  ApiResponse,
  ApprovalRequest,
  ChatMessage,
  FileSearchMeta,
  IntentSuggestion,
  SystemInfo,
  TaskEvent
} from "../shared/types";
import type { DocumentIntentAction, FileToolTab } from "./components/FileSearchPanel";
import { AppSurface } from "./app/AppSurface";
import { useAppStoreSnapshot } from "./app/useAppStoreSnapshot";
import { useHomeSignals } from "./app/useHomeSignals";
import { useRealtimeStatusHandlers } from "./app/useRealtimeStatusHandlers";
import { useTaskRealtimeSync } from "./app/useTaskRealtimeSync";
import { useWorkspaceRefresh } from "./app/useWorkspaceRefresh";
import {
  type HomeReadinessItem,
  type OfficeQuickSkill
} from "./features/office";
import { taskStarterManifestById } from "./features/office/taskStarterManifest";
import { LengrvisApiClient, type RealtimeConnectionStatus } from "./lib/apiClient";
import type { AssistantMode } from "./store";
import {
  isBackendTaskSubmitReady,
  isReadOnlySystemDiagnosticsPrompt,
  mergeTaskSnapshots,
  readableError,
  recentReadableChatMessages,
  requiresLocalLlmHealth,
  selectedPendingApproval,
  withTimeout,
  type RealtimeBadMessageNotice
} from "./appViewModel";

const TASK_SUBMIT_BACKEND_READY_TIMEOUT_MS = 5_000;

export function App() {
  const api = useMemo(() => new LengrvisApiClient(), []);
  const {
    messages,
    setMessages,
    tasks,
    setTasks,
    plan,
    setPlan,
    agentConversations,
    setAgentConversations,
    safetyReview,
    setSafetyReview,
    approvalRequests,
    setApprovalRequests,
    fileResults,
    setFileResults,
    settings,
    setSettings,
    auditEntries,
    setAuditEntries,
    systemInfo,
    setSystemInfo,
    intentSuggestions,
    setIntentSuggestions,
    backendStatus,
    setBackendStatus,
    localLlmHealth,
    setLocalLlmHealth,
    llmHealth,
    setLlmHealth,
    llmCostSummary,
    setLlmCostSummary,
    setContextUsage,
    isLoading,
    setIsLoading,
    isSearching,
    setIsSearching,
    isApprovalOpen,
    setIsApprovalOpen,
    approvalError,
    setApprovalError,
    mode,
    setMode,
    activeView,
    setActiveView,
    focusedTaskId,
    setFocusedTaskId,
    browserSessions,
    setBrowserSessions,
    browserEvents,
    setBrowserEvents,
    browserHostSnapshot,
    setBrowserHostSnapshot,
    activeBrowserSessionId,
    setActiveBrowserSessionId,
    browserError,
    setBrowserError
  } = useAppStoreSnapshot();
  const [draft, setDraft] = useState("");
  const [fileSearchError, setFileSearchError] = useState<string | null>(null);
  const [fileSearchMeta, setFileSearchMeta] = useState<FileSearchMeta | null>(null);
  const [fileToolTab, setFileToolTab] = useState<FileToolTab>("search");
  const [documentIntent, setDocumentIntent] = useState<{
    path: string;
    action: DocumentIntentAction;
    nonce: number;
  } | null>(null);
  const documentIntentNonce = useRef(0);
  const [settingsIntent, setSettingsIntent] = useState<{ section: "privacy"; nonce: number } | null>(null);
  const settingsIntentNonce = useRef(0);
  const [isCheckingComputer, setIsCheckingComputer] = useState(false);
  const [hasLoadedBackendTasks, setHasLoadedBackendTasks] = useState(false);
  const [heroSubmitting, setHeroSubmitting] = useState(false);
  const [heroSubmitError, setHeroSubmitError] = useState<string | null>(null);
  const [approvalSelectionContext, setApprovalSelectionContext] = useState<"task" | "queue">("task");
  const [approvalQueueCursor, setApprovalQueueCursor] = useState(0);
  const [realtimeStatus, setRealtimeStatus] = useState<RealtimeConnectionStatus | null>(null);
  const heroSubmitInFlight = useRef(false);
  const backendStatusRef = useRef(backendStatus);
  const realtimeBadMessageNotice = useRef<RealtimeBadMessageNotice>({
    count: 0,
    messageId: `realtime-bad-message-${crypto.randomUUID()}`,
    samples: []
  });

  const pendingApprovals = useMemo(
    () => approvalRequests.filter((approval) => approval.status === "pending"),
    [approvalRequests]
  );
  const pendingApproval = useMemo(
    () => {
      if (approvalSelectionContext === "queue") {
        return pendingApprovals[Math.min(approvalQueueCursor, Math.max(pendingApprovals.length - 1, 0))] ?? null;
      }
      return selectedPendingApproval(pendingApprovals, focusedTaskId);
    },
    [approvalQueueCursor, approvalSelectionContext, focusedTaskId, pendingApprovals]
  );
  const { connectionState, homeReadinessItems, homeTrustItems } = useHomeSignals({
    backendStatus,
    realtimeStatus,
    mode,
    localLlmHealth,
    settings
  });
  const recentReadableMessages = useMemo(() => recentReadableChatMessages(messages, 8), [messages]);

  useEffect(() => {
    backendStatusRef.current = backendStatus;
  }, [backendStatus]);

  const { handleRealtimeStatus, handleRealtimeBadMessage } = useRealtimeStatusHandlers({
    setRealtimeStatus,
    setMessages,
    realtimeBadMessageNotice
  });

  const refreshWorkspace = useWorkspaceRefresh({
    api,
    backendStatusRef,
    mode,
    activeBrowserSessionId,
    setMessages,
    setTasks,
    setPlan,
    setAgentConversations,
    setSafetyReview,
    setApprovalRequests,
    setSettings,
    setMode,
    setLlmHealth,
    setLlmCostSummary,
    setContextUsage,
    setAuditEntries,
    setSystemInfo,
    setIntentSuggestions,
    setBrowserSessions,
    setBrowserHostSnapshot,
    setActiveBrowserSessionId,
    setBackendStatus,
    setLocalLlmHealth,
    setIsLoading,
    setHasLoadedBackendTasks
  });

  useEffect(() => {
    if (activeView !== "browser") {
      void api.hideBrowserHost().catch(() => undefined);
    }
  }, [activeView, api]);

  const { chatStartedTaskIds, refreshTaskSnapshot } = useTaskRealtimeSync({
    api,
    tasks,
    hasLoadedBackendTasks,
    realtimeStatus,
    setRealtimeStatus,
    setMessages,
    setTasks,
    setPlan,
    setAgentConversations,
    setSafetyReview,
    setApprovalRequests,
    setFocusedTaskId,
    setActiveView,
    onRealtimeStatus: handleRealtimeStatus,
    onRealtimeBadMessage: handleRealtimeBadMessage
  });

  const markBackendResponsive = (message = "后端已响应任务请求") => {
    const currentStatus = backendStatusRef.current;
    if (currentStatus.state === "running" && currentStatus.health?.ok) return;
    const nextStatus = {
      ...currentStatus,
      state: "running" as const,
      message,
      health: {
        ...currentStatus.health,
        ok: true
      },
      lastCheckedAt: new Date().toISOString()
    };
    backendStatusRef.current = nextStatus;
    setBackendStatus(nextStatus);
  };

  const ensureBackendReadyForTaskSubmit = async (): Promise<{ ok: boolean; error?: string }> => {
    try {
      const status = await withTimeout(
        api.getBackendStatus(),
        TASK_SUBMIT_BACKEND_READY_TIMEOUT_MS,
        "连接检查超时"
      );
      backendStatusRef.current = status;
      setBackendStatus(status);
      if (status.health?.ok || (status.state === "running" && !status.health)) {
        markBackendResponsive(status.message ?? "后端已连接，可以启动任务");
        return { ok: true };
      }
      const healthProbeStatus = await api.probeBackendHealth(status.baseUrl);
      if (isBackendTaskSubmitReady(healthProbeStatus)) {
        backendStatusRef.current = {
          ...status,
          ...healthProbeStatus,
          message: healthProbeStatus.message ?? status.message ?? "后端已连接，可以启动任务"
        };
        setBackendStatus(backendStatusRef.current);
        markBackendResponsive(backendStatusRef.current.message);
        return { ok: true };
      }
      const healthReason = status.health && !status.health.ok ? "健康检查还没通过" : "";
      return {
        ok: false,
        error: status.message
          ? `Lengrvis 服务还没连上：${status.message}${healthReason ? `，${healthReason}` : ""}。输入内容已保留，可以稍后重试。`
          : `Lengrvis 服务还没连上${healthReason ? `：${healthReason}` : ""}。输入内容已保留，可以稍后重试。`
      };
    } catch (error) { // broad-exception-boundary
      return {
        ok: false,
        error: `Lengrvis 服务还没连上：${readableError(error, "连接检查失败")}。输入内容已保留，可以稍后重试。`
      };
    }
  };

  const sendMessage = async (content: string): Promise<{ ok: boolean; error?: string }> => {
    const readiness = await ensureBackendReadyForTaskSubmit();
    if (!readiness.ok) {
      return readiness;
    }

    const userMessage: ChatMessage = {
      id: `local-${crypto.randomUUID()}`,
      role: "user",
      author: "你",
      content,
      createdAt: new Date().toISOString(),
      status: "sent"
    };

    setMessages((current) => [...current, userMessage]);
    try {
      const preferRun = isReadOnlySystemDiagnosticsPrompt(content);
      let result = preferRun
        ? await api.startRun({ content, mode })
        : await api.sendChat({ content, mode });
      if (!result.ok && !preferRun) {
        result = await api.startRun({ content, mode });
      }

      const response = result.data;
      if (result.ok && response) {
        markBackendResponsive();
        setMessages((current) => [...current, response.message]);
        if (response.taskUpdates?.length) {
          response.taskUpdates.forEach((task) => chatStartedTaskIds.current.add(task.id));
          setTasks((current) => mergeTaskSnapshots(response.taskUpdates ?? [], current));
          setFocusedTaskId(response.taskUpdates[0]?.id ?? null);
          void refreshTaskSnapshot();
        }
        return { ok: true };
      }

      const message = result.error?.message ?? "Lengrvis 暂时不可用，请稍后再试。";
      setMessages((current) => [
        ...current,
        {
          id: `local-${crypto.randomUUID()}`,
          role: "assistant",
          author: "Lengrvis",
          content: message,
          createdAt: new Date().toISOString(),
          status: "failed"
        }
      ]);
      return { ok: false, error: message };
    } catch (error) { // broad-exception-boundary
      const message = error instanceof Error ? error.message : "Lengrvis 暂时不可用，请稍后再试。";
      setMessages((current) => [
        ...current,
        {
          id: `local-${crypto.randomUUID()}`,
          role: "assistant",
          author: "Lengrvis",
          content: message,
          createdAt: new Date().toISOString(),
          status: "failed"
        }
      ]);
      return { ok: false, error: message };
    }
  };

  const executeSuggestion = async (suggestion: IntentSuggestion) => {
    const readiness = await ensureBackendReadyForTaskSubmit();
    if (!readiness.ok) {
      setMessages((current) => [
        ...current,
        {
          id: `local-${crypto.randomUUID()}`,
          role: "assistant",
          author: "Lengrvis",
          content: readiness.error ?? "建议任务没有启动成功，输入内容未发送，可以稍后重试。",
          createdAt: new Date().toISOString(),
          status: "failed"
        }
      ]);
      return;
    }

    const userMessage: ChatMessage = {
      id: `local-${crypto.randomUUID()}`,
      role: "user",
      author: "你",
      content: suggestion.prompt,
      createdAt: new Date().toISOString(),
      status: "sent"
    };

    setMessages((current) => [...current, userMessage]);

    const result = await api.launchPerceptionSuggestion({
      suggestionId: suggestion.id,
      prompt: suggestion.prompt,
      mode
    });

    const response = result.data;
    if (result.ok && response) {
      setMessages((current) => [...current, response.message]);
      if (response.taskUpdates?.length) {
        setTasks((current) => mergeTaskSnapshots(response.taskUpdates ?? [], current));
      }
      setFocusedTaskId(response.runId ?? response.taskUpdates?.[0]?.id ?? null);
      void refreshTaskSnapshot();
      return;
    }

    setMessages((current) => [
      ...current,
      {
        id: `local-${crypto.randomUUID()}`,
        role: "assistant",
        author: "Lengrvis",
        content: result.error?.message ?? "建议任务启动失败，请稍后再试。",
        createdAt: new Date().toISOString(),
        status: "failed"
      }
    ]);
  };

  const submitHeroPrompt = async () => {
    const value = draft.trim();
    if (!value || heroSubmitInFlight.current) return;

    heroSubmitInFlight.current = true;
    setHeroSubmitting(true);
    setHeroSubmitError(null);
    const result = await sendMessage(value);
    if (result.ok) {
      setDraft("");
    } else {
      setDraft(value);
      setHeroSubmitError(result.error ?? "任务没有启动成功，输入内容已保留，可以重试。");
    }
    setHeroSubmitting(false);
    heroSubmitInFlight.current = false;
  };

  const searchFiles = async (query: string) => {
    if (!query.trim()) {
      setFileResults([]);
      setFileSearchMeta(null);
      setFileSearchError("请输入要查找的文件名或关键词。");
      return;
    }
    setIsSearching(true);
    setFileSearchError(null);
    try {
      const result = await api.searchFiles(query);
      if (result.ok && result.data) {
        setFileResults(result.data.results);
        setFileSearchMeta(result.data.meta);
        return;
      }

      setFileResults([]);
      setFileSearchMeta(null);
      setFileSearchError(result.error?.message ?? "文件搜索失败，请稍后重试。");
    } catch (error) { // broad-exception-boundary
      setFileResults([]);
      setFileSearchMeta(null);
      setFileSearchError(error instanceof Error ? error.message : "文件搜索失败，请稍后重试。");
    } finally {
      setIsSearching(false);
    }
  };

  const saveSettings = async (nextSettings: AppSettings) => {
    const previousSettings = settings;
    const previousMode = mode;
    setSettings(nextSettings);
    setMode(nextSettings.mode);
    let result: ApiResponse<AppSettings>;
    try {
      result = await api.saveSettings(nextSettings);
    } catch (error) { // broad-exception-boundary
      setSettings(previousSettings);
      setMode(previousMode);
      throw new Error(readableError(error, "无法保存设置"));
    }
    if (!result.ok) {
      setSettings(previousSettings);
      setMode(previousMode);
      throw new Error(result.error?.message ?? "无法保存设置");
    }
    if (result.ok && result.data) {
      setSettings(result.data);
      setMode(result.data.mode);
      if (requiresLocalLlmHealth(result.data.mode)) {
        const health = await api.getLocalLlmHealth();
        if (health.ok && health.data) setLocalLlmHealth(health.data);
      } else {
        setLocalLlmHealth(null);
      }
      const llm = await api.getLlmHealth();
      if (llm.ok && llm.data) setLlmHealth(llm.data);
      const cost = await api.getLlmCostSummary();
      if (cost.ok && cost.data) setLlmCostSummary(cost.data);
    }
  };

  const openWindowsSettings = async (uri: string) => {
    const result = await api.openWindowsSettings(uri);
    if (!result.ok) {
      setAuditEntries((current) => [
        {
          id: `settings-${crypto.randomUUID()}`,
          actor: "ComputerAgent",
          action: "open-settings-failed",
          target: uri,
          level: "warning",
          createdAt: new Date().toISOString()
        },
        ...current
      ]);
    }
    void refreshWorkspace();
  };

  const refreshSystemInfo = async (): Promise<SystemInfo | null> => {
    const [statusResult, llmHealthResult, llmCostResult, systemResult] = await Promise.allSettled([
      api.getBackendStatus(),
      api.getLlmHealth(),
      api.getLlmCostSummary(),
      api.getSystemInfo()
    ]);
    if (statusResult.status === "fulfilled") setBackendStatus(statusResult.value);
    if (llmHealthResult.status === "fulfilled" && llmHealthResult.value.ok && llmHealthResult.value.data) {
      setLlmHealth(llmHealthResult.value.data);
    }
    if (llmCostResult.status === "fulfilled" && llmCostResult.value.ok && llmCostResult.value.data) {
      setLlmCostSummary(llmCostResult.value.data);
    }
    const nextSystemInfo =
      systemResult.status === "fulfilled" && systemResult.value.ok && systemResult.value.data
        ? systemResult.value.data
        : null;
    if (nextSystemInfo) setSystemInfo(nextSystemInfo);
    if (requiresLocalLlmHealth(mode)) {
      const localLlmResult = await api.getLocalLlmHealth();
      if (localLlmResult.ok && localLlmResult.data) setLocalLlmHealth(localLlmResult.data);
    } else {
      setLocalLlmHealth(null);
    }
    return nextSystemInfo;
  };

  const exportDiagnosticsPackage = async () => {
    const result = await api.exportDiagnosticsPackage();
    if (!result.ok || !result.data) {
      throw new Error(result.error?.message ?? "无法导出诊断包");
    }
    return result.data;
  };

  const revealPath = async (path: string) => {
    const result = await api.showItemInFolder(path);
    if (!result.ok || result.data?.ok === false) {
      throw new Error(result.error?.message ?? result.data?.error ?? "无法打开所在位置");
    }
  };

  useEffect(() => {
    if (approvalSelectionContext !== "queue") return;
    setApprovalQueueCursor((current) => Math.min(current, Math.max(pendingApprovals.length - 1, 0)));
  }, [approvalSelectionContext, pendingApprovals.length]);

  const submitApprovalDecision = async (
    approvalId: string,
    decision: "approved" | "denied",
    note?: string
  ) => {
    const result = await api.submitApprovalDecision({ approvalId, decision, note });
    if (result.ok && result.data) {
      setApprovalRequests((current) => {
        return current.map((approval) => (approval.id === approvalId ? result.data as ApprovalRequest : approval));
      });
      setApprovalError(null);
      if (approvalSelectionContext === "queue") {
        const nextPendingCount = pendingApprovals.filter((approval) => approval.id !== approvalId).length;
        setApprovalQueueCursor((current) => Math.min(current, Math.max(nextPendingCount - 1, 0)));
        if (nextPendingCount === 0) {
          setIsApprovalOpen(false);
        }
      } else {
        setIsApprovalOpen(false);
      }
      return;
    }
    setApprovalError(result.error?.message ?? "审批提交失败，请刷新后重试");
    const approvalsResult = await api.listPendingApprovals();
    if (approvalsResult.ok && approvalsResult.data) {
      setApprovalRequests(approvalsResult.data);
    }
  };

  const openDocumentTool = (path = "", action: DocumentIntentAction = "summarize") => {
    setFileToolTab("document");
    setDocumentIntent(
      path
        ? {
            path,
            action,
            nonce: ++documentIntentNonce.current
          }
        : null
    );
    setActiveView("files");
  };

  const handleQuickSkill = (skill: OfficeQuickSkill) => {
    const starterManifest = taskStarterManifestById(skill.id);
    if (skill.kind === "action") {
      if (skill.action === "system-check") {
        void runComputerCheck();
      }
      return;
    }
    if (skill.kind === "view") {
      if (skill.id === "summarize-document") {
        setFileSearchError(null);
        openDocumentTool("", "summarize");
        return;
      }
      if (skill.id === "document-qa") {
        setFileSearchError(null);
        openDocumentTool("", "ask");
        return;
      }
      setActiveView(skill.view);
      return;
    }

    const manifestHint = starterManifest
      ? `\n\n任务向导：输入要求：${starterManifest.inputHint}；预检：${starterManifest.preflight.join(" / ")}；预期产出：${starterManifest.outputType}。`
      : "";
    setDraft(`${skill.prompt}${manifestHint}`);
  };

  const runComputerCheck = async () => {
    setActiveView("computer");
    setIsCheckingComputer(true);
    try {
      await refreshSystemInfo();
    } finally {
      setIsCheckingComputer(false);
    }
  };

  const handleReadinessAction = (item: HomeReadinessItem) => {
    if (item.id === "connection") {
      void refreshWorkspace();
      return;
    }
    if (item.id === "scope") {
      setFileToolTab("search");
      setActiveView("files");
      return;
    }
    if (item.id === "document") {
      setFileToolTab("document");
      setActiveView("files");
      return;
    }
    if (item.id === "privacy") {
      setSettingsIntent({ section: "privacy", nonce: ++settingsIntentNonce.current });
      setActiveView("settings");
      return;
    }
    setActiveView(item.targetView ?? "settings");
  };

  const handleTaskPilotAction = (task: TaskEvent | null, action: "open" | "approve" | "compose") => {
    if (action === "approve") {
      if (task?.id) setFocusedTaskId(task.id);
      const matchingApproval = selectedPendingApproval(pendingApprovals, task?.id ?? focusedTaskId);
      if (matchingApproval) {
        setApprovalError(null);
        setApprovalSelectionContext("task");
        setIsApprovalOpen(true);
        return;
      }
      setApprovalError("这个任务当前没有可确认的审批，请刷新后再试。");
      void api.listPendingApprovals().then((approvalsResult) => {
        if (approvalsResult.ok && approvalsResult.data) setApprovalRequests(approvalsResult.data);
      });
      return;
    }
    if (action === "open") {
      if (task?.id) setFocusedTaskId(task.id);
      setActiveView("agents");
      return;
    }
    setActiveView("home");
    window.setTimeout(() => {
      const input = document.querySelector<HTMLTextAreaElement>(".office-command-dock textarea");
      input?.focus();
    }, 0);
  };

  const requestCleanupApproval = async (scope: string) => {
    await sendMessage(
      `请基于这个文件范围生成清理确认任务：${scope}。先生成可清理项预览和审批请求；在我明确批准前不要移动或删除任何文件。`
    );
    void refreshTaskSnapshot();
  };

  return (
    <AppSurface
      api={api}
      activeView={activeView}
      connectionState={connectionState}
      isLoading={isLoading}
      messages={messages}
      recentReadableMessages={recentReadableMessages}
      tasks={tasks}
      systemInfo={systemInfo}
      plan={plan}
      agentConversations={agentConversations}
      safetyReview={safetyReview}
      auditEntries={auditEntries}
      settings={settings}
      backendStatus={backendStatus}
      realtimeStatus={realtimeStatus}
      localLlmHealth={localLlmHealth}
      llmHealth={llmHealth}
      llmCostSummary={llmCostSummary}
      intentSuggestions={intentSuggestions}
      fileResults={fileResults}
      fileSearchMeta={fileSearchMeta}
      fileSearchError={fileSearchError}
      isSearching={isSearching}
      fileToolTab={fileToolTab}
      documentIntent={documentIntent}
      focusedTaskId={focusedTaskId}
      browserSessions={browserSessions}
      browserEvents={browserEvents}
      browserHostSnapshot={browserHostSnapshot}
      activeBrowserSessionId={activeBrowserSessionId}
      browserError={browserError}
      draft={draft}
      heroSubmitting={heroSubmitting}
      heroSubmitError={heroSubmitError}
      homeReadinessItems={homeReadinessItems}
      homeTrustItems={homeTrustItems}
      pendingApproval={pendingApproval}
      pendingApprovals={pendingApprovals}
      approvalSelectionContext={approvalSelectionContext}
      approvalQueueCursor={approvalQueueCursor}
      isApprovalOpen={isApprovalOpen}
      approvalError={approvalError}
      isCheckingComputer={isCheckingComputer}
      settingsIntent={settingsIntent}
      onViewChange={setActiveView}
      onRefreshWorkspace={() => void refreshWorkspace()}
      onOpenApprovals={() => {
        setApprovalError(null);
        setApprovalSelectionContext("queue");
        setApprovalQueueCursor(0);
        setFocusedTaskId(null);
        setIsApprovalOpen(true);
      }}
      onDraftChange={setDraft}
      onSubmitHeroPrompt={submitHeroPrompt}
      onQuickSkill={handleQuickSkill}
      onReadinessAction={handleReadinessAction}
      onTaskPilotAction={handleTaskPilotAction}
      onSendMessage={sendMessage}
      onExecuteSuggestion={executeSuggestion}
      onSearchFiles={searchFiles}
      onClearFileResults={() => {
        setFileResults([]);
        setFileSearchMeta(null);
        setFileSearchError(null);
      }}
      onSaveSettings={saveSettings}
      onFileToolChange={setFileToolTab}
      onUseDocument={openDocumentTool}
      onDocumentIntentHandled={() => setDocumentIntent(null)}
      onRequestCleanupApproval={requestCleanupApproval}
      onRefreshSystemInfo={runComputerCheck}
      onExportDiagnostics={exportDiagnosticsPackage}
      onRevealPath={revealPath}
      onOpenWindowsSettings={openWindowsSettings}
      onStartBackend={async () => setBackendStatus(await api.startBackend())}
      onStopBackend={async () => setBackendStatus(await api.stopBackend())}
      onLocalLlmHealthChange={setLocalLlmHealth}
      onBrowserSessionsChange={setBrowserSessions}
      onBrowserEventsChange={setBrowserEvents}
      onBrowserHostSnapshotChange={setBrowserHostSnapshot}
      onActiveBrowserSessionChange={setActiveBrowserSessionId}
      onBrowserErrorChange={setBrowserError}
      onOpenTaskApproval={() => {
        setApprovalSelectionContext("task");
        setIsApprovalOpen(true);
      }}
      onCloseApproval={() => {
        setApprovalError(null);
        setIsApprovalOpen(false);
      }}
      onPreviousApproval={() => setApprovalQueueCursor((current) => Math.max(0, current - 1))}
      onNextApproval={() => setApprovalQueueCursor((current) => Math.min(pendingApprovals.length - 1, current + 1))}
      onApprovalDecision={submitApprovalDecision}
    />
  );
}
