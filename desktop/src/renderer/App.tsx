import {
  Activity,
  AppWindow,
  Bell,
  BookOpenText,
  Bot,
  Brain,
  CheckCircle2,
  Clock,
  CornerDownLeft,
  FileSearch,
  FolderOpen,
  Globe2,
  Home,
  Image,
  Laptop,
  Loader2,
  MessageSquarePlus,
  MonitorSmartphone,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Wrench,
  type LucideIcon
} from "lucide-react";
import {
  type CSSProperties,
  type Dispatch,
  type SetStateAction,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState
} from "react";

import type {
  AgentConversation,
  AppSettings,
  ApprovalRequest,
  AuditLogEntry,
  BackendStatus,
  ChatMessage,
  FileSearchResult,
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
import { FileSearchPanel } from "./components/FileSearchPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import { PlanViewer } from "./components/PlanViewer";
import { SafetyReviewPanel } from "./components/SafetyReviewPanel";
import { SchedulePanel } from "./components/SchedulePanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { SystemInfoPanel } from "./components/SystemInfoPanel";
import { TaskTimeline } from "./components/TaskTimeline";
import { SkillsView } from "./views/SkillsView";
import {
  sampleAgentConversations,
  sampleApprovalRequests,
  sampleAuditLogs,
  sampleChatMessages,
  sampleFileResults,
  samplePlan,
  sampleSafetyReview,
  sampleSettings,
  sampleSystemInfo,
  sampleTaskTimeline
} from "./data/mockData";
import { MavrisApiClient } from "./lib/apiClient";
import { zhAgentName } from "./lib/zh";

type AssistantMode = "privacy" | "efficiency" | "hybrid";
type ViewKey = "home" | "chat" | "files" | "computer" | "agents" | "memories" | "safety" | "settings";
type OfficeAgentPose =
  | "working"
  | "coffee"
  | "treadmill"
  | "restroom"
  | "nap"
  | "wander"
  | "review";

interface OfficeAgentDefinition {
  id: string;
  name: string;
  role: string;
  icon: LucideIcon;
  prompt: string;
  accent: string;
  glow: string;
  x: number;
  y: number;
  wanderX: number;
  wanderY: number;
  delay: number;
  duration: number;
  scale?: "lead" | "standard";
  activities: string[];
}

interface OfficeAgentRuntime {
  x: number;
  y: number;
  activity: string;
  pose: OfficeAgentPose;
}

interface LeisureSpot {
  pose: OfficeAgentPose;
  x: number;
  y: number;
  activity: string;
}

interface OfficeMapSize {
  width: number;
  height: number;
}

interface PonyAgentProps {
  accent: string;
  pose: OfficeAgentPose;
  isLead?: boolean;
  isWorking: boolean;
}

const disconnectedStatus: BackendStatus = {
  state: "not_configured",
  baseUrl: sampleSettings.apiBaseUrl,
  message: "等待后端连接",
  lastCheckedAt: new Date().toISOString(),
  health: {
    ok: false
  }
};

const modeCopy: Record<AssistantMode, { title: string; body: string }> = {
  privacy: { title: "隐私模式", body: "优先使用本地 LLM；未检测到本地后端时会明确失败" },
  efficiency: { title: "效率模式", body: "端云协同，又快又准" },
  hybrid: { title: "混合模式", body: "本地优先，必要时云端协助" }
};

const localKnowledge = [
  { label: "应用", icon: AppWindow, view: "computer" as ViewKey },
  { label: "文档", icon: BookOpenText, view: "files" as ViewKey },
  { label: "我的记忆", icon: Brain, view: "memories" as ViewKey },
  { label: "图库", icon: Image, view: "files" as ViewKey },
  { label: "此电脑", icon: Laptop, view: "computer" as ViewKey }
];

const dialogueItems = ["办公室", "策划去成都的团建", "设计文档整理", "素材文件归类", "热门 AI 使用方法"];

const quickSkills = [
  { icon: FileSearch, title: "文件智能整理搜索", prompt: "找出重复文件，但先不要删除" },
  { icon: BookOpenText, title: "文件深度理解与生成", prompt: "总结 sample_contract.txt 的付款条款" },
  { icon: Laptop, title: "一句话完成电脑设置", prompt: "查电脑配置" },
  { icon: MonitorSmartphone, title: "手机随时操控电脑", prompt: "生成手机远控电脑的连接方案" }
];

const viewTitles: Record<ViewKey, { title: string; subtitle: string }> = {
  home: { title: "办公室", subtitle: "七只小马 Agent 协同工作" },
  chat: { title: "新建对话", subtitle: "先和主管 Agent 对话，需要执行时再分配" },
  files: { title: "文件工作区", subtitle: "在本机文档与素材里搜索" },
  computer: { title: "此电脑", subtitle: "系统能力与诊断" },
  agents: { title: "自动任务", subtitle: "查看每个 Agent 的协作记录" },
  memories: { title: "我的记忆", subtitle: "Marvis 长期记住的事实与偏好" },
  safety: { title: "安全审核", subtitle: "策略检查与审计" },
  settings: { title: "设置", subtitle: "运行时与权限" }
};

// 7 个原创 Agent，每个分配独立位置 + 围巾颜色
const officeAgents: OfficeAgentDefinition[] = [
  {
    id: "pm",
    name: "Marvis",
    role: "主控调度",
    icon: Sparkles,
    prompt: "帮我把今天的电脑任务拆成一个安全执行计划",
    accent: "#ff5474",
    glow: "rgba(255, 84, 116, 0.32)",
    x: 56,
    y: 38,
    wanderX: 1.2,
    wanderY: 1,
    delay: 0,
    duration: 5.4,
    scale: "lead",
    activities: ["坐镇调度", "拆解目标", "派发任务"]
  },
  {
    id: "file",
    name: "文件 Agent",
    role: "文件专家",
    icon: FolderOpen,
    prompt: "找出重复文件，但先不要删除",
    accent: "#8b5cf6",
    glow: "rgba(139, 92, 246, 0.32)",
    x: 80,
    y: 76,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 0.7,
    duration: 6,
    activities: ["检索文档", "扫描重复", "整理素材"]
  },
  {
    id: "computer",
    name: "电脑 Agent",
    role: "电脑管家",
    icon: Laptop,
    prompt: "查电脑配置",
    accent: "#f5a623",
    glow: "rgba(245, 166, 35, 0.32)",
    x: 80,
    y: 56,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 1.2,
    duration: 5.8,
    activities: ["读取配置", "巡检状态", "定位问题"]
  },
  {
    id: "app",
    name: "应用 Agent",
    role: "应用调度",
    icon: AppWindow,
    prompt: "帮我打开常用办公应用并列出可自动化的任务",
    accent: "#ff7e3e",
    glow: "rgba(255, 126, 62, 0.32)",
    x: 80,
    y: 36,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 1.8,
    duration: 6.5,
    activities: ["查找应用", "准备调用", "同步窗口"]
  },
  {
    id: "browser",
    name: "浏览器 Agent",
    role: "浏览器助手",
    icon: Globe2,
    prompt: "打开浏览器只读搜索最近的 AI 办公资料",
    accent: "#20bcd5",
    glow: "rgba(32, 188, 213, 0.32)",
    x: 62,
    y: 68,
    wanderX: 1.5,
    wanderY: 1.3,
    delay: 2.2,
    duration: 5.7,
    activities: ["读取网页", "等待授权", "整理链接"]
  },
  {
    id: "search",
    name: "搜索 Agent",
    role: "搜索专家",
    icon: Search,
    prompt: "搜索本地和网页资料，整理成三条可靠结论",
    accent: "#b87a4d",
    glow: "rgba(184, 122, 77, 0.32)",
    x: 62,
    y: 56,
    wanderX: 1.7,
    wanderY: 1.2,
    delay: 2.8,
    duration: 6.2,
    activities: ["喝咖啡", "交叉搜索", "比对来源"]
  },
  {
    id: "safety",
    name: "安全审核 Agent",
    role: "全程监督",
    icon: ShieldCheck,
    prompt: "先审核这个任务的风险等级，再告诉我是否需要审批",
    accent: "#4a6cf7",
    glow: "rgba(74, 108, 247, 0.32)",
    x: 38,
    y: 22,
    wanderX: 4,
    wanderY: 2,
    delay: 3.3,
    duration: 6.8,
    activities: ["巡逻审核", "扫描风险", "保护隐私"]
  }
];

const officeViewBox = { width: 1000, height: 700 };

// 下面的坐标使用 OfficeSceneSVG 的 viewBox 坐标，避免和 SVG 的 slice 缩放产生错位。
const leisureSpots: LeisureSpot[] = [
  { pose: "coffee", x: 190, y: 184, activity: "喝咖啡" },
  { pose: "coffee", x: 300, y: 184, activity: "等咖啡" },
  { pose: "treadmill", x: 210, y: 310, activity: "跑步摸鱼" },
  { pose: "nap", x: 210, y: 454, activity: "躺在沙发上" },
  { pose: "wander", x: 380, y: 300, activity: "在走道闲逛" },
  { pose: "restroom", x: 246, y: 594, activity: "去洗手间" }
];

const officeWorkSeats: Record<string, { x: number; y: number }> = {
  pm: { x: 560, y: 344 },
  file: { x: 810, y: 344 },
  computer: { x: 560, y: 504 },
  app: { x: 810, y: 504 },
  browser: { x: 560, y: 664 },
  search: { x: 810, y: 664 },
  safety: { x: 560, y: 344 }
};

const safetyPatrolRoute = [
  { x: 400, y: 270 },
  { x: 390, y: 335 },
  { x: 390, y: 415 },
  { x: 460, y: 425 },
  { x: 460, y: 335 },
  { x: 450, y: 275 }
];

export function App() {
  const api = useMemo(() => new MavrisApiClient(), []);
  const [messages, setMessages] = useState<ChatMessage[]>(sampleChatMessages);
  const [tasks, setTasks] = useState<TaskEvent[]>(sampleTaskTimeline);
  const [plan, setPlan] = useState<Plan>(samplePlan);
  const [agentConversations, setAgentConversations] =
    useState<AgentConversation[]>(sampleAgentConversations);
  const [safetyReview, setSafetyReview] = useState<SafetyReview>(sampleSafetyReview);
  const [approvalRequests, setApprovalRequests] = useState<ApprovalRequest[]>(sampleApprovalRequests);
  const [fileResults, setFileResults] = useState<FileSearchResult[]>(sampleFileResults);
  const [settings, setSettings] = useState<AppSettings>(sampleSettings);
  const [auditEntries, setAuditEntries] = useState<AuditLogEntry[]>(sampleAuditLogs);
  const [systemInfo, setSystemInfo] = useState<SystemInfo>(sampleSystemInfo);
  const [intentSuggestions, setIntentSuggestions] = useState<IntentSuggestion[]>([]);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>(disconnectedStatus);
  const [localLlmHealth, setLocalLlmHealth] = useState<LocalLLMHealth | null>(null);
  const [llmHealth, setLlmHealth] = useState<LLMHealthStatus | null>(null);
  const [llmCostSummary, setLlmCostSummary] = useState<LLMCostSummary | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [isApprovalOpen, setIsApprovalOpen] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [mode, setMode] = useState<AssistantMode>("efficiency");
  const [activeView, setActiveView] = useState<ViewKey>("home");
  const [focusedTaskId, setFocusedTaskId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [chatDraftSeed, setChatDraftSeed] = useState("");
  const [conversationResetKey, setConversationResetKey] = useState(0);

  const pendingApproval = approvalRequests.find((approval) => approval.status === "pending") ?? null;
  const connectionState = backendStatus.state === "running" ? "online" : isLoading ? "checking" : "offline";
  const activeOfficeAgentId = useMemo(
    () => inferActiveOfficeAgentId(tasks, plan, agentConversations, safetyReview.status),
    [agentConversations, plan, safetyReview.status, tasks]
  );
  const safetyAlert = safetyReview.status === "needs_review" || safetyReview.status === "blocked";
  const latestTaskId = useMemo(() => latestStreamableTaskId(tasks), [tasks]);

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
      auditResult,
      systemResult,
      suggestionsResult
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
      api.listAuditLogs(),
      api.getSystemInfo(),
      api.listIntentSuggestions()
    ]);

    if (chatResult.status === "fulfilled" && chatResult.value.ok && chatResult.value.data) setMessages(chatResult.value.data);
    if (tasksResult.status === "fulfilled" && tasksResult.value.ok && tasksResult.value.data) setTasks(tasksResult.value.data);
    if (planResult.status === "fulfilled" && planResult.value.ok && planResult.value.data) setPlan(planResult.value.data);
    if (agentsResult.status === "fulfilled" && agentsResult.value.ok && agentsResult.value.data) {
      setAgentConversations((current) => preserveStreamedRunConversations(current, agentsResult.value.data ?? []));
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
    if (auditResult.status === "fulfilled" && auditResult.value.ok && auditResult.value.data) setAuditEntries(auditResult.value.data);
    if (systemResult.status === "fulfilled" && systemResult.value.ok && systemResult.value.data) setSystemInfo(systemResult.value.data);
    if (suggestionsResult.status === "fulfilled" && suggestionsResult.value.ok && suggestionsResult.value.data) {
      setIntentSuggestions(suggestionsResult.value.data);
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
          error: localLlmResult.error?.message ?? "无法读取本地 LLM 健康状态。"
        });
      }
    } else {
      setLocalLlmHealth(null);
    }

    setIsLoading(false);
  }, [api]);

  useEffect(() => {
    void refreshWorkspace();
  }, [refreshWorkspace]);

  const startNewConversation = useCallback((initialPrompt = "") => {
    setDraft("");
    setChatDraftSeed(initialPrompt);
    setConversationResetKey((current) => current + 1);
    setActiveView("chat");
  }, []);

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
        author: "Marvis",
        content: result.error?.message ?? "后端暂时不可用。",
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
    setSettings(nextSettings);
    setMode(nextSettings.mode);
    const result = await api.saveSettings(nextSettings);
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
    if (runsResult.status === "fulfilled" && runsResult.value.ok && runsResult.value.data) {
      setTasks(runsResult.value.data);
    } else if (legacyTasksResult.status === "fulfilled" && legacyTasksResult.value.ok && legacyTasksResult.value.data) {
      setTasks(legacyTasksResult.value.data);
    }
    if (planResult.status === "fulfilled" && planResult.value.ok && planResult.value.data) setPlan(planResult.value.data);
    if (agentsResult.status === "fulfilled" && agentsResult.value.ok && agentsResult.value.data) {
      setAgentConversations((current) => preserveStreamedRunConversations(current, agentsResult.value.data ?? []));
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
        if (event.type !== "run_event") return;
        mergeStreamedRunEvent(latestTaskId, event, setAgentConversations);
        void refreshTaskSnapshot();
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
    setApprovalError(result.error?.message ?? "审批提交失败，请刷新后重试。");
    const approvalsResult = await api.listPendingApprovals();
    if (approvalsResult.ok && approvalsResult.data) {
      setApprovalRequests(approvalsResult.data);
    }
  };

  const viewMeta = viewTitles[activeView];

  return (
    <div className="marvis-shell">
      <aside className="marvis-sidebar">
        <div className="sidebar-brand">
          <span className="sidebar-brand__logo">M</span>
          <span className="sidebar-brand__text">
            <strong>Mavris</strong>
            <small>办公室 Agent</small>
          </span>
        </div>

        <div className="sidebar-search">
          <Search size={13} aria-hidden="true" />
          <span>搜索</span>
          <kbd>Ctrl K</kbd>
        </div>

        <nav className="primary-nav" aria-label="主导航">
          <SideButton icon={Home} label="办公室" active={activeView === "home"} onClick={() => setActiveView("home")} />
          <SideButton icon={MessageSquarePlus} label="新建对话" active={activeView === "chat"} onClick={() => startNewConversation()} />
          <SideButton icon={Activity} label="自动任务" active={activeView === "agents"} onClick={() => setActiveView("agents")} />
          <SideButton icon={Wrench} label="技能大全" active={activeView === "safety"} onClick={() => setActiveView("safety")} />
        </nav>

        <div className="sidebar-section">
          <span>本地知识库</span>
          {localKnowledge.map((item) => (
            <SideButton
              key={item.label}
              icon={item.icon}
              label={item.label}
              active={activeView === item.view}
              onClick={() => setActiveView(item.view)}
            />
          ))}
        </div>

        <div className="sidebar-section sidebar-section--dialogues">
          <span>最近对话</span>
          {dialogueItems.map((item) => (
            <button
              key={item}
              className="dialogue-link"
              onClick={() => {
                if (item === "办公室") {
                  setActiveView("home");
                  return;
                }
                startNewConversation(item);
              }}
              type="button"
            >
              {item}
            </button>
          ))}
        </div>

        <button className="sidebar-user" onClick={() => setActiveView("settings")} type="button">
          <span className="mini-avatar" />
          <span style={{ flex: 1, display: "grid", gap: 0 }}>
            <strong>Marvis</strong>
            <em>{modeCopy[mode].title}</em>
          </span>
          <Settings size={14} aria-hidden="true" />
        </button>
      </aside>

      <main className="marvis-main">
        <header className="window-bar">
          <div className="window-bar__left">
            <div className="window-bar__title">
              <span>{viewMeta.title}</span>
              <small>{viewMeta.subtitle}</small>
            </div>
          </div>
          <div className="window-actions">
            <span className={`connection-pill connection-pill--${connectionState}`}>
              <span className="connection-pill__dot" />
              {connectionState === "online" ? "后端在线" : connectionState === "checking" ? "检查中" : "离线演示"}
            </span>
            <button className="icon-button" aria-label="刷新" onClick={() => void refreshWorkspace()} disabled={isLoading} type="button">
              {isLoading ? (
                <Loader2 size={15} aria-hidden="true" style={{ animation: "dot-spin 1s linear infinite" }} />
              ) : (
                <RefreshCw size={15} aria-hidden="true" />
              )}
            </button>
            <button className="icon-button" aria-label="审批" onClick={() => setIsApprovalOpen(true)} type="button">
              <Bell size={15} aria-hidden="true" />
            </button>
          </div>
        </header>

        {activeView === "home" ? (
          <section className="marvis-home">
            <OfficeScene
              agents={officeAgents}
              completedTaskCount={tasks.filter((task) => task.state === "completed").length}
              totalTaskCount={tasks.length}
              mode={mode}
              draft={draft}
              onDraftChange={setDraft}
              onSubmitPrompt={submitHeroPrompt}
              onModeChange={setMode}
              localLlmHealth={localLlmHealth}
              llmHealth={llmHealth}
              llmCostSummary={llmCostSummary}
              onAgentSelect={(prompt) => setDraft(prompt)}
              activeAgentId={activeOfficeAgentId}
              recentTasks={tasks}
              quickSkills={quickSkills}
              onQuickSkill={(prompt) => void sendMessage(prompt)}
              safetyAlert={safetyAlert}
            />
          </section>
        ) : null}

        {activeView === "chat" ? (
          <section className="conversation-view">
            <ChatPanel
              key={conversationResetKey}
              messages={messages}
              connectionState={connectionState}
              onSend={sendMessage}
              initialDraft={chatDraftSeed}
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

        <footer className="consumer-statusbar">
          <span><Activity size={14} aria-hidden="true" />{backendStatus.message ?? "就绪"} · {modeCopy[mode].title}</span>
          <span><CheckCircle2 size={14} aria-hidden="true" />{new Date(backendStatus.lastCheckedAt).toLocaleTimeString()}</span>
        </footer>
      </main>

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
    </div>
  );
}

function OfficeScene({
  agents,
  completedTaskCount,
  totalTaskCount,
  mode,
  draft,
  recentTasks,
  quickSkills: sceneQuickSkills,
  activeAgentId,
  onDraftChange,
  onSubmitPrompt,
  onModeChange,
  localLlmHealth,
  llmHealth,
  llmCostSummary,
  onAgentSelect,
  onQuickSkill,
  safetyAlert
}: {
  agents: OfficeAgentDefinition[];
  completedTaskCount: number;
  totalTaskCount: number;
  mode: AssistantMode;
  draft: string;
  recentTasks: TaskEvent[];
  quickSkills: typeof quickSkills;
  activeAgentId: string;
  onDraftChange: (value: string) => void;
  onSubmitPrompt: () => void;
  onModeChange: (mode: AssistantMode) => void;
  localLlmHealth: LocalLLMHealth | null;
  llmHealth: LLMHealthStatus | null;
  llmCostSummary: LLMCostSummary | null;
  onAgentSelect: (prompt: string) => void;
  onQuickSkill: (prompt: string) => void;
  safetyAlert: boolean;
}) {
  const initialOfficeAgentId = activeAgentId || "pm";
  const officeMapRef = useRef<HTMLDivElement | null>(null);
  const syncedActiveAgentIdRef = useRef(initialOfficeAgentId);
  const [officeMapSize, setOfficeMapSize] = useState<OfficeMapSize>({ width: 0, height: 0 });
  const [workingAgentId, setWorkingAgentId] = useState<string>(initialOfficeAgentId);
  const [agentState, setAgentState] = useState<Record<string, OfficeAgentRuntime>>(() =>
    createOfficeAgentState(agents, initialOfficeAgentId, true)
  );
  const [movingAgents, setMovingAgents] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const element = officeMapRef.current;
    if (!element) return;

    const updateMapSize = () => {
      const rect = element.getBoundingClientRect();
      setOfficeMapSize({ width: rect.width, height: rect.height });
    };

    updateMapSize();
    const resizeObserver = new ResizeObserver(updateMapSize);
    resizeObserver.observe(element);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    let walkClearId: number | undefined;
    const intervalId = window.setInterval(() => {
      setAgentState((current) => {
        const next = createOfficeAgentState(agents, workingAgentId, true);
        const moving = new Set<string>();
        for (const agent of agents) {
          const prev = current[agent.id];
          const incoming = next[agent.id];
          if (!prev || !incoming) continue;
          const dx = Math.abs(prev.x - incoming.x);
          const dy = Math.abs(prev.y - incoming.y);
          if (dx > 1.5 || dy > 1.5) moving.add(agent.id);
        }
        if (moving.size > 0) {
          setMovingAgents(moving);
          if (walkClearId) window.clearTimeout(walkClearId);
          walkClearId = window.setTimeout(() => setMovingAgents(new Set()), 4200);
        }
        return next;
      });
    }, 14000);

    return () => {
      window.clearInterval(intervalId);
      if (walkClearId) window.clearTimeout(walkClearId);
    };
  }, [agents, workingAgentId]);

  useEffect(() => {
    if (activeAgentId && activeAgentId !== syncedActiveAgentIdRef.current) {
      syncedActiveAgentIdRef.current = activeAgentId;
      setWorkingAgentId(activeAgentId);
      setAgentState(createOfficeAgentState(agents, activeAgentId, true));
    }
  }, [activeAgentId, agents]);

  const activateAgent = (agent: OfficeAgentDefinition) => {
    setWorkingAgentId(agent.id);
    setAgentState(createOfficeAgentState(agents, agent.id, true));
    onAgentSelect(agent.prompt);
  };

  const runningTaskCount = recentTasks.filter((task) => task.state === "running" || task.state === "queued").length;
  const blockedTaskCount = recentTasks.filter((task) => task.state === "blocked").length;
  const displayedTasks = recentTasks.slice(0, 3);
  const tokenUsed = llmCostSummary?.totalTokens ?? null;
  const tokenLimit = llmHealth?.active.profile.modelProfile.contextWindow ?? 0;
  const tokenUsedLabel = tokenUsed === null ? "N/A" : formatTokenCount(tokenUsed);
  const tokenLimitLabel = tokenLimit > 0 ? formatTokenCount(tokenLimit) : "N/A";
  const costLabel = llmCostSummary?.totalCostUsd === null || llmCostSummary?.totalCostUsd === undefined
    ? "N/A"
    : `$${llmCostSummary.totalCostUsd.toFixed(4)}`;
  const activeProfile = llmHealth?.active.profile;
  const activeProviderLabel = activeProfile
    ? `${activeProfile.activeBackend || activeProfile.providerName} ? ${activeProfile.model || "model"}`
    : "N/A";
  const activeAgent = agents.find((agent) => agent.id === workingAgentId) ?? agents[0];
  const isOfficeMapReady = officeMapSize.width > 0 && officeMapSize.height > 0;

  return (
    <div className="office-workspace" aria-label="Marvis 办公室">
      <div className="office-stage">
        <div className="office-headline">
          <div className="office-headline__title">
            <h1>Marvis 办公室</h1>
            <p>七只小马 Agent 协作中，安全审核 Agent 全程监督</p>
          </div>
          <div className="office-headline__legend">
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: activeAgent.accent
              }}
            />
            正在工作 · <strong>{activeAgent.name}</strong>
          </div>
        </div>

        <div className="office-map" ref={officeMapRef}>
          <OfficeSceneSVG />

          <span className="office-zone-label office-zone-label--pantry">茶水间</span>
          <span className="office-zone-label office-zone-label--gym">健身区</span>
          <span className="office-zone-label office-zone-label--lounge">休息区</span>
          <span className="office-zone-label office-zone-label--restroom">洗手间</span>
          <span className="office-zone-label office-zone-label--workstations">工位区</span>
          <span className="office-zone-label office-zone-label--meeting">会议白板</span>

          <div className={`office-patrol-scan ${safetyAlert ? "office-patrol-scan--active" : ""}`} />

          <div className={`office-agents ${isOfficeMapReady ? "office-agents--ready" : ""}`}>
            {isOfficeMapReady
              ? agents.map((agent) => (
                  <OfficeAgent
                    key={agent.id}
                    agent={agent}
                    state={agentState[agent.id]}
                    mapSize={officeMapSize}
                    isWorking={workingAgentId === agent.id}
                    isMoving={movingAgents.has(agent.id)}
                    onSelect={() => activateAgent(agent)}
                  />
                ))
              : null}
          </div>
        </div>

        <div className="office-command-dock">
          <div className="office-command-dock__hint">
            <span>对 Marvis 说</span>
            <span>·</span>
            <span><CornerDownLeft size={11} aria-hidden="true" /> Ctrl + Enter 发送</span>
          </div>
          <textarea
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                onSubmitPrompt();
              }
            }}
            placeholder="例如：找出真正吃空间的大文件，给我靠谱的清理建议"
          />
          <div className="command-footer">
            <div className="mode-tabs">
              {(["privacy", "hybrid", "efficiency"] as AssistantMode[]).map((item) => (
                <button
                  key={item}
                  className={item === mode ? "mode-pill mode-pill--active" : "mode-pill"}
                  onClick={() => onModeChange(item)}
                  type="button"
                  title={modeCopy[item].body}
                >
                  {modeCopy[item].title}
                </button>
              ))}
            </div>
            <button className="send-orb" aria-label="发送" onClick={() => onSubmitPrompt()} type="button">
              <CornerDownLeft size={16} aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <aside className="office-inspector" aria-label="Office status">
        <div className="inspector-card token-card">
          <div className="inspector-card__head">
            <strong>Today Token Usage</strong>
            <span className="token-card__chip">{llmCostSummary ? `${llmCostSummary.calls} calls` : "N/A"}</span>
          </div>
          <div className="token-card__value">
            <strong>
              {tokenUsedLabel}
              <small>/ {tokenLimitLabel}</small>
            </strong>
            <em>{activeProviderLabel}</em>
          </div>
          {mode === "privacy" && localLlmHealth ? (
            <div
              className="token-card__health"
              style={{
                marginTop: 6,
                fontSize: 11,
                display: "flex",
                alignItems: "center",
                gap: 6,
                color: localLlmHealth.available ? "#2f9e44" : "#e8590c"
              }}
              role="status"
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  background: localLlmHealth.available ? "#2f9e44" : "#e8590c"
                }}
                aria-hidden="true"
              />
              {localLlmHealth.available
                ? `Local inference ready - ${localLlmHealth.selectedBackend?.kind ?? "local"}${
                    localLlmHealth.selectedBackend?.model ? ` - ${localLlmHealth.selectedBackend.model}` : ""
                  }`
                : "No local LLM detected; privacy mode fails closed"}
            </div>
          ) : null}
          <div className="token-card__bar" />
        </div>

        <div className="inspector-card token-card token-card--saved">
          <div className="inspector-card__head">
            <strong>Today LLM Cost</strong>
            <span className="token-card__chip">{llmCostSummary?.estimated ? "Estimated" : llmCostSummary ? "Actual" : "N/A"}</span>
          </div>
          <div className="token-card__value">
            <strong>
              {costLabel}
              <small>{llmCostSummary ? `${llmCostSummary.windowHours}h` : ""}</small>
            </strong>
            <em>{llmCostSummary?.lastEventAt ? `Last call ${new Date(llmCostSummary.lastEventAt).toLocaleTimeString()}` : "No usage telemetry yet"}</em>
          </div>
          <div className="token-card__bar" />
        </div>

        <div className="inspector-card task-list-card">
          <div className="inspector-card__head">
            <strong>对话明细</strong>
            <button type="button">全部 ›</button>
          </div>
          <div className="metric-row">
            <div>
              <strong>{runningTaskCount}</strong>
              <span>进行中</span>
            </div>
            <div>
              <strong>{completedTaskCount}</strong>
              <span>已完成</span>
            </div>
            <div>
              <strong>{totalTaskCount}</strong>
              <span>总计</span>
            </div>
          </div>

          <div className="task-list-card__list" style={{ marginTop: 14 }}>
            {displayedTasks.map((task) => (
              <button key={task.id} type="button" className="task-row">
                <span className={`task-row__dot task-row__dot--${task.state}`}>
                  {task.state === "completed" ? (
                    <CheckCircle2 size={14} aria-hidden="true" />
                  ) : task.state === "blocked" || task.state === "failed" ? (
                    <ShieldCheck size={14} aria-hidden="true" />
                  ) : (
                    <Clock size={14} aria-hidden="true" />
                  )}
                </span>
                <div className="task-row__body">
                  <strong>{task.title}</strong>
                  <em>
                    {task.state === "completed"
                      ? "已完成"
                      : task.state === "running"
                        ? "执行中"
                        : task.state === "blocked"
                          ? "待审批"
                          : task.state === "failed"
                            ? "失败"
                            : "排队中"}
                  </em>
                </div>
                <time className="task-row__time">
                  {new Date(task.updatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </time>
              </button>
            ))}
            {blockedTaskCount > 0 ? (
              <span style={{ fontSize: 11, color: "var(--amber)", fontWeight: 700, paddingLeft: 8 }}>
                · 有 {blockedTaskCount} 个任务等待审批
              </span>
            ) : null}
          </div>
        </div>

        <div className="office-quick-actions">
          {sceneQuickSkills.slice(0, 4).map((skill) => (
            <button key={skill.title} type="button" onClick={() => onQuickSkill(skill.prompt)}>
              <skill.icon size={14} aria-hidden="true" />
              <span>{skill.title}</span>
            </button>
          ))}
        </div>
      </aside>
    </div>
  );
}

