import { useCallback, useEffect, useRef } from "react";

import type {
  AuditLogEntry,
  BackendStatus,
  BrowserHostSnapshot,
  BrowserSession
} from "../../shared/types";
import type { ChatMessage, IntentSuggestion } from "../../shared/catalogTypes";
import type { SystemInfo } from "../../shared/systemTypes";
import type {
  AgentConversation,
  ApprovalRequest,
  Plan,
  SafetyReview,
  TaskEvent
} from "../../shared/executionTypes";
import type { ContextUsage, LLMCostSummary, LLMHealthStatus } from "../../shared/llmContextTypes";
import type { LocalLLMHealth } from "../../shared/localModelTypes";
import type { AppSettings } from "../../shared/settingsTypes";
import { preserveStreamedRunConversations as preserveStreamedRunConversationsFromEvents } from "../events";
import type { LengrvisApiClient } from "../lib/apiClient";
import type { AssistantMode } from "../store";
import { mergeTaskSnapshots, readableError, requiresLocalLlmHealth } from "../appViewModel";

interface MutableRefValue<T> {
  current: T;
}

interface UseWorkspaceRefreshOptions {
  api: LengrvisApiClient;
  backendStatusRef: MutableRefValue<BackendStatus>;
  mode: AssistantMode;
  activeBrowserSessionId: string | null;
  setMessages: (messages: ChatMessage[]) => void;
  setTasks: (tasks: TaskEvent[]) => void;
  setPlan: (plan: Plan) => void;
  setAgentConversations: (
    conversations: AgentConversation[] | ((current: AgentConversation[]) => AgentConversation[])
  ) => void;
  setSafetyReview: (review: SafetyReview) => void;
  setApprovalRequests: (approvalRequests: ApprovalRequest[]) => void;
  setSettings: (settings: AppSettings) => void;
  setMode: (mode: AssistantMode) => void;
  setLlmHealth: (health: LLMHealthStatus) => void;
  setLlmCostSummary: (summary: LLMCostSummary) => void;
  setContextUsage: (usage: ContextUsage) => void;
  setAuditEntries: (entries: AuditLogEntry[]) => void;
  setSystemInfo: (info: SystemInfo) => void;
  setIntentSuggestions: (suggestions: IntentSuggestion[]) => void;
  setBrowserSessions: (sessions: BrowserSession[]) => void;
  setBrowserHostSnapshot: (snapshot: BrowserHostSnapshot | null) => void;
  setActiveBrowserSessionId: (sessionId: string | null) => void;
  setBackendStatus: (status: BackendStatus) => void;
  setLocalLlmHealth: (health: LocalLLMHealth | null) => void;
  setIsLoading: (isLoading: boolean) => void;
  setHasLoadedBackendTasks: (hasLoaded: boolean) => void;
}

