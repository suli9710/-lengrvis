import {
  BookOpenText,
  FileSearch,
  Laptop,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState
} from "react";

import type {
  AgentConversation,
  AppSettings,
  ApprovalRequest,
  AuditLogEntry,
  ChatMessage,
  ContextUsage,
  IntentSuggestion,
  LLMCostSummary,
  LLMHealthStatus,
  LocalLLMHealth,
  Plan,
  SafetyReview,
  TaskEvent
} from "../shared/types";
import { AgentConversationPanel } from "./components/AgentConversationPanel";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { AuditLogPanel } from "./components/AuditLogPanel";
import { BrowserActivityPanel } from "./components/BrowserActivityPanel";
import { ChatPanel } from "./components/ChatPanel";
import { FileSearchPanel } from "./components/FileSearchPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import { PlanViewer } from "./components/PlanViewer";
import { SafetyReviewPanel } from "./components/SafetyReviewPanel";
import { SchedulePanel } from "./components/SchedulePanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { SystemInfoPanel } from "./components/SystemInfoPanel";
import { TaskTimeline } from "./components/TaskTimeline";
import { SkillsView } from "./views/SkillsView";
import { sampleFileResults } from "./data/mockData";
import {
  latestStreamableTaskId as latestStreamableTaskIdFromEvents,
  mergeRunStreamEventIntoConversations,
  preserveStreamedRunConversations as preserveStreamedRunConversationsFromEvents
} from "./events";
import {
  inferActiveOfficeAgentId,
  OfficeScene,
  officeAgents,
  type OfficeQuickSkill
} from "./features/office";
import { ShellFrame } from "./features/shell";
import { MavrisApiClient } from "./lib/apiClient";
import { useMavrisStore, type AssistantMode, type ViewKey } from "./store";

const quickSkills: OfficeQuickSkill[] = [
  { icon: FileSearch, title: "查找大文件", prompt: "找出这台电脑上最大的文件，并建议哪些可以安全清理。" },
  { icon: BookOpenText, title: "总结文档", prompt: "总结 sample_contract.txt 的主要内容。" },
  { icon: Laptop, title: "检查电脑", prompt: "检查这台电脑，并告诉我有哪些需要注意的地方。" }
];