function OfficeAgent({
  agent,
  state,
  mapSize,
  isWorking,
  isMoving,
  onSelect
}: {
  agent: OfficeAgentDefinition;
  state?: OfficeAgentRuntime;
  mapSize: OfficeMapSize;
  isWorking: boolean;
  isMoving: boolean;
  onSelect: () => void;
}) {
  const runtime = state ?? {
    x: agent.x,
    y: agent.y,
    activity: agent.activities[0],
    pose: "working" as OfficeAgentPose
  };
  const targetPose: OfficeAgentPose = isWorking ? "working" : agent.id === "safety" ? "review" : runtime.pose;
  const isWalkingToRest = isMoving && targetPose === "nap";
  const pose: OfficeAgentPose = isWalkingToRest ? "wander" : targetPose;
  const activity = isWalkingToRest ? "去休息区" : runtime.activity;
  const isLead = agent.scale === "lead" && isWorking;
  const screenPosition = projectOfficePoint(runtime.x, runtime.y, mapSize);
  const style = {
    left: `${screenPosition.x}px`,
    top: `${screenPosition.y}px`,
    "--agent-accent": agent.accent,
    "--agent-glow": agent.glow,
    "--agent-delay": `${agent.delay}s`,
    "--agent-duration": `${agent.duration}s`
  } as CSSProperties;

  return (
    <button
      type="button"
      className={`office-agent office-agent--${pose} ${isWorking ? "office-agent--active" : "office-agent--idle"} ${isMoving ? "office-agent--moving" : ""} ${isLead ? "office-agent--lead" : ""}`}
      style={style}
      onClick={onSelect}
      aria-label={`${agent.name}，${agent.role}，${activity}`}
    >
      <span className="office-agent__halo" aria-hidden="true" />
      <span className="office-agent__bubble">{activity}</span>
      <PonyAgent accent={agent.accent} pose={pose} isLead={isLead} isWorking={isWorking} />
      <span className="office-agent__label">
        <strong>{agent.name}</strong>
        <span>{agent.role}</span>
      </span>
    </button>
  );
}

