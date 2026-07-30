import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import type {
  ApiResponse,
  FileSearchMeta
} from "../shared/types";
import type { SystemInfo } from "../shared/systemTypes";
import type { ApprovalRequest, TaskEvent, TaskPilotAction } from "../shared/executionTypes";
import type { AppSettings } from "../shared/settingsTypes";
import type { DocumentIntentAction, FileToolTab } from "./components/FileSearchPanel";
import { AppSurface } from "./app/AppSurface";
import { useAppStoreSnapshot } from "./app/useAppStoreSnapshot";
import { useHomeSignals } from "./app/useHomeSignals";
import { useRealtimeStatusHandlers } from "./app/useRealtimeStatusHandlers";
import { useTaskSubmission } from "./app/useTaskSubmission";
import { useTaskRealtimeSync } from "./app/useTaskRealtimeSync";
import { useWorkspaceRefresh } from "./app/useWorkspaceRefresh";
import {
  type HomeReadinessItem,
  type OfficeQuickSkill
} from "./features/office";
import { LengrvisApiClient, type RealtimeConnectionStatus } from "./lib/apiClient";
import type { AssistantMode } from "./store";
import {
  readableError,
  recentReadableChatMessages,
  requiresLocalLlmHealth,
  approvalAuthorizationIsFresh,
  selectedPendingApproval,
  type RealtimeBadMessageNotice
} from "./appViewModel";

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
  const [approvalSelectionContext, setApprovalSelectionContext] = useState<"task" | "queue">("task");
  const [approvalQueueCursor, setApprovalQueueCursor] = useState(0);
  const [realtimeStatus, setRealtimeStatus] = useState<RealtimeConnectionStatus | null>(null);
  const backendStatusRef = useRef(backendStatus);
  const realtimeBadMessageNotice = useRef<RealtimeBadMessageNotice>({
    count: 0,
    messageId: `realtime-bad-message-${crypto.randomUUID()}`,
    samples: []
  });

  const pendingApprovals = useMemo(
    () => approvalRequests.filter((approval) => approval.status === "pending" && approvalAuthorizationIsFresh(approval)),
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

  const {
    draft,
    setDraft,
    heroSubmitting,
    heroSubmitError,
    sendMessage,
    executeSuggestion,
    submitHeroPrompt,
    requestCleanupApproval
  } = useTaskSubmission({
    api,
    mode,
    backendStatusRef,
    chatStartedTaskIds,
    setMessages,
    setTasks,
    setFocusedTaskId,
    setBackendStatus,
    refreshTaskSnapshot
  });

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
      const needsLocalHealth = requiresLocalLlmHealth(result.data.mode);
      const [localHealthResult, llmResult, costResult] = await Promise.allSettled([
        needsLocalHealth ? api.getLocalLlmHealth() : Promise.resolve(null),
        api.getLlmHealth(),
        api.getLlmCostSummary()
      ]);
      if (!needsLocalHealth) {
        setLocalLlmHealth(null);
      } else if (
        localHealthResult.status === "fulfilled" &&
        localHealthResult.value?.ok &&
        localHealthResult.value.data
      ) {
        setLocalLlmHealth(localHealthResult.value.data);
      }
      if (llmResult.status === "fulfilled" && llmResult.value.ok && llmResult.value.data) {
        setLlmHealth(llmResult.value.data);
      }
      if (costResult.status === "fulfilled" && costResult.value.ok && costResult.value.data) {
        setLlmCostSummary(costResult.value.data);
      }
    }
  };

  const openWindowsSettings = async (uri: string) => {
    try {
      const result = await api.openWindowsSettings(uri);
      if (result.ok && result.data?.ok !== false && result.data?.opened !== false) {
        return;
      }
      throw new Error("open_settings_failed");
    } catch { // broad-exception-boundary
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
      throw new Error("无法打开 Windows 设置，请稍后重试。");
    } finally {
      void refreshWorkspace().catch(() => undefined); // best-effort-refresh
    }
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
      const [localLlmResult] = await Promise.allSettled([api.getLocalLlmHealth()]);
      if (localLlmResult.status === "fulfilled" && localLlmResult.value.ok && localLlmResult.value.data) {
        setLocalLlmHealth(localLlmResult.value.data);
      }
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
    try {
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
    } catch (error) { // broad-exception-boundary
      setApprovalError(readableError(error, "审批提交失败，请刷新后重试"));
    }
    await refreshPendingApprovals();
  };

  const refreshPendingApprovals = async () => {
    try {
      const approvalsResult = await api.listPendingApprovals();
      if (approvalsResult.ok && approvalsResult.data) {
        setApprovalRequests(approvalsResult.data);
      } else {
        setApprovalError(approvalsResult.error?.message ?? "无法刷新审批队列，请稍后重试");
      }
    } catch (error) { // broad-exception-boundary
      setApprovalError(readableError(error, "无法刷新审批队列，请稍后重试"));
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

    setDraft(skill.prompt);
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

  const handleTaskPilotAction = async (task: TaskEvent | null, action: TaskPilotAction) => {
    const targetTaskId = task?.sourceTaskId ?? task?.id ?? null;
    if (action === "pause" || action === "resume" || action === "stop" || action === "cancel") {
      if (!targetTaskId) return;
      const response = action === "pause"
        ? await api.pauseTask(targetTaskId)
        : action === "resume"
          ? await api.resumeTask(targetTaskId)
          : await api.cancelTask(targetTaskId);
      if (!response.ok) throw new Error(response.error?.message ?? "任务控制失败");
      setFocusedTaskId(targetTaskId);
      await refreshWorkspace();
      return;
    }
    if (action === "approve") {
      if (targetTaskId) setFocusedTaskId(targetTaskId);
      const matchingApproval = selectedPendingApproval(pendingApprovals, targetTaskId ?? focusedTaskId);
      if (matchingApproval) {
        setApprovalError(null);
        setApprovalSelectionContext("task");
        setIsApprovalOpen(true);
        return;
      }
      setApprovalError("这个任务当前没有可确认的审批，请刷新后再试。");
      void api.listPendingApprovals()
        .then((approvalsResult) => {
          if (approvalsResult.ok && approvalsResult.data) {
            setApprovalRequests(approvalsResult.data);
          } else {
            setApprovalError(approvalsResult.error?.message ?? "无法刷新审批队列，请稍后重试。");
          }
        })
        .catch((error: unknown) => {
          setApprovalError(readableError(error, "无法刷新审批队列，请稍后重试。"));
        });
      return;
    }
    if (action === "open") {
      if (targetTaskId) setFocusedTaskId(targetTaskId);
      setActiveView("agents");
      return;
    }
    setActiveView("home");
    window.setTimeout(() => {
      const input = document.querySelector<HTMLTextAreaElement>(".office-command-dock textarea");
      input?.focus();
    }, 0);
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
