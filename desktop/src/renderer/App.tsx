import {
  BookOpenText,
  FileSearch,
  FolderOpen,
  Laptop,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  lazy,
  useMemo,
  useRef,
  Suspense,
  useState
} from "react";

import type {
  AgentConversation,
  AppSettings,
  ApprovalRequest,
  AuditLogEntry,
  ChatMessage,
  ContextUsage,
  FileSearchMeta,
  IntentSuggestion,
  LLMCostSummary,
  LLMHealthStatus,
  LocalLLMHealth,
  Plan,
  SafetyReview,
  SystemInfo,
  TaskEvent
} from "../shared/types";
import { AgentConversationPanel } from "./components/AgentConversationPanel";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { AuditLogPanel } from "./components/AuditLogPanel";
import { ChatPanel } from "./components/ChatPanel";
import type { DocumentIntentAction, FileToolTab } from "./components/FileSearchPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import { PlanViewer } from "./components/PlanViewer";
import { SafetyReviewPanel } from "./components/SafetyReviewPanel";
import { SchedulePanel } from "./components/SchedulePanel";
import { SystemInfoPanel } from "./components/SystemInfoPanel";
import { TaskTimeline } from "./components/TaskTimeline";
import { localLibraryViewKeys, sectionForView } from "./views/localLibrarySections";
import {
  latestStreamableTaskId as latestStreamableTaskIdFromEvents,
  mergeRunStreamEventIntoConversations,
  mergeStreamedAgentMessage,
  preserveStreamedRunConversations as preserveStreamedRunConversationsFromEvents
} from "./events";
import { AgentOpsView } from "./features/agents";
import {
  inferActiveOfficeAgentId,
  officeAgents,
  type HomeReadinessItem,
  type HomeTrustItem,
  type OfficeQuickSkill
} from "./features/office";
import { taskStarterManifestById } from "./features/office/taskStarterManifest";
import { ShellFrame } from "./features/shell";
import { LengrvisApiClient, type RealtimeConnectionStatus } from "./lib/apiClient";
import { zhBackendText, zhRealtimeBadMessageSummary, zhRealtimeConnectionStatus } from "./lib/zh";
import { useLengrvisStore, type AssistantMode, type ConnectionState, type ViewKey } from "./store";

const quickSkills: OfficeQuickSkill[] = [
  {
    id: "clean-downloads",
    icon: FolderOpen,
    title: "整理下载目录",
    summary: "先盘点和分组，删除前确认",
    kind: "prompt",
    prompt: "扫描我的下载目录，按安装包、文档、图片、压缩包和临时文件分组，给出整理建议；不要直接删除任何文件。",
    trust: { local: "本机文件", cloud: "不传正文", approval: "删除需审批", rollback: "回收站可恢复", estimate: "3-6 分钟" },
    wizard: {
      input: "下载目录或指定文件夹",
      preflight: "确认授权范围和清理风险",
      output: "分组清单、可释放空间、审批预览",
      nextStep: "点发送后先生成清理计划，不会直接删除文件"
    }
  },
  {
    id: "summarize-document",
    icon: BookOpenText,
    title: "总结本地文档",
    summary: "选择文件后生成摘要和引用",
    kind: "view",
    view: "files",
    trust: { local: "本机读取", cloud: "按模式", approval: "只读", rollback: "无改动", estimate: "1-3 分钟" },
    wizard: {
      input: "PDF/DOCX/TXT 等本地文档",
      preflight: "同步文件范围并读取预览",
      output: "摘要、重点、引用来源",
      nextStep: "进入文档操作区，选择文档后总结"
    }
  },
  {
    id: "find-large-files",
    icon: FileSearch,
    title: "查找大文件",
    summary: "列出占用空间和清理建议",
    kind: "prompt",
    prompt: "找出这台电脑上最大的文件，并按安全清理、需要确认、建议保留三类给出建议；不要直接删除任何文件。",
    trust: { local: "本机索引", cloud: "不传路径", approval: "清理需审批", rollback: "先审再处理", estimate: "2-5 分钟" },
    wizard: {
      input: "文件范围和大小阈值",
      preflight: "先扫描授权目录，不触碰未授权路径",
      output: "大文件排行、保留建议、清理候选",
      nextStep: "点发送后先选文件夹，再生成只读结果"
    }
  },
  {
    id: "check-computer",
    icon: Laptop,
    title: "检查电脑状态",
    summary: "只读查看后端、系统和本地 AI",
    kind: "action",
    action: "system-check",
    trust: { local: "本机状态", cloud: "不上云", approval: "只读", rollback: "无改动", estimate: "30 秒" },
    wizard: {
      input: "无需输入",
      preflight: "只读读取后端、系统和本地模型状态",
      output: "健康状态、缺失依赖、下一步修复入口",
      nextStep: "正在打开电脑状态页并刷新快照"
    }
  },
  {
    id: "document-qa",
    icon: BookOpenText,
    title: "文档问答",
    summary: "选择文件后带来源回答",
    kind: "view",
    view: "files",
    trust: { local: "本机抽取", cloud: "按模式", approval: "只读", rollback: "无改动", estimate: "1-4 分钟" },
    wizard: {
      input: "一份文档和一个问题",
      preflight: "读取选中文档并保留引用块",
      output: "带来源回答、可继续追问",
      nextStep: "进入文档操作区，选择文档后提问"
    }
  }
];