function PonyAgent({ accent, pose, isLead, isWorking }: PonyAgentProps) {
  const reactId = useId();
  const idPrefix = `pony-${reactId.replace(/[^a-z0-9_-]+/gi, "")}`;
  const shadowFilterId = `${idPrefix}-shadow`;
  const bodyGradId = `${idPrefix}-body`;

  return (
    <span
      className={`pony-agent-svg ${isLead ? "pony-agent-svg--lead" : ""} pony-agent-svg--${pose} ${isWorking ? "pony-agent-svg--working" : "pony-agent-svg--idle"}`}
      aria-hidden="true"
      style={{ "--agent-accent": accent } as CSSProperties}
    >
      <svg
        className="pony-agent-svg__art"
        viewBox="0 0 128 128"
        aria-hidden="true"
        focusable="false"
      >
        <defs>
          <linearGradient id={bodyGradId} x1="20%" y1="0%" x2="90%" y2="100%">
            <stop offset="0%" stopColor="#1a1d24" />
            <stop offset="60%" stopColor="#08090d" />
            <stop offset="100%" stopColor="#000000" />
          </linearGradient>
          <filter id={shadowFilterId} x="-30%" y="-30%" width="160%" height="180%">
            <feDropShadow dx="0" dy="6" stdDeviation="4" floodColor="#000000" floodOpacity="0.22" />
          </filter>
        </defs>

        {/* 地面阴影 */}
        <ellipse className="svg-pony-shadow" cx="64" cy="116" rx="34" ry="5" />

        <g className="svg-pony" filter={`url(#${shadowFilterId})`}>
          {/* 尾巴（在身体后面） */}
          <path
            className="svg-pony-tail"
            d="M92 64 C112 56 118 76 104 90 C95 98 88 92 94 84 C86 88 84 76 92 64Z"
          />

          {/* 身体 */}
          <path
            className="svg-pony-body"
            d="M44 60 C56 44 88 44 100 62 C112 82 102 102 76 104 L54 102 C38 96 34 76 44 60Z"
            fill={`url(#${bodyGradId})`}
          />

          {/* 胸前白肚 */}
          <path
            className="svg-pony-belly"
            d="M46 70 C56 66 70 70 74 82 L66 100 L48 96 Z"
          />

          {/* 后腿（在前腿之前画） */}
          <g className="svg-pony-legs">
            <path className="svg-pony-leg svg-pony-leg--back-left" d="M85 92 L95 118 L83 120 L74 95Z" />
            <path className="svg-pony-leg svg-pony-leg--back-right" d="M78 96 L82 121 L70 122 L66 98Z" />
            <path className="svg-pony-leg svg-pony-leg--front-left" d="M52 92 L46 119 L34 117 L42 92Z" />
            <path className="svg-pony-leg svg-pony-leg--front-right" d="M64 94 L68 121 L56 122 L54 95Z" />
          </g>

          {/* 脖子 */}
          <path className="svg-pony-neck" d="M54 46 C66 42 76 48 76 60 C70 64 62 68 56 76 C50 64 48 52 54 46Z" />

          {/* 头部 */}
          <path
            className="svg-pony-head"
            d="M22 42 C24 24 46 18 60 28 C74 38 74 58 60 66 C46 74 26 66 22 50 L12 46 Z"
          />

          {/* 耳朵 */}
          <path className="svg-pony-ear svg-pony-ear--left" d="M34 28 C30 12 42 8 50 22 L46 38Z" />
          <path className="svg-pony-ear svg-pony-ear--right" d="M52 26 C56 12 68 14 66 30 L60 40Z" />
          <path className="svg-pony-ear-inner" d="M38 24 C38 18 44 18 46 26 L44 33Z" />
          <path className="svg-pony-ear-inner" d="M55 24 C57 18 63 20 62 28 L60 34Z" />

          {/* 鬃毛 */}
          <path
            className="svg-pony-mane"
            d="M60 26 L78 18 L74 32 L88 30 L78 42 L92 46 L77 53 L86 62 L70 60 L72 72 L57 60 C66 50 67 38 60 26Z"
          />

          {/* 围巾 */}
          <path className="svg-pony-scarf" d="M50 62 L88 56 L94 70 L54 78 Z" />
          <path className="svg-pony-scarf-tail" d="M83 70 L106 80 L95 96 L78 78Z" />

          {/* 锁骨/胸前突起 */}
          <path className="svg-pony-chest" d="M44 70 C50 67 56 70 60 78 C55 81 48 80 44 70Z" />

          {/* 脸部 */}
          <g className="svg-pony-face">
            <ellipse className="svg-pony-cheek" cx="32" cy="50" rx="4" ry="2.4" />
            <circle className="svg-pony-eye" cx="42" cy="42" r="5.4" />
            <circle className="svg-pony-eye" cx="56" cy="42" r="5.4" />
            <circle className="svg-pony-pupil" cx="40.5" cy="42.5" r="1.9" />
            <circle className="svg-pony-pupil" cx="54.5" cy="42.5" r="1.9" />
            <circle cx="39.2" cy="41.2" r="0.9" fill="#ffffff" opacity="0.9" />
            <circle cx="53.2" cy="41.2" r="0.9" fill="#ffffff" opacity="0.9" />
            <path className="svg-pony-smile" d="M40 54 C44 57 50 57 53 53" />
          </g>

          {/* 配饰徽章（围巾上的徽章） */}
          <g className="svg-pony-badge">
            <circle className="svg-pony-badge-ring" cx="68" cy="69" r="4" />
            <circle cx="68" cy="69" r="2.5" />
          </g>

          {/* 配件：咖啡杯（coffee 姿势） */}
          <g className="svg-pony-cup">
            <rect className="svg-pony-cup-mug" x="14" y="74" width="14" height="15" rx="2.5" />
            <path className="svg-pony-cup-handle" d="M28 78 C34 78 34 86 28 86" />
            <path className="svg-pony-cup-steam" d="M18 70 C15 66 22 64 18 60" />
            <path className="svg-pony-cup-steam" d="M24 70 C21 66 28 64 24 60" />
          </g>

          {/* 配件：键盘（working 姿势） */}
          <g className="svg-pony-keys">
            <rect x="46" y="108" width="38" height="8" rx="2" fill="#dadce3" />
            <rect x="49" y="111" width="6" height="2" rx="0.6" fill="#9aa1ad" />
            <rect x="58" y="111" width="6" height="2" rx="0.6" fill="#9aa1ad" />
            <rect x="67" y="111" width="6" height="2" rx="0.6" fill="#9aa1ad" />
            <rect x="76" y="111" width="5" height="2" rx="0.6" fill="#9aa1ad" />
          </g>

          {/* 配件：Z 气泡（nap） */}
          {pose === "nap" ? (
            <g className="svg-pony-zzz" aria-hidden="true">
              <text x="92" y="26" fontSize="12">Z</text>
              <text x="100" y="18" fontSize="9">z</text>
            </g>
          ) : null}

          {/* 配件：盾牌（review/safety） */}
          <g className="svg-pony-shield">
            <path d="M100 30 L114 30 L114 42 C114 50 107 56 107 56 C107 56 100 50 100 42 Z" />
            <path d="M104 40 L106 43 L110 38" fill="none" stroke="#ffffff" strokeWidth="1.6" strokeLinecap="round" />
          </g>
        </g>
      </svg>
    </span>
  );
}