export function App() {
  const api = useMemo(() => new MavrisApiClient(), []);
  const messages = useMavrisStore((state) => state.messages);
  const setMessages = useMavrisStore((state) => state.setMessages);
  const tasks = useMavrisStore((state) => state.tasks);
  const setTasks = useMavrisStore((state) => state.setTasks);
  const plan = useMavrisStore((state) => state.plan);
  const setPlan = useMavrisStore((state) => state.setPlan);
  const agentConversations = useMavrisStore((state) => state.agentConversations);
  const setAgentConversations = useMavrisStore((state) => state.setAgentConversations);
  const safetyReview = useMavrisStore((state) => state.safetyReview);
  const setSafetyReview = useMavrisStore((state) => state.setSafetyReview);
  const approvalRequests = useMavrisStore((state) => state.approvalRequests);
  const setApprovalRequests = useMavrisStore((state) => state.setApprovalRequests);
  const fileResults = useMavrisStore((state) => state.fileResults);
  const setFileResults = useMavrisStore((state) => state.setFileResults);
  const settings = useMavrisStore((state) => state.settings);
  const setSettings = useMavrisStore((state) => state.setSettings);
  const auditEntries = useMavrisStore((state) => state.auditEntries);
  const setAuditEntries = useMavrisStore((state) => state.setAuditEntries);
  const systemInfo = useMavrisStore((state) => state.systemInfo);
  const setSystemInfo = useMavrisStore((state) => state.setSystemInfo);
  const intentSuggestions = useMavrisStore((state) => state.intentSuggestions);
  const setIntentSuggestions = useMavrisStore((state) => state.setIntentSuggestions);
  const backendStatus = useMavrisStore((state) => state.backendStatus);
  const setBackendStatus = useMavrisStore((state) => state.setBackendStatus);
  const localLlmHealth = useMavrisStore((state) => state.localLlmHealth);
  const setLocalLlmHealth = useMavrisStore((state) => state.setLocalLlmHealth);
  const llmHealth = useMavrisStore((state) => state.llmHealth);
  const setLlmHealth = useMavrisStore((state) => state.setLlmHealth);
  const llmCostSummary = useMavrisStore((state) => state.llmCostSummary);
  const setLlmCostSummary = useMavrisStore((state) => state.setLlmCostSummary);
  const contextUsage = useMavrisStore((state) => state.contextUsage);
  const setContextUsage = useMavrisStore((state) => state.setContextUsage);
  const isLoading = useMavrisStore((state) => state.isLoading);
  const setIsLoading = useMavrisStore((state) => state.setIsLoading);
  const isSearching = useMavrisStore((state) => state.isSearching);
  const setIsSearching = useMavrisStore((state) => state.setIsSearching);
  const isApprovalOpen = useMavrisStore((state) => state.isApprovalOpen);
  const setIsApprovalOpen = useMavrisStore((state) => state.setIsApprovalOpen);
  const approvalError = useMavrisStore((state) => state.approvalError);
  const setApprovalError = useMavrisStore((state) => state.setApprovalError);
  const mode = useMavrisStore((state) => state.mode);
  const setMode = useMavrisStore((state) => state.setMode);
  const activeView = useMavrisStore((state) => state.activeView);
  const setActiveView = useMavrisStore((state) => state.setActiveView);
  const focusedTaskId = useMavrisStore((state) => state.focusedTaskId);
  const setFocusedTaskId = useMavrisStore((state) => state.setFocusedTaskId);
  const browserSessions = useMavrisStore((state) => state.browserSessions);
  const setBrowserSessions = useMavrisStore((state) => state.setBrowserSessions);
  const browserEvents = useMavrisStore((state) => state.browserEvents);
  const setBrowserEvents = useMavrisStore((state) => state.setBrowserEvents);
  const browserHostSnapshot = useMavrisStore((state) => state.browserHostSnapshot);
  const setBrowserHostSnapshot = useMavrisStore((state) => state.setBrowserHostSnapshot);
  const activeBrowserSessionId = useMavrisStore((state) => state.activeBrowserSessionId);
  const setActiveBrowserSessionId = useMavrisStore((state) => state.setActiveBrowserSessionId);
  const browserError = useMavrisStore((state) => state.browserError);
  const setBrowserError = useMavrisStore((state) => state.setBrowserError);
  const [draft, setDraft] = useState("");

  const pendingApproval = approvalRequests.find((approval) => approval.status === "pending") ?? null;
  const connectionState = backendStatus.state === "running" ? "online" : isLoading ? "checking" : "offline";
  const activeOfficeAgentId = useMemo(
    () => inferActiveOfficeAgentId(tasks, plan, agentConversations, safetyReview.status),
    [agentConversations, plan, safetyReview.status, tasks]
  );
  const safetyAlert = safetyReview.status === "needs_review" || safetyReview.status === "blocked";
  const latestTaskId = useMemo(() => latestStreamableTaskIdFromEvents(tasks), [tasks]);

  const refreshWorkspace = useCallback(async () => {
    setIsLoading(true);

    const currentStatus = await api.getBackendStatus();
    setBackendStatus(currentStatus);

    const [
      chatResult,
      tasksResult,
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

    if (chatResult.status === "fulfilled" && chatResult.value.ok && chatResult.value.data) setMessages(chatResult.value.data);
    if (tasksResult.status === "fulfilled" && tasksResult.value.ok && tasksResult.value.data) setTasks(tasksResult.value.data);
    if (planResult.status === "fulfilled" && planResult.value.ok && planResult.value.data) setPlan(planResult.value.data);
    if (agentsResult.status === "fulfilled" && agentsResult.value.ok && agentsResult.value.data) {
      setAgentConversations((current) => preserveStreamedRunConversationsFromEvents(current, agentsResult.value.data ?? []));
    }
    if (safetyResult.status === "fulfilled" && safetyResult.value.ok && safetyResult.value.data) setSafetyReview(safetyResult.value.data);
    if (approvalsResult.status === "fulfilled" && approvalsResult.value.ok && approvalsResult.value.data) setApprovalRequests(approvalsResult.value.data);
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
      if (!activeBrowserSessionId && browserSessionsResult.value.data[0]) {
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
        : mode;
    if (requiresLocalLlmHealth(currentMode)) {
      const localLlmResult = await api.getLocalLlmHealth();
      if (localLlmResult.ok && localLlmResult.data) {
        setLocalLlmHealth(localLlmResult.data);
      } else {
        setLocalLlmHealth({
          available: false,
          selectedBackend: null,
          probeOrder: ["ollama", "lmstudio", "llamacpp"],
          error: localLlmResult.error?.message ?? "Unable to read local LLM health"
        });
      }
    } else {
      setLocalLlmHealth(null);
    }

    setIsLoading(false);
  }, [activeBrowserSessionId, api, mode]);

  useEffect(() => {
    void refreshWorkspace();
  }, [refreshWorkspace]);

  const sendMessage = async (content: string) => {
    const userMessage: ChatMessage = {
      id: `local-${crypto.randomUUID()}`,
      role: "user",
      author: "你",
      content,
      createdAt: new Date().toISOString(),
      status: "sent"
    };

    setMessages((current) => [...current, userMessage]);
    let result = await api.startRun({ content, mode });
    if (!result.ok) {
      result = await api.sendChat({ content, mode });
    }

    const response = result.data;
    if (result.ok && response) {
      setMessages((current) => [...current, response.message]);
      if (response.taskUpdates?.length) {
        setTasks(response.taskUpdates);
        void refreshTaskSnapshot();
      }
      return;
    }

    setMessages((current) => [
      ...current,
      {
        id: `local-${crypto.randomUUID()}`,
        role: "assistant",
        author: "Mavris",
        content: result.error?.message ?? "Mavris 暂时不可用，请稍后再试。",
        createdAt: new Date().toISOString(),
        status: "failed"
      }
    ]);
  };

  const executeSuggestion = async (suggestion: IntentSuggestion) => {
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
        setTasks(response.taskUpdates);
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
        author: "Mavris",
        content: result.error?.message ?? "建议任务启动失败，请稍后再试。",
        createdAt: new Date().toISOString(),
        status: "failed"
      }
    ]);
  };

  const submitHeroPrompt = async () => {
    const value = draft.trim();
    if (!value) return;
    setDraft("");
    await sendMessage(value);
  };

  const searchFiles = async (query: string) => {
    setIsSearching(true);
    const result = await api.searchFiles(query);
    if (result.ok && result.data) {
      setFileResults(result.data);
    } else if (query) {
      setFileResults(sampleFileResults.filter((item) => item.path.toLowerCase().includes(query.toLowerCase())));
    } else {
      setFileResults(sampleFileResults);
    }
    setIsSearching(false);
  };

  const saveSettings = async (nextSettings: AppSettings) => {
    const previousSettings = settings;
    const previousMode = mode;
    setSettings(nextSettings);
    setMode(nextSettings.mode);
    const result = await api.saveSettings(nextSettings);
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

  const refreshSystemInfo = async () => {
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
    if (systemResult.status === "fulfilled" && systemResult.value.ok && systemResult.value.data) setSystemInfo(systemResult.value.data);
    if (requiresLocalLlmHealth(mode)) {
      const localLlmResult = await api.getLocalLlmHealth();
      if (localLlmResult.ok && localLlmResult.data) setLocalLlmHealth(localLlmResult.data);
    } else {
      setLocalLlmHealth(null);
    }
  };

  const refreshTaskSnapshot = useCallback(async () => {
    const [runsResult, legacyTasksResult, planResult, agentsResult, safetyResult, approvalsResult] = await Promise.allSettled([
      api.listRuns(),
      api.listTaskTimeline(),
      api.getCurrentPlan(),
      api.listAgentConversations(),
      api.getSafetyReview(),
      api.listPendingApprovals()
    ]);
    const runTasks = runsResult.status === "fulfilled" && runsResult.value.ok ? runsResult.value.data : undefined;
    const legacyTasks =
      legacyTasksResult.status === "fulfilled" && legacyTasksResult.value.ok ? legacyTasksResult.value.data : undefined;
    if (runTasks || legacyTasks) {
      setTasks(mergeTaskSnapshots(runTasks ?? [], legacyTasks ?? []));
    }
    if (planResult.status === "fulfilled" && planResult.value.ok && planResult.value.data) setPlan(planResult.value.data);
    if (agentsResult.status === "fulfilled" && agentsResult.value.ok && agentsResult.value.data) {
      setAgentConversations((current) => preserveStreamedRunConversationsFromEvents(current, agentsResult.value.data ?? []));
    }
    if (safetyResult.status === "fulfilled" && safetyResult.value.ok && safetyResult.value.data) setSafetyReview(safetyResult.value.data);
    if (approvalsResult.status === "fulfilled" && approvalsResult.value.ok && approvalsResult.value.data) setApprovalRequests(approvalsResult.value.data);
  }, [api]);

  useEffect(() => {
    const hasRunningTask = tasks.some(
      (task) => task.state === "running" || task.state === "queued" || task.state === "blocked"
    );
    if (!hasRunningTask) return;
    const intervalId = window.setInterval(() => {
      void refreshTaskSnapshot();
    }, 2500);
    return () => window.clearInterval(intervalId);
  }, [refreshTaskSnapshot, tasks]);

  useEffect(() => {
    if (!latestTaskId) return;

    const unsubscribe = api.subscribeRunEvents(latestTaskId, {
      onMessage: (event) => {
        if (event.type === "run_event") {
          setAgentConversations((current) => mergeRunStreamEventIntoConversations(current, latestTaskId, event));
          void refreshTaskSnapshot();
        }
      }
    });

    return () => {
      unsubscribe();
    };
  }, [api, latestTaskId, refreshTaskSnapshot]);

  useEffect(() => {
    const unsubscribe = window.mavris?.notifications.onOpenTask((taskId) => {
      setFocusedTaskId(taskId);
      setActiveView("agents");
      void refreshTaskSnapshot();
    });

    return () => {
      unsubscribe?.();
    };
  }, [refreshTaskSnapshot]);

  const submitApprovalDecision = async (
    approvalId: string,
    decision: "approved" | "denied",
    note?: string
  ) => {
    const result = await api.submitApprovalDecision({ approvalId, decision, note });
    if (result.ok && result.data) {
      setApprovalRequests((current) =>
        current.map((approval) => (approval.id === approvalId ? result.data as ApprovalRequest : approval))
      );
      setApprovalError(null);
      setIsApprovalOpen(false);
      return;
    }
    setApprovalError(result.error?.message ?? "审批提交失败，请刷新后重试");
    const approvalsResult = await api.listPendingApprovals();
    if (approvalsResult.ok && approvalsResult.data) {
      setApprovalRequests(approvalsResult.data);
    }
  };

  return (
    <>
      <ShellFrame
        activeView={activeView}
        connectionState={connectionState}
        isLoading={isLoading}
        onViewChange={setActiveView}
        onRefresh={() => void refreshWorkspace()}
        onOpenApprovals={() => setIsApprovalOpen(true)}
        hasPendingApproval={Boolean(pendingApproval)}
      >

        {activeView === "home" ? (
          <section className="marvis-home">
            <OfficeScene
              agents={officeAgents}
              draft={draft}
              onDraftChange={setDraft}
              onSubmitPrompt={submitHeroPrompt}
              onAgentSelect={(prompt) => setDraft(prompt)}
              activeAgentId={activeOfficeAgentId}
              recentTasks={tasks}
              quickSkills={quickSkills}
              onQuickSkill={(prompt) => setDraft(prompt)}
              safetyAlert={safetyAlert}
            />
          </section>
        ) : null}

        {activeView === "chat" ? (
          <section className="conversation-view">
            <ChatPanel
              messages={messages}
              connectionState={connectionState}
              onSend={sendMessage}
              onExecuteSuggestion={executeSuggestion}
              suggestions={intentSuggestions}
              autoFocus
            />
            <div className="conversation-side">
              <PlanViewer plan={plan} />
              <TaskTimeline tasks={tasks} api={api} focusedTaskId={focusedTaskId} />
            </div>
          </section>
        ) : null}

        {activeView === "files" ? (
          <section className="detail-grid">
            <FileSearchPanel results={fileResults} isSearching={isSearching} onSearch={searchFiles} api={api} />
            <ChatPanel messages={messages} connectionState={connectionState} onSend={sendMessage} />
          </section>
        ) : null}

        {activeView === "computer" ? (
          <section className="detail-grid">
            <SystemInfoPanel info={systemInfo} onRefresh={refreshSystemInfo} onOpenSettings={openWindowsSettings} />
            <PlanViewer plan={plan} />
          </section>
        ) : null}

        {activeView === "agents" ? (
          <section className="detail-grid">
            <AgentConversationPanel conversations={agentConversations} />
            <SchedulePanel api={api} />
            <TaskTimeline tasks={tasks} api={api} focusedTaskId={focusedTaskId} />
            <PlanViewer plan={plan} />
          </section>
        ) : null}

        {activeView === "browser" ? (
          <section className="browser-view">
            <BrowserActivityPanel
              api={api}
              sessions={browserSessions}
              events={browserEvents}
              hostSnapshot={browserHostSnapshot}
              activeSessionId={activeBrowserSessionId}
              error={browserError}
              onSessionsChange={setBrowserSessions}
              onEventsChange={setBrowserEvents}
              onHostSnapshotChange={setBrowserHostSnapshot}
              onActiveSessionChange={setActiveBrowserSessionId}
              onErrorChange={setBrowserError}
            />
          </section>
        ) : null}

        {activeView === "memories" ? (
          <section className="detail-grid">
            <MemoryPanel api={api} />
          </section>
        ) : null}

        {activeView === "safety" ? (
          <section className="detail-grid">
            <SafetyReviewPanel review={safetyReview} onOpenApproval={() => setIsApprovalOpen(true)} />
            <AuditLogPanel entries={auditEntries} />
          </section>
        ) : null}

        {activeView === "settings" ? (
          <section className="detail-grid">
            <SettingsPanel
              settings={settings}
              backendStatus={backendStatus}
              localLlmHealth={localLlmHealth}
              llmHealth={llmHealth}
              llmCostSummary={llmCostSummary}
              onSave={saveSettings}
              onStartBackend={async () => setBackendStatus(await api.startBackend())}
              onStopBackend={async () => setBackendStatus(await api.stopBackend())}
              api={api}
            />
            <SkillsView api={api} />
            <SystemInfoPanel info={systemInfo} onRefresh={refreshSystemInfo} onOpenSettings={openWindowsSettings} />
          </section>
        ) : null}

      </ShellFrame>

      <ApprovalDialog
        approval={pendingApproval}
        isOpen={isApprovalOpen}
        error={approvalError}
        onClose={() => {
          setApprovalError(null);
          setIsApprovalOpen(false);
        }}
        onDecision={submitApprovalDecision}
      />
    </>
  );
}

function mergeTaskSnapshots(runTasks: TaskEvent[], legacyTasks: TaskEvent[]): TaskEvent[] {
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

function requiresLocalLlmHealth(mode: AssistantMode): boolean {
  return mode === "privacy" || mode === "hybrid";
}
