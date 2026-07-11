import {
  Activity,
  Bell,
  BookOpenText,
  Brain,
  CheckCircle2,
  CircleDashed,
  FolderOpen,
  Globe,
  Home,
  Image as ImageIcon,
  Laptop,
  Loader2,
  MessageSquarePlus,
  Puzzle,
  RefreshCw,
  Settings,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  TriangleAlert,
  X,
  type LucideIcon
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

import type { ChatMessage } from "../../../shared/catalogTypes";
import type { TaskEvent } from "../../../shared/executionTypes";
import type { SystemInfo } from "../../../shared/systemTypes";
import xiaomaErrorStill from "../../assets/xiaoma-agent/sleeping_still.png?no-inline";
import xiaomaCompletedStill from "../../assets/xiaoma-agent/salute_still.png?no-inline";
import xiaomaRunningGif from "../../assets/xiaoma-agent/working.gif";
import xiaomaRunningStill from "../../assets/xiaoma-agent/working_still.png?no-inline";
import xiaomaStandbyStill from "../../assets/xiaoma-agent/standby_still.png?no-inline";
import { libraryFamilyForView } from "../../views/localLibrarySections";
import { useUiPreferences, type EffectiveMotion } from "../../lib/uiPreferences";
import type { ConnectionState, ViewKey } from "../../store";

export interface NavItem {
  icon: LucideIcon;
  label: string;
  view: ViewKey;
}

export interface NavGroup {
  label?: string;
  items: NavItem[];
}

export interface RecentDialogue {
  label: string;
  onSelect: () => void;
}

export interface ViewTitle {
  title: string;
  subtitle: string;
}

export const primaryNavGroups: NavGroup[] = [
  {
    items: [
      { icon: Home, label: "首页", view: "home" },
      { icon: MessageSquarePlus, label: "对话", view: "chat" },
      { icon: TerminalSquare, label: "进度", view: "agents" },
      { icon: Settings, label: "设置", view: "settings" }
    ]
  },
  {
    label: "本地内容",
    items: [
      { icon: BookOpenText, label: "知识库", view: "documents" },
      { icon: ImageIcon, label: "图库", view: "gallery" }
    ]
  },
  {
    items: [
      { icon: FolderOpen, label: "文件工具", view: "files" },
      { icon: Laptop, label: "此电脑", view: "computer" }
    ]
  },
  {
    label: "更多",
    items: [
      { icon: Puzzle, label: "技能", view: "skills" },
      { icon: ShieldCheck, label: "审批", view: "safety" },
      { icon: Brain, label: "记忆", view: "memories" },
      { icon: Globe, label: "浏览器", view: "browser" }
    ]
  }
];

export const primaryNavItems: NavItem[] = primaryNavGroups.flatMap((group) => group.items);

export const viewTitles: Record<ViewKey, ViewTitle> = {
  browser: { title: "浏览器监看", subtitle: "查看浏览器活动，并在需要时接管控制" },
  home: { title: "首页", subtitle: "让 Lengrvis 帮你处理电脑上的事务" },
  chat: { title: "对话", subtitle: "自然对话，全程 Agent 协作" },
  apps: { title: "应用", subtitle: "直接查看本地应用和脚本" },
  documents: { title: "文档", subtitle: "直接查看本地文档内容" },
  documentOcr: { title: "文档识别", subtitle: "定位本地文档并按需识别" },
  papers: { title: "论文", subtitle: "浏览本地论文和研究材料" },
  courseware: { title: "课件", subtitle: "浏览本地课件和讲义" },
  reports: { title: "报告", subtitle: "浏览本地报告和总结" },
  gallery: { title: "图库", subtitle: "直接查看本地图片" },
  imageOcr: { title: "图片识别", subtitle: "浏览图片并按需识别" },
  people: { title: "人物印象", subtitle: "查看本地人物相关图片" },
  places: { title: "足迹地点", subtitle: "查看本地地点相关图片" },
  timeline: { title: "时光长廊", subtitle: "按时间查看本地图片" },
  files: { title: "文件工具", subtitle: "查找、整理和分析你的文件" },
  computer: { title: "此电脑", subtitle: "检查这台设备并打开常用工具" },
  agents: { title: "进度", subtitle: "当前正在处理的工作" },
  memories: { title: "记忆", subtitle: "Lengrvis 后续可使用的本地信息" },
  safety: { title: "审批", subtitle: "等待你确认的项目" },
  skills: { title: "技能", subtitle: "查看已安装的技能包及其权限边界" },
  settings: { title: "设置", subtitle: "隐私模式、本地 AI、安全权限和运行配置" }
};

export function ShellFrame({
  activeView,
  connectionState,
  isLoading,
  onViewChange,
  onRefresh,
  onOpenApprovals,
  hasPendingApproval,
  messages = [],
  tasks = [],
  systemInfo,
  children
}: {
  activeView: ViewKey;
  connectionState: ConnectionState;
  isLoading: boolean;
  onViewChange: (view: ViewKey) => void;
  onRefresh: () => void;
  onOpenApprovals: () => void;
  hasPendingApproval: boolean;
  messages?: ChatMessage[];
  tasks?: TaskEvent[];
  systemInfo?: SystemInfo;
  children: ReactNode;
}) {
  const viewMeta = viewTitles[activeView];
  const connection = getConnectionSummary(connectionState, isLoading);
  const workbench = getWorkbenchState({ isLoading, messages, tasks, connectionState });
  const health = getComputerHealthSummary(systemInfo);
  const recentReadableMessages = getRecentReadableMessages(messages);
  const activityCenterLabel = stateLabel(workbench.state, connection.state);
  const { effectiveMotion } = useUiPreferences();
  const [isActivityCenterMounted, setIsActivityCenterMounted] = useState(false);
  const [isActivityCenterOpen, setIsActivityCenterOpen] = useState(false);
  const activityCenterTriggerRef = useRef<HTMLButtonElement>(null);
  const activityCenterPanelRef = useRef<HTMLElement>(null);
  const activityCenterCloseTimerRef = useRef<number | undefined>(undefined);

  const openActivityCenter = useCallback(() => {
    if (activityCenterCloseTimerRef.current !== undefined) {
      window.clearTimeout(activityCenterCloseTimerRef.current);
      activityCenterCloseTimerRef.current = undefined;
    }
    setIsActivityCenterMounted(true);
    setIsActivityCenterOpen(true);
  }, []);

  const closeActivityCenter = useCallback(() => {
    if (!isActivityCenterMounted) return;
    setIsActivityCenterOpen(false);
    if (activityCenterCloseTimerRef.current !== undefined) {
      window.clearTimeout(activityCenterCloseTimerRef.current);
    }
    const closeDelay = effectiveMotion === "reduced" ? 0 : 280;
    activityCenterCloseTimerRef.current = window.setTimeout(() => {
      activityCenterCloseTimerRef.current = undefined;
      setIsActivityCenterMounted(false);
      window.requestAnimationFrame(() => activityCenterTriggerRef.current?.focus());
    }, closeDelay);
  }, [effectiveMotion, isActivityCenterMounted]);

  useEffect(() => {
    if (isActivityCenterMounted) closeActivityCenter();
    // Closing on navigation intentionally follows the same exit/focus path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeView]);

  useEffect(() => () => {
    if (activityCenterCloseTimerRef.current !== undefined) {
      window.clearTimeout(activityCenterCloseTimerRef.current);
    }
  }, []);

  useEffect(() => {
    if (!isActivityCenterMounted) return undefined;
    const previousHtmlOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";

    const handleActivityCenterKeys = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeActivityCenter();
        return;
      }
      if (event.key !== "Tab") return;

      const panel = activityCenterPanelRef.current;
      if (!panel) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter((element) => element.getClientRects().length > 0);
      if (!focusable.length) {
        event.preventDefault();
        panel.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleActivityCenterKeys);
    return () => {
      window.removeEventListener("keydown", handleActivityCenterKeys);
      document.documentElement.style.overflow = previousHtmlOverflow;
      document.body.style.overflow = previousBodyOverflow;
    };
  }, [closeActivityCenter, isActivityCenterMounted, isActivityCenterOpen]);

  return (
    <div className="lengrvis-shell">
      <Sidebar activeView={activeView} onViewChange={onViewChange} />

      <div className="lengrvis-workbench-layout">
        <main className="lengrvis-main">
          <WindowBar
            activeView={activeView}
            viewMeta={viewMeta}
            connectionState={connectionState}
            isLoading={isLoading}
            onRefresh={onRefresh}
            onOpenApprovals={onOpenApprovals}
            hasPendingApproval={hasPendingApproval}
            activityCenterState={workbench.state}
            activityCenterLabel={activityCenterLabel}
            isActivityCenterOpen={isActivityCenterOpen}
            activityCenterTriggerRef={activityCenterTriggerRef}
            onToggleActivityCenter={() => {
              if (isActivityCenterOpen) closeActivityCenter();
              else openActivityCenter();
            }}
          />
          {children}
        </main>
      </div>

      {isActivityCenterMounted ? (
        <>
          <button
            className={isActivityCenterOpen ? "workbench-scrim" : "workbench-scrim workbench-scrim--closing"}
            type="button"
            tabIndex={-1}
            aria-label="关闭活动中心"
            onClick={closeActivityCenter}
          />
          <WorkbenchPanel
            panelRef={activityCenterPanelRef}
            state={workbench.state}
            title={workbench.title}
            detail={workbench.detail}
            connection={connection}
            health={health}
            activeTask={workbench.activeTask}
            recentMessages={recentReadableMessages}
            isClosing={!isActivityCenterOpen}
            onClose={closeActivityCenter}
          />
        </>
      ) : null}
    </div>
  );
}

type WorkbenchAgentState = "idle" | "running" | "completed" | "error";

interface StatusSummary {
  state: WorkbenchAgentState;
  text: string;
  detail: string;
}

function WorkbenchPanel({
  panelRef,
  state,
  title,
  detail,
  connection,
  health,
  activeTask,
  recentMessages,
  isClosing,
  onClose
}: {
  panelRef: RefObject<HTMLElement>;
  state: WorkbenchAgentState;
  title: string;
  detail: string;
  connection: StatusSummary;
  health: StatusSummary;
  activeTask?: TaskEvent;
  recentMessages: ChatMessage[];
  isClosing: boolean;
  onClose: () => void;
}) {
  const { effectiveMotion } = useUiPreferences();
  const image = workbenchImageForState(state, effectiveMotion);

  return (
    <aside
      ref={panelRef}
      id="lengrvis-activity-center"
      className={`workbench-panel workbench-panel--${state}${isClosing ? " workbench-panel--closing" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby="lengrvis-activity-center-title"
      tabIndex={-1}
    >
      <div className="workbench-panel__header">
        <div>
          <span>工作台状态</span>
          <strong id="lengrvis-activity-center-title">活动中心</strong>
          <p>任务进展、连接状态与最近回复</p>
        </div>
        <button className="icon-button" type="button" aria-label="关闭活动中心" onClick={onClose} autoFocus>
          <X size={16} aria-hidden="true" />
        </button>
      </div>

      <section className="workbench-card workbench-card--agent">
        <div className="workbench-card__head">
          <span>
            <Sparkles size={14} aria-hidden="true" />
            状态总览
          </span>
          <strong>{stateLabel(state, connection.state)}</strong>
        </div>
        <div className="workbench-agent-stage">
          <img
            className={effectiveMotion === "full" && state === "running" ? "workbench-agent-image workbench-agent-gif" : "workbench-agent-image workbench-agent-still"}
            src={image}
            alt=""
            draggable={false}
          />
        </div>
        <div className="workbench-status-stack">
          <StatusLine title="Lengrvis 连接" summary={connection} />
          <StatusLine title="最近任务" summary={{ state, text: title, detail }} />
          <StatusLine title="电脑健康" summary={health} />
        </div>
      </section>

      <section className="workbench-card">
        <div className="workbench-card__head">
          <span>
            <TerminalSquare size={14} aria-hidden="true" />
            下一步
          </span>
        </div>
        {activeTask ? (
          <div className="workbench-task">
            <span className={`workbench-task__dot workbench-task__dot--${activeTask.state}`} />
            <div>
              <strong>{activeTask.title}</strong>
              <p>{activeTask.description}</p>
            </div>
          </div>
        ) : (
          <p className="workbench-empty">在首页输入一句话，我会先整理要做什么，再开始执行。</p>
        )}
      </section>

      <section className="workbench-card workbench-card--messages">
        <div className="workbench-card__head">
          <span>
            <MessageSquarePlus size={14} aria-hidden="true" />
            最近回复
          </span>
        </div>
        <div className="workbench-message-list">
          {recentMessages.length ? recentMessages.map((message) => (
            <div className="workbench-message" key={message.id}>
              <strong>{friendlyMessageAuthor(message)}</strong>
              <p>{messagePreview(message)}</p>
            </div>
          )) : (
            <p className="workbench-empty">这里会保留最近几条对话，方便你接着问。</p>
          )}
        </div>
      </section>
    </aside>
  );
}

function StatusLine({ title, summary }: { title: string; summary: StatusSummary }) {
  const Icon = summary.state === "error" ? TriangleAlert : summary.state === "idle" ? CircleDashed : CheckCircle2;
  return (
    <div className={`workbench-status-line workbench-status-line--${summary.state}`}>
      <span className="workbench-status-line__icon">
        <Icon size={13} aria-hidden="true" />
      </span>
      <div>
        <strong>{title}</strong>
        <p>{summary.text}</p>
        <small>{summary.detail}</small>
      </div>
    </div>
  );
}

function getWorkbenchState({
  isLoading,
  messages,
  tasks,
  connectionState
}: {
  isLoading: boolean;
  messages: ChatMessage[];
  tasks: TaskEvent[];
  connectionState: ConnectionState;
}) {
  const visibleTasks = recentUserFacingTasks(tasks);
  const activeTask = visibleTasks.find((task) => task.state === "running" || task.state === "queued" || task.state === "blocked");
  const latestTask = visibleTasks[0];
  const latestMessage = messages[messages.length - 1];
  const hasError = latestTask?.state === "failed" || latestMessage?.status === "failed";

  if (connectionState === "offline" && !activeTask) {
    return {
      state: "idle" as const,
      title: "暂无最近任务",
      detail: "助手暂时连不上，任务还不会进入执行流；这不代表电脑本身有问题。",
      activeTask: undefined
    };
  }

  if (connectionState === "checking" && !activeTask) {
    return {
      state: "running" as const,
      title: "正在连接服务",
      detail: "正在确认 Lengrvis 服务状态，还没有开始新的用户任务。",
      activeTask: undefined
    };
  }

  if (hasError) {
    return {
      state: "error" as const,
      title: "最近任务需关注",
      detail: "最近一次处理没有顺利完成，可以稍后重试或换个说法。",
      activeTask: activeTask ?? latestTask
    };
  }

  if (isLoading || activeTask) {
    return {
      state: "running" as const,
      title: activeTask ? "小马正在工作" : "正在同步工作台",
      detail: activeTask?.description || "我在整理最新状态，很快就可以继续使用。",
      activeTask
    };
  }

  if (latestTask?.state === "completed") {
    return {
      state: "completed" as const,
      title: "刚完成一项工作",
      detail: latestTask.description || "任务已经收尾，可以继续发起下一步。",
      activeTask: latestTask
    };
  }

  return {
    state: "idle" as const,
    title: latestTask ? "暂无进行中的任务" : "暂无最近任务",
    detail: latestTask ? "最近任务已经收尾，可以继续发起下一步。" : "把文件、电脑或文档相关的需求交给我，我会先确认要做什么。",
    activeTask: undefined
  };
}

function getConnectionSummary(connectionState: ConnectionState, _isLoading: boolean): StatusSummary {
  if (connectionState === "online") {
    return {
      state: "completed",
      text: "已连接",
      detail: "Lengrvis 服务可用，可以继续发起任务。"
    };
  }
  if (connectionState === "checking") {
    return {
      state: "running",
      text: "正在连接",
      detail: "正在同步服务状态，这不代表电脑异常。"
    };
  }
  return {
    state: "idle",
    text: "连接待恢复",
    detail: "助手暂时连不上，电脑本身没有因此变成故障。点右上角刷新可重新检查。"
  };
}

function getComputerHealthSummary(info?: SystemInfo): StatusSummary {
  const diagnostics = info?.diagnostics;
  const suggestions = info?.diagnostics?.suggestions ?? [];
  const hasInfoValues = Boolean(diagnostics?.info && Object.keys(diagnostics.info).length > 0);
  const hasSnapshot =
    Boolean(diagnostics) &&
    (hasInfoValues ||
      Boolean(diagnostics?.disks?.length) ||
      Boolean(diagnostics?.topProcesses?.length) ||
      Boolean(diagnostics?.startupItems?.length) ||
      Boolean(suggestions.length));
  const hasCriticalIssue = suggestions.some((suggestion) =>
    !/No critical system issue detected/i.test(suggestion) &&
    /low|critical|error|failed|严重|不足|失败|异常/i.test(suggestion)
  );

  if (hasSnapshot && hasCriticalIssue) {
    return {
      state: "error",
      text: "建议关注",
      detail: "只读诊断发现了需要留意的项目，可以进入“此电脑”查看。"
    };
  }
  if (hasSnapshot) {
    return {
      state: "completed",
      text: "未发现关键问题",
      detail: "只读诊断未发现关键系统问题。部分“暂未读取”不是异常。"
    };
  }
  return {
    state: "idle",
    text: "尚未检查",
    detail: "进入“此电脑”刷新后，会显示电脑健康快照。"
  };
}

function workbenchImageForState(state: WorkbenchAgentState, motion: EffectiveMotion): string {
  if (state === "running") return motion === "full" ? xiaomaRunningGif : xiaomaRunningStill;
  if (state === "completed") return xiaomaCompletedStill;
  if (state === "error") return xiaomaErrorStill;
  return xiaomaStandbyStill;
}

function stateLabel(state: WorkbenchAgentState, connectionState?: WorkbenchAgentState): string {
  if (connectionState === "error" || (connectionState === "idle" && state === "idle")) return "待连接";
  if (connectionState === "running") return "连接中";
  if (state === "running") return "运行中";
  if (state === "completed") return "已完成";
  if (state === "error") return "需关注";
  return "待命";
}

function messagePreview(message: ChatMessage): string {
  if (typeof message.content === "string") return friendlyPreviewText(message.content);
  const firstText = message.content.find((part) => part.type !== "tool_call");
  if (firstText && "text" in firstText) return friendlyPreviewText(firstText.text);
  const firstTool = message.content.find((part) => part.type === "tool_call");
  if (firstTool && firstTool.type === "tool_call") {
    if (firstTool.status === "error") return "有一步需要关注，我会在对话里说明。";
    if (firstTool.status === "success") return "我已经完成了一个处理步骤。";
    return "我正在处理相关步骤。";
  }
  return "";
}

function getRecentReadableMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages
    .filter((message) => (message.role === "user" || message.role === "assistant") && messagePreview(message).trim())
    .slice(-3);
}

function friendlyMessageAuthor(message: ChatMessage): string {
  return message.role === "user" ? "你" : "Lengrvis";
}

function friendlyPreviewText(text: string): string {
  return text
    .replace(/\bAgent\b/gi, "助手")
    .replace(/\bbackend\b/gi, "服务")
    .replace(/\bworkbench\b/gi, "工作区")
    .replace(/\btrace\b/gi, "记录")
    .trim();
}

function recentUserFacingTasks(tasks: TaskEvent[]): TaskEvent[] {
  const now = Date.now();
  return [...tasks]
    .sort((a, b) => taskUpdatedAt(b) - taskUpdatedAt(a))
    .filter((task) => now - taskUpdatedAt(task) <= 24 * 60 * 60 * 1000);
}

function taskUpdatedAt(task: TaskEvent): number {
  const time = Date.parse(task.updatedAt || task.createdAt);
  return Number.isFinite(time) ? time : 0;
}

function Sidebar({
  activeView,
  onViewChange
}: {
  activeView: ViewKey;
  onViewChange: (view: ViewKey) => void;
}) {
  return (
    <aside className="lengrvis-sidebar">
      <div className="sidebar-brand">
        <XiaoMaAvatar className="sidebar-brand__logo" />
        <span className="sidebar-brand__text">
          <strong>Lengrvis</strong>
          <small>电脑助手</small>
        </span>
      </div>

      <div className="primary-nav-wrap">
        <nav className="primary-nav" aria-label="主导航">
          {primaryNavGroups.map((group, groupIndex) => (
            <div className="primary-nav__group" key={group.label ?? `group-${groupIndex}`}>
              {group.label ? <span className="primary-nav__label">{group.label}</span> : null}
              {group.items.map((item) => (
                <SideButton
                  key={item.view}
                  icon={item.icon}
                  label={item.label}
                  active={isNavItemActive(item.view, activeView)}
                  onClick={() => onViewChange(item.view)}
                />
              ))}
            </div>
          ))}
        </nav>
        <span className="primary-nav__more" aria-hidden="true">横向滑动查看全部入口</span>
      </div>

      <div className="sidebar-user">
        <XiaoMaAvatar className="mini-avatar" />
        <span className="sidebar-user__meta">
          <strong>Lengrvis</strong>
          <em>电脑助手</em>
        </span>
      </div>
    </aside>
  );
}

function XiaoMaAvatar({ className }: { className: string }) {
  return (
    <span className={className} aria-hidden="true">
      <img className="xiaoma-avatar-gif xiaoma-avatar-still" src={xiaomaStandbyStill} alt="" draggable={false} />
    </span>
  );
}

function WindowBar({
  activeView,
  viewMeta,
  connectionState,
  isLoading,
  onRefresh,
  onOpenApprovals,
  hasPendingApproval,
  activityCenterState,
  activityCenterLabel,
  isActivityCenterOpen,
  activityCenterTriggerRef,
  onToggleActivityCenter
}: {
  activeView: ViewKey;
  viewMeta: ViewTitle;
  connectionState: ConnectionState;
  isLoading: boolean;
  onRefresh: () => void;
  onOpenApprovals: () => void;
  hasPendingApproval: boolean;
  activityCenterState: WorkbenchAgentState;
  activityCenterLabel: string;
  isActivityCenterOpen: boolean;
  activityCenterTriggerRef: RefObject<HTMLButtonElement>;
  onToggleActivityCenter: () => void;
}) {
  const showConnectionPill = connectionState !== "online" && !(activeView === "home" && connectionState === "checking");

  return (
    <header className="window-bar">
      <div className="window-bar__left">
        <div className="window-bar__title" key={activeView}>
          <span>{viewMeta.title}</span>
          <small>{viewMeta.subtitle}</small>
        </div>
      </div>
      <div className="window-actions">
        {showConnectionPill ? (
          <span className={`connection-pill connection-pill--${connectionState}`}>
            <span className="connection-pill__dot" />
            {connectionState === "checking" ? "正在连接 Lengrvis" : "助手暂时连不上"}
          </span>
        ) : null}
        <button
          ref={activityCenterTriggerRef}
          className={`activity-center-trigger activity-center-trigger--${activityCenterState}`}
          type="button"
          aria-controls="lengrvis-activity-center"
          aria-expanded={isActivityCenterOpen}
          aria-label={`${isActivityCenterOpen ? "关闭" : "打开"}活动中心，当前${activityCenterLabel}`}
          onClick={onToggleActivityCenter}
        >
          <span className="activity-center-trigger__icon" aria-hidden="true">
            <Activity size={15} />
          </span>
          <span>活动中心</span>
          <strong>{activityCenterLabel}</strong>
          {hasPendingApproval ? <i aria-hidden="true" /> : null}
        </button>
        <button className="icon-button" aria-label="刷新" onClick={onRefresh} disabled={isLoading} type="button">
          {isLoading ? (
            <Loader2 className="spin-icon" size={15} aria-hidden="true" />
          ) : (
            <RefreshCw size={15} aria-hidden="true" />
          )}
        </button>
        {hasPendingApproval ? (
          <button className="icon-button icon-button--alert" aria-label="有待审批项目" onClick={onOpenApprovals} type="button">
            <Bell size={15} aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </header>
  );
}

function isNavItemActive(itemView: ViewKey, activeView: ViewKey): boolean {
  if (itemView === activeView) return true;
  const family = libraryFamilyForView(activeView);
  if (family === "knowledge") return itemView === "documents";
  if (family === "gallery") return itemView === "gallery";
  return false;
}

function SideButton({
  icon: Icon,
  label,
  active,
  onClick
}: {
  icon: LucideIcon;
  label: string;
  active?: boolean;
  onClick: () => void;
}) {
  const className = ["side-button", active ? "side-button--active" : ""].filter(Boolean).join(" ");
  return (
    <button className={className} onClick={onClick} type="button">
      <Icon size={15} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}