export function useWorkspaceRefresh({
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
}: UseWorkspaceRefreshOptions) {
  const modeRef = useRef(mode);
  const activeBrowserSessionIdRef = useRef(activeBrowserSessionId);
  const workspaceAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  useEffect(() => {
    activeBrowserSessionIdRef.current = activeBrowserSessionId;
  }, [activeBrowserSessionId]);

  const refreshWorkspace = useCallback(async () => {
    workspaceAbortRef.current?.abort();
    const controller = new AbortController();
    workspaceAbortRef.current = controller;
    const { signal } = controller;
    await api.beginBatch("workspace-refresh");
    setIsLoading(true);

    try {
      const currentStatus = await api.getBackendStatus();
      if (signal.aborted) return;
      setBackendStatus(currentStatus);

      const [
        chatResult,
        runsResult,
        legacyTasksResult,
        planResult,
        agentsResult,
        safetyResult,
        approvalsResult,
        settingsResult,
        llmHealthResult,
        llmCostResult,
        contextUsageResult,
        auditResult,
        systemResult,
        suggestionsResult,
        browserSessionsResult,
        browserHostResult
      ] = await Promise.allSettled([
        api.listChatMessages(),
        api.listRuns(),
        api.listTaskTimeline(),
        api.getCurrentPlan(),
        api.listAgentConversations(),
        api.getSafetyReview(),
        api.listPendingApprovals(),
        api.getSettings(),
        api.getLlmHealth(),
        api.getLlmCostSummary(),
        api.getContextUsage(),
        api.listAuditLogs(),
        api.getSystemInfo(),
        api.listIntentSuggestions(),
        api.listBrowserSessions(),
        api.getBrowserHostSnapshot()
      ]);

      if (signal.aborted) return;
      if (chatResult.status === "fulfilled" && chatResult.value.ok && chatResult.value.data) setMessages(chatResult.value.data);
      const initialRunTasks = runsResult.status === "fulfilled" && runsResult.value.ok ? runsResult.value.data : undefined;
      const initialLegacyTasks =
        legacyTasksResult.status === "fulfilled" && legacyTasksResult.value.ok ? legacyTasksResult.value.data : undefined;
      if (initialRunTasks || initialLegacyTasks) {
        setTasks(mergeTaskSnapshots(initialRunTasks ?? [], initialLegacyTasks ?? []));
      }
      if (runsResult.status === "fulfilled" || legacyTasksResult.status === "fulfilled") {
        setHasLoadedBackendTasks(true);
      }
      if (planResult.status === "fulfilled" && planResult.value.ok && planResult.value.data) setPlan(planResult.value.data);
      if (agentsResult.status === "fulfilled" && agentsResult.value.ok && agentsResult.value.data) {
        setAgentConversations((current) => preserveStreamedRunConversationsFromEvents(current, agentsResult.value.data ?? []));
      }
      if (safetyResult.status === "fulfilled" && safetyResult.value.ok && safetyResult.value.data) setSafetyReview(safetyResult.value.data);
      if (approvalsResult.status === "fulfilled" && approvalsResult.value.ok && approvalsResult.value.data) {
        setApprovalRequests(approvalsResult.value.data);
      }
      if (settingsResult.status === "fulfilled" && settingsResult.value.ok && settingsResult.value.data) {
        setSettings(settingsResult.value.data);
        setMode(settingsResult.value.data.mode);
      }
      if (llmHealthResult.status === "fulfilled" && llmHealthResult.value.ok && llmHealthResult.value.data) {
        setLlmHealth(llmHealthResult.value.data);
      }
      if (llmCostResult.status === "fulfilled" && llmCostResult.value.ok && llmCostResult.value.data) {
        setLlmCostSummary(llmCostResult.value.data);
      }
      if (contextUsageResult.status === "fulfilled" && contextUsageResult.value.ok && contextUsageResult.value.data) {
        setContextUsage(contextUsageResult.value.data);
      }
      if (auditResult.status === "fulfilled" && auditResult.value.ok && auditResult.value.data) setAuditEntries(auditResult.value.data);
      if (systemResult.status === "fulfilled" && systemResult.value.ok && systemResult.value.data) setSystemInfo(systemResult.value.data);
      if (suggestionsResult.status === "fulfilled" && suggestionsResult.value.ok && suggestionsResult.value.data) {
        setIntentSuggestions(suggestionsResult.value.data);
      }
      if (browserSessionsResult.status === "fulfilled" && browserSessionsResult.value.ok && browserSessionsResult.value.data) {
        setBrowserSessions(browserSessionsResult.value.data);
        if (!activeBrowserSessionIdRef.current && browserSessionsResult.value.data[0]) {
          setActiveBrowserSessionId(browserSessionsResult.value.data[0].id);
        }
      }
      if (browserHostResult.status === "fulfilled") {
        setBrowserHostSnapshot(browserHostResult.value);
        if (browserHostResult.value.activeSessionId) {
          setActiveBrowserSessionId(browserHostResult.value.activeSessionId);
        }
      }

      const currentMode =
        settingsResult.status === "fulfilled" && settingsResult.value.ok && settingsResult.value.data
          ? settingsResult.value.data.mode
          : modeRef.current;
      if (signal.aborted) return;
      if (requiresLocalLlmHealth(currentMode)) {
        const localLlmResult = await api.getLocalLlmHealth();
        if (signal.aborted) return;
        if (localLlmResult.ok && localLlmResult.data) {
          setLocalLlmHealth(localLlmResult.data);
        } else {
          setLocalLlmHealth({
            available: false,
            selectedBackend: null,
            probeOrder: ["ollama", "lmstudio", "llamacpp"],
            error: localLlmResult.error?.message ?? "无法读取本地模型健康状态"
          });
        }
      } else {
        setLocalLlmHealth(null);
      }
    } catch (error) { // broad-exception-boundary
      if (signal.aborted) return;
      setBackendStatus({
        ...backendStatusRef.current,
        state: "error",
        message: readableError(error, "Workspace refresh failed"),
        lastCheckedAt: new Date().toISOString()
      });
    } finally {
      api.endBatch("workspace-refresh");
      if (!signal.aborted) {
        setIsLoading(false);
      }
    }
  }, [api]);

  useEffect(() => {
    void refreshWorkspace();
    return () => {
      workspaceAbortRef.current?.abort();
      api.abortInflight("workspace-refresh");
    };
  }, [api, refreshWorkspace]);

  return refreshWorkspace;
}