function createOfficeAgentState(
  agents: OfficeAgentDefinition[],
  workingAgentId: string,
  shouldWander: boolean
): Record<string, OfficeAgentRuntime> {
  const leisureOffset = Math.floor(Math.random() * leisureSpots.length);
  let idleIndex = 0;

  return Object.fromEntries(
    agents.map((agent) => {
      if (agent.id === workingAgentId || !shouldWander) {
        const seat = officeWorkSeats[agent.id] ?? { x: agent.x, y: agent.y };
        const activity = "坐在办公桌前敲击键盘";

        return [agent.id, { x: seat.x, y: seat.y, activity, pose: "working" as OfficeAgentPose }];
      }

      // 安全审核 Agent 非工作时巡逻；进入工作态时也必须坐回办公桌前。
      if (agent.id === "safety") {
        const routeIndex = Math.floor(Date.now() / 14000) % safetyPatrolRoute.length;
        const point = safetyPatrolRoute[routeIndex] ?? safetyPatrolRoute[0];
        const x = point.x + randomBetween(-6, 6);
        const y = point.y + randomBetween(-6, 6);
        return [agent.id, { x, y, activity: "巡逻审核", pose: "review" as OfficeAgentPose }];
      }

      const spot = leisureSpots[(idleIndex + leisureOffset) % leisureSpots.length] ?? leisureSpots[0];
      idleIndex += 1;
      const x = clamp(spot.x + randomBetween(-8, 8), 0, officeViewBox.width);
      const y = clamp(spot.y + randomBetween(-8, 8), 0, officeViewBox.height);

      return [agent.id, { x, y, activity: spot.activity, pose: spot.pose }];
    })
  );
}