const OfficeScene = lazy(() => import("./features/office/OfficeScene").then((module) => ({ default: module.OfficeScene })));
const FileSearchPanel = lazy(() => import("./components/FileSearchPanel").then((module) => ({ default: module.FileSearchPanel })));
const BrowserActivityPanel = lazy(() => import("./components/BrowserActivityPanel").then((module) => ({ default: module.BrowserActivityPanel })));
const SettingsPanel = lazy(() => import("./components/SettingsPanel").then((module) => ({ default: module.SettingsPanel })));
const SkillsView = lazy(() => import("./views/SkillsView").then((module) => ({ default: module.SkillsView })));
const LocalLibraryView = lazy(() => import("./views/LocalLibraryView").then((module) => ({ default: module.LocalLibraryView })));

export function App() {
  const api = useMemo(() => new LengrvisApiClient(), []);
  const messages = useLengrvisStore((state) => state.messages);
  const setMessages = useLengrvisStore((state) => state.setMessages);
  const tasks = useLengrvisStore((state) => state.tasks);
  const setTasks = useLengrvisStore((state) => state.setTasks);
  const plan = useLengrvisStore((state) => state.plan);
  const setPlan = useLengrvisStore((state) => state.setPlan);
  const agentConversations = useLengrvisStore((state) => state.agentConversations);
  const setAgentConversations = useLengrvisStore((state) => state.setAgentConversations);
  const safetyReview = useLengrvisStore((state) => state.safetyReview);
  const setSafetyReview = useLengrvisStore((state) => state.setSafetyReview);
  const approvalRequests = useLengrvisStore((state) => state.approvalRequests);
  const setApprovalRequests = useLengrvisStore((state) => state.setApprovalRequests);
  const fileResults = useLengrvisStore((state) => state.fileResults);
  const setFileResults = useLengrvisStore((state) => state.setFileResults);
  const settings = useLengrvisStore((state) => state.settings);
  const setSettings = useLengrvisStore((state) => state.setSettings);
  const auditEntries = useLengrvisStore((state) => state.auditEntries);
  const setAuditEntries = useLengrvisStore((state) => state.setAuditEntries);
  const systemInfo = useLengrvisStore((state) => state.systemInfo);
  const setSystemInfo = useLengrvisStore((state) => state.setSystemInfo);
  const intentSuggestions = useLengrvisStore((state) => state.intentSuggestions);
  const setIntentSuggestions = useLengrvisStore((state) => state.setIntentSuggestions);
  const backendStatus = useLengrvisStore((state) => state.backendStatus);
  const setBackendStatus = useLengrvisStore((state) => state.setBackendStatus);
  const localLlmHealth = useLengrvisStore((state) => state.localLlmHealth);
  const setLocalLlmHealth = useLengrvisStore((state) => state.setLocalLlmHealth);
  const llmHealth = useLengrvisStore((state) => state.llmHealth);
  const setLlmHealth = useLengrvisStore((state) => state.setLlmHealth);
  const llmCostSummary = useLengrvisStore((state) => state.llmCostSummary);
  const setLlmCostSummary = useLengrvisStore((state) => state.setLlmCostSummary);
  const contextUsage = useLengrvisStore((state) => state.contextUsage);
  const setContextUsage = useLengrvisStore((state) => state.setContextUsage);
  const isLoading = useLengrvisStore((state) => state.isLoading);
  const setIsLoading = useLengrvisStore((state) => state.setIsLoading);
  const isSearching = useLengrvisStore((state) => state.isSearching);
  const setIsSearching = useLengrvisStore((state) => state.setIsSearching);
  const isApprovalOpen = useLengrvisStore((state) => state.isApprovalOpen);
  const setIsApprovalOpen = useLengrvisStore((state) => state.setIsApprovalOpen);
  const approvalError = useLengrvisStore((state) => state.approvalError);
  const setApprovalError = useLengrvisStore((state) => state.setApprovalError);
  const mode = useLengrvisStore((state) => state.mode);
  const setMode = useLengrvisStore((state) => state.setMode);
  const activeView = useLengrvisStore((state) => state.activeView);
  const setActiveView = useLengrvisStore((state) => state.setActiveView);
  const focusedTaskId = useLengrvisStore((state) => state.focusedTaskId);
  const setFocusedTaskId = useLengrvisStore((state) => state.setFocusedTaskId);
  const browserSessions = useLengrvisStore((state) => state.browserSessions);
  const setBrowserSessions = useLengrvisStore((state) => state.setBrowserSessions);
  const browserEvents = useLengrvisStore((state) => state.browserEvents);
  const setBrowserEvents = useLengrvisStore((state) => state.setBrowserEvents);
  const browserHostSnapshot = useLengrvisStore((state) => state.browserHostSnapshot);
  const setBrowserHostSnapshot = useLengrvisStore((state) => state.setBrowserHostSnapshot);
  const activeBrowserSessionId = useLengrvisStore((state) => state.activeBrowserSessionId);
  const setActiveBrowserSessionId = useLengrvisStore((state) => state.setActiveBrowserSessionId);
  const browserError = useLengrvisStore((state) => state.browserError);
  const setBrowserError = useLengrvisStore((state) => state.setBrowserError);
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
  const connectionState = connectionStateFromBackendAndRealtime(backendStatus, realtimeStatus);
  const activeOfficeAgentId = useMemo(
    () => inferActiveOfficeAgentId(tasks, plan, agentConversations, safetyReview.status),
    [agentConversations, plan, safetyReview.status, tasks]
  );
  const safetyAlert = safetyReview.status === "needs_review" || safetyReview.status === "blocked";
  const homeReadinessItems = useMemo(
    () => buildHomeReadinessItems({
      connectionState,
      realtimeStatus,
      mode,
      localLlmHealth,
      allowedDirectories: settings.allowedDirectories,
      workspaceRoot: settings.workspaceRoot
    }),
    [connectionState, localLlmHealth, mode, realtimeStatus, settings.allowedDirectories, settings.workspaceRoot]
  );
  const homeTrustItems = useMemo(
    () =>
      buildHomeTrustItems({
        mode,
        localLlmHealth,
        allowedDirectories: settings.allowedDirectories,
        workspaceRoot: settings.workspaceRoot,
        allowCloudContext: settings.allowCloudContext,
        allowFileContentUpload: settings.allowFileContentUpload
      }),
    [
      localLlmHealth,
      mode,
      settings.allowCloudContext,
      settings.allowFileContentUpload,
      settings.allowedDirectories,
      settings.workspaceRoot
    ]
  );
  const chatStartedTaskIds = useRef(new Set<string>());
  const announcedTerminalTaskIds = useRef(new Set<string>());
  const refreshTaskSnapshotTimer = useRef<number | null>(null);
  const latestTaskId = useMemo(
    () => hasLoadedBackendTasks ? latestStreamableTaskIdFromEvents(tasks) : null,
    [hasLoadedBackendTasks, tasks]
  );
  const latestLegacyTaskId = useMemo(
    () => latestLegacyTaskIdFromEvents(tasks, chatStartedTaskIds.current, announcedTerminalTaskIds.current),
    [tasks]
  );
  const recentReadableMessages = useMemo(() => recentReadableChatMessages(messages, 8), [messages]);

  useEffect(() => {
    backendStatusRef.current = backendStatus;
  }, [backendStatus]);

  const handleRealtimeStatus = useCallback((status: RealtimeConnectionStatus) => {
    setRealtimeStatus(status);
    if (shouldShowRealtimeStatusMessage(status)) {
      setMessages((current) => appendUniqueMessage(current, realtimeStatusChatMessage(status)));
    }
  }, [setMessages]);

  const handleRealtimeBadMessage = useCallback((status: RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string }) => {
    setRealtimeStatus(status);
    setMessages((current) => upsertRealtimeBadMessageNotice(current, realtimeBadMessageNotice.current, status));
  }, [setMessages]);

  useEffect(() => {
    if (!latestTaskId && !latestLegacyTaskId) {
      setRealtimeStatus(null);
    }
  }, [latestLegacyTaskId, latestTaskId]);

  const refreshWorkspace = useCallback(async () => {
    setIsLoading(true);

    try {
      const currentStatus = await api.getBackendStatus();
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
            error: localLlmResult.error?.message ?? "无法读取本地模型健康状态"
          });
        }
      } else {
        setLocalLlmHealth(null);
      }
    } catch (error) {
      setBackendStatus({
        ...backendStatusRef.current,
        state: "error",
        message: readableError(error, "Workspace refresh failed"),
        lastCheckedAt: new Date().toISOString()
      });
    } finally {
      setIsLoading(false);
    }
  }, [activeBrowserSessionId, api, mode]);

  useEffect(() => {
    void refreshWorkspace();
  }, [refreshWorkspace]);

  useEffect(() => {
    if (activeView !== "browser") {
      void api.hideBrowserHost().catch(() => undefined);
    }
  }, [activeView, api]);

  const sendMessage = async (content: string): Promise<{ ok: boolean; error?: string }> => {
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
      let result = await api.sendChat({ content, mode });
      if (!result.ok) {
        result = await api.startRun({ content, mode });
      }

      const response = result.data;
      if (result.ok && response) {
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
    } catch (error) {
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
    if (connectionState === "offline") {
      setHeroSubmitError("Lengrvis 服务还没连上。请先刷新连接，输入内容我会保留。");
      return;
    }

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
    } catch (error) {
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

  const scheduleTaskSnapshotRefresh = useCallback(() => {
    if (refreshTaskSnapshotTimer.current !== null) return;
    refreshTaskSnapshotTimer.current = window.setTimeout(() => {
      refreshTaskSnapshotTimer.current = null;
      void refreshTaskSnapshot();
    }, 1200);
  }, [refreshTaskSnapshot]);

  useEffect(() => () => {
    if (refreshTaskSnapshotTimer.current !== null) {
      window.clearTimeout(refreshTaskSnapshotTimer.current);
      refreshTaskSnapshotTimer.current = null;
    }
  }, []);

  useEffect(() => {
    if (!hasLoadedBackendTasks) return;
    const hasRunningTask = tasks.some(
      (task) => isActiveTask(task) && (Boolean(task.runId) || wasUpdatedRecently(task))
    );
    if (!hasRunningTask) return;
    const intervalId = window.setInterval(() => {
      void refreshTaskSnapshot();
    }, 2500);
    return () => window.clearInterval(intervalId);
  }, [hasLoadedBackendTasks, refreshTaskSnapshot, tasks]);

  useEffect(() => {
    if (!latestTaskId) return;

    const unsubscribe = api.subscribeRunEvents(latestTaskId, {
      onMessage: (event) => {
        if (event.type === "run_event") {
          setAgentConversations((current) => mergeRunStreamEventIntoConversations(current, latestTaskId, event));
          const terminalMessage = chatMessageFromRunTerminalEvent(event);
          if (terminalMessage) {
            setMessages((current) => appendUniqueMessage(current, terminalMessage));
          }
          scheduleTaskSnapshotRefresh();
        }
      },
      onStatus: handleRealtimeStatus,
      onBadMessage: handleRealtimeBadMessage
    });

    return () => {
      unsubscribe();
    };
  }, [api, handleRealtimeBadMessage, handleRealtimeStatus, latestTaskId, scheduleTaskSnapshotRefresh]);

  useEffect(() => {
    if (!latestLegacyTaskId) return;

    const unsubscribe = api.subscribeTaskMessages(latestLegacyTaskId, {
      onMessage: (event) => {
        if (event.type !== "agent_message" || !event.message) return;
        const message = {
          ...event.message,
          content: zhBackendText(event.message.content)
        };
        setAgentConversations((current) => mergeStreamedAgentMessage(current, latestLegacyTaskId, message));
        const chatMessage = chatMessageFromTaskAgentMessage(latestLegacyTaskId, message);
        if (chatMessage) {
          setMessages((current) => appendUniqueMessage(current, chatMessage));
        }
        scheduleTaskSnapshotRefresh();
      },
      onStatus: handleRealtimeStatus,
      onBadMessage: handleRealtimeBadMessage
    });

    return () => {
      unsubscribe();
    };
  }, [api, handleRealtimeBadMessage, handleRealtimeStatus, latestLegacyTaskId, scheduleTaskSnapshotRefresh]);

  useEffect(() => {
    if (approvalSelectionContext !== "queue") return;
    setApprovalQueueCursor((current) => Math.min(current, Math.max(pendingApprovals.length - 1, 0)));
  }, [approvalSelectionContext, pendingApprovals.length]);

  useEffect(() => {
    for (const task of tasks) {
      if (task.runId || !chatStartedTaskIds.current.has(task.id)) continue;
      if (task.state !== "completed" && task.state !== "failed") continue;
      if (announcedTerminalTaskIds.current.has(task.id)) continue;
      const message = chatMessageFromLegacyTaskTerminal(task);
      if (!message) continue;
      announcedTerminalTaskIds.current.add(task.id);
      setMessages((current) => appendUniqueMessage(current, message));
    }
  }, [tasks, setMessages]);

  useEffect(() => {
    const unsubscribe = window.lengrvis?.notifications.onOpenTask((taskId) => {
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

  const isLocalLibraryView = localLibraryViewKeys.has(activeView);
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
    <>
      <ShellFrame
        activeView={activeView}
        connectionState={connectionState}
        isLoading={isLoading}
        onViewChange={setActiveView}
        onRefresh={() => void refreshWorkspace()}
        onOpenApprovals={() => {
          setApprovalError(null);
          setApprovalSelectionContext("queue");
          setApprovalQueueCursor(0);
          setFocusedTaskId(null);
          setIsApprovalOpen(true);
        }}
        hasPendingApproval={pendingApprovals.length > 0}
        messages={messages}
        tasks={tasks}
        systemInfo={systemInfo}
      >

        {isLocalLibraryView ? (
          <Suspense fallback={<RouteLoading />}>
            <LocalLibraryView api={api} activeSection={sectionForView(activeView)} onUseDocument={openDocumentTool} />
          </Suspense>
        ) : null}

        {activeView === "home" ? (
          <section className="lengrvis-home">
            <Suspense fallback={<RouteLoading />}>
              <OfficeScene
                agents={officeAgents}
                draft={draft}
                onDraftChange={setDraft}
                onSubmitPrompt={submitHeroPrompt}
                onAgentSelect={(prompt) => setDraft(prompt)}
                connectionState={connectionState}
                isSubmitting={heroSubmitting}
                submitError={heroSubmitError}
                activeAgentId={activeOfficeAgentId}
                recentTasks={tasks}
                quickSkills={quickSkills}
                readinessItems={homeReadinessItems}
                trustItems={homeTrustItems}
                onQuickSkill={handleQuickSkill}
                onReadinessAction={handleReadinessAction}
                onTaskPilotAction={handleTaskPilotAction}
                pendingApprovalCount={pendingApprovals.length}
                safetyAlert={safetyAlert}
              />
            </Suspense>
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

        {activeView === "agentOps" ? (
          <section className="agent-ops-view">
            <AgentOpsView
              tasks={tasks}
              safetyReview={safetyReview}
              onDraftPrompt={setDraft}
              onOpenChat={() => setActiveView("chat")}
              onOpenApprovals={() => {
                setApprovalSelectionContext("task");
                setIsApprovalOpen(true);
              }}
            />
          </section>
        ) : null}

        {activeView === "files" ? (
          <section className="detail-grid">
            <Suspense fallback={<RouteLoading />}>
              <FileSearchPanel
                results={fileResults}
                searchMeta={fileSearchMeta}
                isSearching={isSearching}
                onSearch={searchFiles}
                onClearResults={() => {
                  setFileResults([]);
                  setFileSearchMeta(null);
                  setFileSearchError(null);
                }}
                searchError={fileSearchError}
                api={api}
                connectionState={connectionState}
                settings={settings}
                onSaveSettings={saveSettings}
                initialTool={fileToolTab}
                onToolChange={setFileToolTab}
                selectedDocumentPath={documentIntent?.path}
                selectedDocumentAction={documentIntent?.action}
                selectedDocumentIntentId={documentIntent?.nonce}
                onDocumentIntentHandled={() => setDocumentIntent(null)}
                hasPendingApproval={Boolean(pendingApproval)}
                onOpenApprovals={() => {
                  setApprovalSelectionContext("task");
                  setIsApprovalOpen(true);
                }}
                onRequestCleanupApproval={requestCleanupApproval}
              />
            </Suspense>
            <ChatPanel messages={recentReadableMessages} connectionState={connectionState} onSend={sendMessage} />
          </section>
        ) : null}

        {activeView === "computer" ? (
          <section className="detail-grid">
            <SystemInfoPanel
              info={systemInfo}
              isRefreshing={isCheckingComputer}
              onRefresh={async () => {
                setIsCheckingComputer(true);
                try {
                  await refreshSystemInfo();
                } finally {
                  setIsCheckingComputer(false);
                }
              }}
              onOpenSettings={openWindowsSettings}
            />
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
            <Suspense fallback={<RouteLoading />}>
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
            </Suspense>
          </section>
        ) : null}

        {activeView === "memories" ? (
          <section className="detail-grid">
            <MemoryPanel api={api} />
          </section>
        ) : null}

        {activeView === "safety" ? (
          <section className="detail-grid">
            <SafetyReviewPanel
              review={safetyReview}
              onOpenApproval={() => {
                setApprovalSelectionContext("task");
                setIsApprovalOpen(true);
              }}
            />
            <AuditLogPanel entries={auditEntries} />
          </section>
        ) : null}

        {activeView === "settings" ? (
          <section className="detail-grid">
            <Suspense fallback={<RouteLoading />}>
              <SettingsPanel
                settings={settings}
                backendStatus={backendStatus}
                realtimeStatus={realtimeStatus}
                localLlmHealth={localLlmHealth}
                llmHealth={llmHealth}
                llmCostSummary={llmCostSummary}
                onLocalLlmHealthChange={setLocalLlmHealth}
                onSave={saveSettings}
                onStartBackend={async () => setBackendStatus(await api.startBackend())}
                onStopBackend={async () => setBackendStatus(await api.stopBackend())}
                api={api}
                privacyIntentId={settingsIntent?.section === "privacy" ? settingsIntent.nonce : undefined}
              />
            </Suspense>
            <Suspense fallback={<RouteLoading />}>
              <SkillsView api={api} />
            </Suspense>
            <SystemInfoPanel
              info={systemInfo}
              onRefresh={async () => {
                await refreshSystemInfo();
              }}
              onOpenSettings={openWindowsSettings}
            />
          </section>
        ) : null}

      </ShellFrame>

      <ApprovalDialog
        approval={pendingApproval}
        pendingCount={pendingApprovals.length}
        selectionContext={approvalSelectionContext}
        queueIndex={pendingApproval ? pendingApprovals.findIndex((approval) => approval.id === pendingApproval.id) + 1 : 0}
        isOpen={isApprovalOpen}
        error={approvalError}
        canGoPrevious={approvalSelectionContext === "queue" && approvalQueueCursor > 0}
        canGoNext={approvalSelectionContext === "queue" && approvalQueueCursor < pendingApprovals.length - 1}
        onClose={() => {
          setApprovalError(null);
          setIsApprovalOpen(false);
        }}
        onPrevious={() => setApprovalQueueCursor((current) => Math.max(0, current - 1))}
        onNext={() => setApprovalQueueCursor((current) => Math.min(pendingApprovals.length - 1, current + 1))}
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

function RouteLoading() {
  return (
    <div className="route-loading" role="status" aria-live="polite">
      <span className="spin-icon" aria-hidden="true" />
      <span>正在载入</span>
    </div>
  );
}

function selectedPendingApproval(approvals: ApprovalRequest[], taskId?: string | null): ApprovalRequest | null {
  if (taskId) {
    const taskApproval = approvals.find((approval) => approval.taskId === taskId);
    if (taskApproval) return taskApproval;
  }
  return approvals[0] ?? null;
}

function latestLegacyTaskIdFromEvents(
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

function requiresLocalLlmHealth(mode: AssistantMode): boolean {
  return mode === "privacy" || mode === "hybrid";
}

function buildHomeReadinessItems({
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
      detail: realtimeDetail || (connectionState === "online" ? "服务已连接，可以直接开始任务" : connectionState === "checking" ? "正在确认后端服务" : "服务离线，先恢复连接"),
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
      detail: primaryScope ? compactPath(primaryScope) : "先选择桌面、下载或指定文件夹",
      state: primaryScope ? "ready" : "action",
      actionLabel: primaryScope ? "查看" : "选择",
      targetView: "files"
    },
    {
      id: "document",
      label: "文档操作",
      detail: "读取、总结、提问都从这里进入",
      state: "action",
      actionLabel: "打开",
      targetView: "files"
    }
  ];
}

function buildHomeTrustItems({
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
            ? "可使用云端辅助"
            : "云端上下文关闭",
      state: localReady || !allowCloudContext ? "ready" : "warning"
    },
    {
      id: "files",
      label: "文件范围",
      value: primaryScope ? compactPath(primaryScope) : "未授权",
      detail: primaryScope ? "文件工具只看这个范围" : "先选择桌面、下载或文件夹",
      state: primaryScope ? "ready" : "warning"
    },
    {
      id: "upload",
      label: "文件内容上传",
      value: allowFileContentUpload ? "需要确认" : "已关闭",
      detail: allowFileContentUpload ? "读取内容前仍会遵守权限" : "默认不上传文件正文",
      state: allowFileContentUpload ? "warning" : "ready"
    },
    {
      id: "approval",
      label: "危险操作",
      value: "先审批",
      detail: "删除、移动、写入前会停下来",
      state: "ready"
    }
  ];
}

function compactPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return path;
  return `${parts.at(-2)}/${parts.at(-1)}`;
}

interface RealtimeBadMessageNotice {
  count: number;
  messageId: string;
  samples: string[];
}

function connectionStateFromBackendAndRealtime(
  backendStatus: { state: string },
  realtimeStatus: RealtimeConnectionStatus | null
): ConnectionState {
  if (backendStatus.state === "starting") return "checking";
  if (backendStatus.state !== "running") return "offline";
  if (!realtimeStatus) return "online";
  if (realtimeStatus.state === "connecting" || realtimeStatus.state === "reconnecting") return "checking";
  if (["unauthorized", "policy_violation", "error", "closed"].includes(realtimeStatus.state)) return "offline";
  return "online";
}

function shouldShowRealtimeStatusMessage(status: RealtimeConnectionStatus): boolean {
  return ["reconnecting", "error", "unauthorized", "policy_violation"].includes(status.state);
}

function realtimeStatusChatMessage(status: RealtimeConnectionStatus): ChatMessage {
  return {
    id: `realtime-status-${status.endpoint}-${status.state}`,
    role: "assistant",
    author: "Lengrvis",
    content: zhRealtimeConnectionStatus(status),
    createdAt: status.at,
    status: status.state === "reconnecting" ? "streaming" : "failed"
  };
}

function upsertRealtimeBadMessageNotice(
  current: ChatMessage[],
  notice: RealtimeBadMessageNotice,
  status: RealtimeConnectionStatus & { state: "bad_message"; rawMessage: string }
): ChatMessage[] {
  notice.count += 1;
  const sample = status.rawMessage.trim();
  if (sample && !notice.samples.includes(sample)) {
    notice.samples = [sample, ...notice.samples].slice(0, 3);
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

function isActiveTask(task: TaskEvent): boolean {
  return task.state === "running" || task.state === "queued" || task.state === "blocked" || task.state === "paused";
}

function wasUpdatedRecently(task: TaskEvent): boolean {
  const timestamp = Date.parse(task.updatedAt || task.createdAt || "");
  if (Number.isNaN(timestamp)) return true;
  return Date.now() - timestamp < 15 * 60 * 1000;
}

function chatMessageFromRunTerminalEvent(event: {
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

function chatMessageFromTaskAgentMessage(
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

function chatMessageFromLegacyTaskTerminal(task: TaskEvent): ChatMessage | null {
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

function isUserVisibleTaskMessage(message: {
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

function summarizeTaskAgentContent(content: string, author: string): string {
  const translated = zhBackendText(content).replace(/\s*Large output persisted to .+$/u, "").trim();
  if (translated.includes("已生成清理预览")) {
    return `${author} 已生成清理预览。你可以在任务面板查看候选项；不会直接删除文件，需要你审批后才会执行。`;
  }
  if (translated.includes("Explicit absolute path is required")) {
    return `${author} 没有拿到明确路径，所以没有继续清理。请说具体目录，比如 D:\\Downloads 或 D:\\Temp。`;
  }
  return translated;
}

function isInternalTaskMessageContent(content: string): boolean {
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

function terminalLegacyTaskMessage(status: "completed" | "failed", description: string): string {
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

function zhAgentAuthor(agent: string): string {
  if (agent === "FileAgent") return "文件 Agent";
  if (agent === "DocumentAgent") return "文档 Agent";
  if (agent === "ComputerAgent") return "电脑 Agent";
  if (agent === "BrowserAgent") return "浏览器 Agent";
  if (agent === "SearchAgent") return "搜索 Agent";
  if (agent === "PlannerAgent") return "规划 Agent";
  if (agent === "OrchestratorAgent") return "调度 Agent";
  return agent || "Agent";
}

function terminalRunMessage(name: string, payload: Record<string, unknown>): string {
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

function stripTerminalPrefix(value: string): string {
  return value
    .replace(/^任务执行失败[：:]\s*/u, "")
    .replace(/^执行失败[：:]\s*/u, "")
    .trim();
}

function recentReadableChatMessages(messages: ChatMessage[], limit: number): ChatMessage[] {
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

function isMojibakeLike(value: string): boolean {
  const compact = value.replace(/\s+/g, "");
  if (!compact) return false;
  const questionMarks = (compact.match(/\?/g) ?? []).length;
  if (questionMarks >= 3 && questionMarks / compact.length > 0.2) return true;
  return /�|Ã|Â|ä¸|ç®|å·|æ/.test(compact);
}

function readableError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function appendUniqueMessage(current: ChatMessage[], message: ChatMessage): ChatMessage[] {
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