function inferActiveOfficeAgentId(
  tasks: TaskEvent[],
  plan: Plan,
  conversations: AgentConversation[],
  safetyStatus: SafetyReview["status"]
) {
  if (safetyStatus === "needs_review" || safetyStatus === "blocked") {
    return "safety";
  }

  const activeTask = tasks.find((task) => task.state === "running" || task.state === "queued" || task.state === "blocked");
  const activeStep = plan.steps.find((step) => step.state === "active" || step.state === "blocked");
  const latestAgentMessage = conversations
    .flatMap((conversation) => conversation.messages)
    .filter((message) => message.agent || message.name)
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt))[0];

  return (
    agentIdFromText(activeTask?.agent) ||
    agentIdFromText(activeStep?.owner) ||
    agentIdFromText(latestAgentMessage?.agent || latestAgentMessage?.name) ||
    "pm"
  );
}

function latestStreamableTaskId(tasks: TaskEvent[]): string | null {
  const candidates = tasks.filter((task) => task.state === "running" || task.state === "queued" || task.state === "blocked");
  const task = candidates[0] ?? tasks[0];
  return task?.id ?? null;
}

function preserveStreamedRunConversations(
  current: AgentConversation[],
  incoming: AgentConversation[]
): AgentConversation[] {
  const byId = new Map(incoming.map((conversation) => [conversation.id, conversation]));
  for (const conversation of current) {
    if (!conversation.id.endsWith("-events") || conversation.messages.length === 0) continue;
    const snapshot = byId.get(conversation.id);
    if (!snapshot) {
      byId.set(conversation.id, conversation);
      continue;
    }
    const messageIds = new Set(snapshot.messages.map((message) => message.id));
    const streamedMessages = conversation.messages.filter((message) => !messageIds.has(message.id));
    if (streamedMessages.length) {
      byId.set(conversation.id, {
        ...snapshot,
        messages: [...snapshot.messages, ...streamedMessages].sort(
          (left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt)
        )
      });
    }
  }
  return Array.from(byId.values());
}

function mergeStreamedAgentMessage(
  taskId: string,
  message: {
    id: string;
    role?: ChatMessage["role"];
    name?: string;
    content: string;
    created_at: string;
    tool_calls?: AgentConversation["messages"][number]["toolCalls"];
    tool_call_id?: string;
    metadata?: Record<string, unknown>;
    from_agent?: string;
    message_type?: string;
  },
  setAgentConversations: Dispatch<SetStateAction<AgentConversation[]>>
) {
  setAgentConversations((current) => {
    const conversationIndex = current.findIndex((conversation) => conversation.id === `${taskId}-agents`);
    const conversation = current[conversationIndex] ?? {
      id: `${taskId}-agents`,
      title: "实时任务",
      status: "running" as const,
      messages: []
    };
    if (conversation.messages.some((item) => item.id === message.id)) {
      return current;
    }

    const agentName = message.name ?? String(message.metadata?.from_agent ?? message.from_agent ?? "assistant");
    const nextConversation: AgentConversation = {
      ...conversation,
      status: "running",
      messages: [
        ...conversation.messages,
        {
          id: message.id,
          role: message.role ?? "assistant",
          name: agentName,
          agent: agentName,
          content: message.content,
          createdAt: message.created_at,
          toolCalls: message.tool_calls,
          toolCallId: message.tool_call_id,
          metadata: message.metadata,
          kind: mapStreamAgentKind(String(message.metadata?.message_type ?? message.message_type ?? ""))
        }
      ]
    };

    if (conversationIndex < 0) {
      return [nextConversation, ...current];
    }
    return current.map((item, index) => (index === conversationIndex ? nextConversation : item));
  });
}

function mergeStreamedRunEvent(
  runId: string,
  event: {
    id: string;
    event?: string;
    name?: string;
    payload?: Record<string, unknown>;
    created_at: string;
  },
  setAgentConversations: Dispatch<SetStateAction<AgentConversation[]>>
) {
  setAgentConversations((current) => {
    const conversationIndex = current.findIndex((conversation) => conversation.id === `${runId}-events`);
    const conversation = current[conversationIndex] ?? {
      id: `${runId}-events`,
      title: "Run events",
      status: "running" as const,
      messages: []
    };
    if (conversation.messages.some((item) => item.id === event.id)) {
      return current;
    }

    const payload = event.payload ?? {};
    const eventName = String(event.event ?? event.name ?? "");
    const agentName = String(payload.from_agent ?? "ExecutionEngine");
    const content = String(payload.content ?? payload.transition_reason ?? eventName);
    const nextConversation: AgentConversation = {
      ...conversation,
      status: eventName === "run.completed" ? "done" : eventName === "run.waiting_approval" ? "waiting" : "running",
      messages: [
        ...conversation.messages,
        {
          id: event.id,
          role: "assistant",
          name: agentName,
          agent: agentName,
          content,
          createdAt: event.created_at,
          metadata: { ...payload, event_type: eventName },
          kind: mapStreamRunEventKind(eventName)
        }
      ]
    };

    if (conversationIndex < 0) {
      return [nextConversation, ...current];
    }
    return current.map((item, index) => (index === conversationIndex ? nextConversation : item));
  });
}

function mapStreamAgentKind(kind: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (kind === "observation") return "observation";
  if (kind === "review" || kind === "critique") return "handoff";
  if (kind === "final") return "result";
  return "action";
}

function mapStreamRunEventKind(kind: string): NonNullable<AgentConversation["messages"][number]["kind"]> {
  if (kind === "tool.result" || kind === "run.completed") return "result";
  if (kind === "approval.needed" || kind === "run.waiting_approval") return "handoff";
  if (kind === "tool.progress") return "observation";
  return "action";
}

function agentIdFromText(value?: string) {
  const normalized = (value ?? "").toLowerCase();
  if (!normalized) return "";
  const localized = zhAgentName(value);
  if (normalized.includes("safety") || normalized.includes("human") || localized.includes("安全")) return "safety";
  if (normalized.includes("computer") || normalized.includes("system") || localized.includes("电脑")) return "computer";
  if (normalized.includes("browser") || localized.includes("浏览器")) return "browser";
  if (normalized.includes("search") || localized.includes("搜索")) return "search";
  if (normalized.includes("document") || normalized.includes("file") || normalized.includes("index") || localized.includes("文件") || localized.includes("文档") || localized.includes("索引")) return "file";
  if (normalized.includes("app") || localized.includes("应用")) return "app";
  if (normalized.includes("planner") || normalized.includes("orchestrator") || normalized.includes("pm") || localized.includes("规划") || localized.includes("调度")) return "pm";
  return "";
}

function randomBetween(min: number, max: number) {
  return min + Math.random() * (max - min);
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function projectOfficePoint(x: number, y: number, mapSize: OfficeMapSize) {
  if (mapSize.width <= 0 || mapSize.height <= 0) {
    return { x: 0, y: 0 };
  }

  const scale = Math.max(mapSize.width / officeViewBox.width, mapSize.height / officeViewBox.height);
  const renderedWidth = officeViewBox.width * scale;
  const renderedHeight = officeViewBox.height * scale;
  const offsetX = (mapSize.width - renderedWidth) / 2;
  const offsetY = (mapSize.height - renderedHeight) / 2;

  return {
    x: offsetX + x * scale,
    y: offsetY + y * scale
  };
}

function formatTokenCount(value: number): string {
  if (value <= 0) return "0";
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return value.toLocaleString();
}

function requiresLocalLlmHealth(mode: AssistantMode): boolean {
  return mode === "privacy" || mode === "hybrid";
}

function SideButton({
  icon: Icon,
  label,
  active,
  onClick
}: {
  icon: typeof Bot;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  return (
    <button className={active ? "side-button side-button--active" : "side-button"} onClick={onClick} type="button">
      <Icon size={15} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}

/* ============================================================
   原创 SVG 办公室场景
   ============================================================ */

function OfficeSceneSVG() {
  return (
    <svg
      className="office-scene-svg"
      viewBox="0 0 1000 700"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id="desk-top-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="100%" stopColor="#e9ecf3" />
        </linearGradient>
        <linearGradient id="monitor-screen" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#6f93ff" />
          <stop offset="100%" stopColor="#4a6cf7" />
        </linearGradient>
        <linearGradient id="chair-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#3a4566" />
          <stop offset="100%" stopColor="#1f2740" />
        </linearGradient>
        <linearGradient id="coffee-machine" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#3f4a66" />
          <stop offset="100%" stopColor="#1c2238" />
        </linearGradient>
        <linearGradient id="treadmill-belt" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2a3145" />
          <stop offset="100%" stopColor="#1a2032" />
        </linearGradient>
        <linearGradient id="sofa-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#d2b3ff" />
          <stop offset="100%" stopColor="#a279f5" />
        </linearGradient>
        <linearGradient id="counter-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#f5f0e6" />
          <stop offset="100%" stopColor="#d8c7a7" />
        </linearGradient>
        <linearGradient id="whiteboard-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="100%" stopColor="#f0f2f7" />
        </linearGradient>
        <radialGradient id="rug-grad" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#dbe2f5" />
          <stop offset="100%" stopColor="#bcc5e0" stopOpacity="0" />
        </radialGradient>
        <filter id="soft-shadow" x="-30%" y="-30%" width="160%" height="180%">
          <feDropShadow dx="0" dy="14" stdDeviation="14" floodColor="#1a2240" floodOpacity="0.1" />
        </filter>
        <filter id="light-shadow" x="-30%" y="-30%" width="160%" height="180%">
          <feDropShadow dx="0" dy="8" stdDeviation="8" floodColor="#1a2240" floodOpacity="0.08" />
        </filter>
      </defs>

      {/* 远处墙壁分割线（让左右分区更清晰） */}
      <line x1="370" y1="80" x2="370" y2="640" stroke="#e2e6ee" strokeWidth="1" strokeDasharray="5 6" opacity="0.5" />

      {/* 中央地毯 */}
      <ellipse cx="620" cy="500" rx="280" ry="80" fill="url(#rug-grad)" opacity="0.5" />

      {/* ============== 左侧设施 ============== */}

      {/* 茶水间 — 吧台 + 咖啡机 + 杯子 */}
      <g filter="url(#light-shadow)">
        <rect x="48" y="120" width="280" height="36" rx="6" fill="url(#counter-grad)" />
        <rect x="48" y="120" width="280" height="6" rx="3" fill="#bba47e" opacity="0.3" />

        {/* 咖啡机 */}
        <g transform="translate(70, 78)">
          <rect width="44" height="48" rx="6" fill="url(#coffee-machine)" />
          <rect x="6" y="6" width="32" height="18" rx="3" fill="#0d1322" />
          <rect x="10" y="10" width="24" height="2" rx="1" fill="#5ad7c8" />
          <rect x="10" y="14" width="16" height="2" rx="1" fill="#5ad7c8" opacity="0.6" />
          <rect x="14" y="30" width="16" height="3" rx="1.5" fill="#5ad7c8" />
          <rect x="18" y="38" width="8" height="6" rx="1" fill="#1a2032" />
          {/* 蒸汽 */}
          <path d="M16 70 C12 64 18 60 14 54" stroke="#bcc5e0" strokeWidth="1.8" fill="none" strokeLinecap="round" opacity="0.6" />
          <path d="M24 72 C20 66 26 62 22 56" stroke="#bcc5e0" strokeWidth="1.8" fill="none" strokeLinecap="round" opacity="0.5" />
        </g>

        {/* 咖啡杯 5 个 */}
        {[0, 1, 2, 3, 4].map((i) => (
          <g key={i} transform={`translate(${140 + i * 24}, 102)`}>
            <rect width="14" height="18" rx="3" fill="#d59a6b" />
            <ellipse cx="7" cy="17" rx="6" ry="2" fill="#a06b3f" opacity="0.5" />
            <path d="M15 5 C20 5 20 13 15 13" stroke="#a06b3f" strokeWidth="1.5" fill="none" strokeLinecap="round" />
          </g>
        ))}

        {/* 糖罐 */}
        <g transform="translate(280, 100)">
          <rect width="18" height="20" rx="4" fill="#f7d97c" />
          <rect x="3" y="3" width="12" height="3" rx="1" fill="#e8be4a" />
          <text x="9" y="16" fontSize="6" fontWeight="800" textAnchor="middle" fill="#9b7610">糖</text>
        </g>
      </g>

      {/* 健身区 — 跑步机 */}
      <g filter="url(#soft-shadow)" transform="translate(60, 230)">
        {/* 跑步带 */}
        <rect x="20" y="50" width="220" height="80" rx="14" fill="url(#treadmill-belt)" />
        <rect x="30" y="58" width="200" height="64" rx="8" fill="#0d1322" />
        {/* 跑步带条纹 */}
        {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => (
          <rect
            key={i}
            x={36 + i * 19}
            y="64"
            width="14"
            height="2"
            rx="1"
            fill="#4a6cf7"
            opacity="0.2"
          >
            <animate
              attributeName="opacity"
              values="0.1;0.5;0.1"
              dur="0.6s"
              begin={`${i * 0.06}s`}
              repeatCount="indefinite"
            />
          </rect>
        ))}
        {/* 控制台 */}
        <rect x="80" y="0" width="100" height="56" rx="8" fill="#1f2740" />
        <rect x="88" y="10" width="84" height="28" rx="4" fill="#0d1322" />
        <rect x="92" y="14" width="48" height="3" rx="1" fill="#5ad7c8" />
        <rect x="92" y="20" width="32" height="3" rx="1" fill="#5ad7c8" opacity="0.6" />
        <rect x="92" y="26" width="60" height="3" rx="1" fill="#5ad7c8" opacity="0.4" />
        {/* 两边把手 */}
        <rect x="22" y="0" width="6" height="60" rx="3" fill="#2a3145" />
        <rect x="232" y="0" width="6" height="60" rx="3" fill="#2a3145" />
      </g>

      {/* 休息区 — 沙发 + 抱枕 + 茶几 */}
      <g filter="url(#soft-shadow)" transform="translate(50, 420)">
        {/* 沙发主体 */}
        <rect x="0" y="40" width="280" height="60" rx="14" fill="url(#sofa-grad)" />
        {/* 沙发靠背 */}
        <rect x="6" y="10" width="268" height="46" rx="12" fill="#a279f5" opacity="0.85" />
        {/* 三个坐垫分隔 */}
        <line x1="98" y1="50" x2="98" y2="98" stroke="#7c5cd1" strokeWidth="1.5" opacity="0.6" />
        <line x1="180" y1="50" x2="180" y2="98" stroke="#7c5cd1" strokeWidth="1.5" opacity="0.6" />
        {/* 抱枕 */}
        <rect x="20" y="22" width="36" height="32" rx="6" fill="#ffe4ec" transform="rotate(-4 38 38)" />
        <rect x="225" y="22" width="36" height="32" rx="6" fill="#fff8dc" transform="rotate(5 243 38)" />
        {/* 茶几 */}
        <rect x="86" y="106" width="108" height="20" rx="4" fill="url(#desk-top-grad)" />
        <rect x="98" y="111" width="12" height="3" rx="1" fill="#4a6cf7" opacity="0.4" />
        <rect x="120" y="111" width="40" height="6" rx="1" fill="#19a37a" opacity="0.4" />
      </g>

      {/* 洗手间 — 侧视马桶 */}
      <g filter="url(#soft-shadow)" transform="translate(46, 541)">
        <rect x="0" y="5" width="230" height="46" rx="2" fill="#f7f8fb" />
        <rect x="0" y="5" width="230" height="18" fill="#eef1f6" />
        <path d="M0 51 H230 L210 76 H22 Z" fill="#ffffff" opacity="0.95" />
        <ellipse cx="82" cy="70" rx="58" ry="8" fill="#1f2740" opacity="0.1" />

        <g transform="translate(48, 4)">
          <rect x="0" y="12" width="38" height="24" rx="3" fill="#ffffff" stroke="#dce2eb" strokeWidth="1.4" />
          <path d="M10 12 L20 2 L27 4 L22 18 Z" fill="#ffffff" stroke="#dce2eb" strokeWidth="1.4" />
          <path d="M24 5 L29 22" stroke="#cfd6e2" strokeWidth="2" strokeLinecap="round" />
          <path d="M38 36 C54 35 69 42 73 53" fill="none" stroke="#cfd6e2" strokeWidth="2.4" strokeLinecap="round" />
          <path d="M0 36 H58 C67 36 74 43 74 51 C74 60 67 66 56 66 H16 C7 66 0 59 0 50 Z" fill="#ffffff" stroke="#dce2eb" strokeWidth="1.4" />
          <ellipse cx="60" cy="50" rx="28" ry="12" fill="#ffffff" stroke="#dce2eb" strokeWidth="1.4" />
          <ellipse cx="60" cy="50" rx="17" ry="6" fill="#c8cdd4" opacity="0.82" />
          <path d="M16 66 H56 L62 72 H10 Z" fill="#ffffff" stroke="#dce2eb" strokeWidth="1.2" />
        </g>

        <g transform="translate(166, 14)">
          <rect x="-2" y="-2" width="32" height="24" rx="3" fill="#ffffff" stroke="#e1e5ec" strokeWidth="1.3" />
          <ellipse cx="14" cy="10" rx="8" ry="9" fill="#edf1f6" />
          <ellipse cx="14" cy="10" rx="3.5" ry="4.5" fill="#ffffff" />
          <path d="M30 0 L36 6 V22 H30 Z" fill="#e7ebf1" />
        </g>
      </g>

      {/* ============== 右侧 6 个工位 + 会议区 ============== */}

      {/* 会议白板（顶部） */}
      <g filter="url(#light-shadow)" transform="translate(420, 80)">
        <rect x="0" y="0" width="500" height="100" rx="10" fill="url(#whiteboard-grad)" />
        <rect x="4" y="4" width="492" height="92" rx="6" fill="#ffffff" />
        {/* 白板内容 */}
        <text x="20" y="28" fontSize="13" fontWeight="800" fill="#16203a">今日工作流</text>
        <line x1="20" y1="36" x2="80" y2="36" stroke="#4a6cf7" strokeWidth="2" />
        <text x="20" y="56" fontSize="10" fill="#3a4566">1. 拆解目标 → 派发任务</text>
        <text x="20" y="72" fontSize="10" fill="#3a4566">2. 多 Agent 协作 → 安全审核</text>
        <text x="20" y="88" fontSize="10" fill="#3a4566">3. 反馈给用户 → 持续优化</text>
        {/* 便签 */}
        <rect x="200" y="14" width="60" height="60" rx="3" fill="#fff7c0" transform="rotate(-3 230 44)" />
        <text x="230" y="44" fontSize="10" fontWeight="700" textAnchor="middle" fill="#9b7610" transform="rotate(-3 230 44)">本周</text>
        <text x="230" y="58" fontSize="9" textAnchor="middle" fill="#9b7610" transform="rotate(-3 230 44)">OKR</text>

        <rect x="280" y="14" width="60" height="60" rx="3" fill="#d2f7e3" transform="rotate(4 310 44)" />
        <text x="310" y="44" fontSize="10" fontWeight="700" textAnchor="middle" fill="#19a37a" transform="rotate(4 310 44)">协作</text>
        <text x="310" y="58" fontSize="9" textAnchor="middle" fill="#19a37a" transform="rotate(4 310 44)">原则</text>

        <rect x="360" y="14" width="60" height="60" rx="3" fill="#fde0e8" transform="rotate(-2 390 44)" />
        <text x="390" y="44" fontSize="10" fontWeight="700" textAnchor="middle" fill="#d8475c" transform="rotate(-2 390 44)">安全</text>
        <text x="390" y="58" fontSize="9" textAnchor="middle" fill="#d8475c" transform="rotate(-2 390 44)">优先</text>
      </g>

      {/* 6 个工位 (2 列 × 3 排) — 每个工位都有显示器 + 桌面 + 椅子 */}
      {[
        { x: 470, y: 220 },
        { x: 720, y: 220 },
        { x: 470, y: 380 },
        { x: 720, y: 380 },
        { x: 470, y: 540 },
        { x: 720, y: 540 }
      ].map((pos, idx) => (
        <g key={idx} filter="url(#light-shadow)" transform={`translate(${pos.x}, ${pos.y})`}>
          {/* 桌面 */}
          <rect x="0" y="38" width="180" height="56" rx="3" fill="url(#desk-top-grad)" />
          <rect x="0" y="38" width="180" height="3" fill="#dadce3" />
          {/* 桌腿（透视） */}
          <path d="M6 94 L12 130 L20 130 L20 94 Z" fill="#bcc5e0" />
          <path d="M160 94 L168 130 L174 130 L174 94 Z" fill="#bcc5e0" />
          {/* 显示器 */}
          <rect x="40" y="0" width="100" height="46" rx="4" fill="#1f2740" />
          <rect x="44" y="4" width="92" height="38" rx="2" fill="url(#monitor-screen)" />
          {/* 屏幕里的内容（蓝色波形 + 文字行） */}
          <rect x="48" y="8" width="40" height="2.5" rx="1" fill="#ffffff" opacity="0.75" />
          <rect x="48" y="14" width="60" height="2.5" rx="1" fill="#ffffff" opacity="0.45" />
          <rect x="48" y="20" width="32" height="2.5" rx="1" fill="#ffffff" opacity="0.55" />
          <path
            d="M48 32 L56 28 L64 34 L72 30 L80 33 L88 27 L96 33 L104 30 L112 32 L120 28 L128 32"
            stroke="#ffffff"
            strokeWidth="1.4"
            fill="none"
            opacity="0.8"
          />
          {/* 显示器支架 */}
          <rect x="85" y="46" width="10" height="6" fill="#1f2740" />
          <rect x="74" y="50" width="32" height="3" rx="1" fill="#1f2740" />
          {/* 键盘 */}
          <rect x="56" y="58" width="68" height="10" rx="2" fill="#e9ecf3" />
          <rect x="62" y="62" width="56" height="2" rx="0.5" fill="#bcc5e0" opacity="0.7" />
          {/* 鼠标 */}
          <ellipse cx="135" cy="64" rx="4" ry="6" fill="#e9ecf3" />
          {/* 椅子 */}
          <g transform="translate(60, 96)">
            <ellipse cx="30" cy="38" rx="26" ry="6" fill="#1f2740" opacity="0.3" />
            <rect x="2" y="10" width="56" height="24" rx="6" fill="url(#chair-grad)" />
            <rect x="6" y="-8" width="48" height="22" rx="6" fill="url(#chair-grad)" />
            <rect x="28" y="32" width="4" height="14" fill="#3a4566" />
          </g>
        </g>
      ))}

      {/* 顶部柔光 */}
      <ellipse cx="500" cy="40" rx="380" ry="40" fill="#ffffff" opacity="0.5" />

      {/* 绿植装饰 */}
      <g filter="url(#light-shadow)" transform="translate(340, 240)">
        <rect x="0" y="40" width="28" height="32" rx="3" fill="#9c5d36" />
        <path d="M14 0 C4 0 0 14 6 24 C2 24 0 32 14 32 C28 32 26 24 22 24 C28 14 24 0 14 0Z" fill="#3aa66d" />
        <path d="M8 14 C6 18 8 22 14 22" stroke="#2d8253" strokeWidth="1.4" fill="none" />
        <path d="M20 14 C22 18 20 22 14 22" stroke="#2d8253" strokeWidth="1.4" fill="none" />
      </g>

      <g filter="url(#light-shadow)" transform="translate(340, 480)">
        <rect x="0" y="36" width="24" height="28" rx="3" fill="#9c5d36" />
        <path d="M12 4 C2 6 0 16 6 22 C2 24 4 30 12 28 C20 30 22 24 18 22 C24 16 22 6 12 4Z" fill="#5fb886" />
      </g>
    </svg>
  );
}
