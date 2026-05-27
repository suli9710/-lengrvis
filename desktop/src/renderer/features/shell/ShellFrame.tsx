import {
  Bell,
  BookOpenText,
  Home,
  Laptop,
  Loader2,
  MessageSquarePlus,
  RefreshCw,
  Settings,
  type LucideIcon
} from "lucide-react";
import type { ReactNode } from "react";

import xiaomaStandbyGif from "../../assets/xiaoma-agent/standby.gif";
import type { ConnectionState, ViewKey } from "../../store";

export interface NavItem {
  icon: LucideIcon;
  label: string;
  view: ViewKey;
}

export interface RecentDialogue {
  label: string;
  onSelect: () => void;
}

export interface ViewTitle {
  title: string;
  subtitle: string;
}

export const primaryNavItems: NavItem[] = [
  { icon: Home, label: "首页", view: "home" },
  { icon: MessageSquarePlus, label: "对话", view: "chat" },
  { icon: BookOpenText, label: "文件", view: "files" },
  { icon: Laptop, label: "电脑", view: "computer" },
  { icon: Settings, label: "设置", view: "settings" }
];

export const viewTitles: Record<ViewKey, ViewTitle> = {
  browser: { title: "浏览器监看", subtitle: "查看浏览器活动，并在需要时接管控制" },
  home: { title: "首页", subtitle: "让 Mavris 帮你处理电脑上的事务" },
  chat: { title: "对话", subtitle: "继续和 Mavris 协作" },
  files: { title: "文件", subtitle: "查找、整理和分析你的文档" },
  computer: { title: "电脑", subtitle: "检查这台设备并打开常用工具" },
  agents: { title: "进度", subtitle: "当前正在处理的工作" },
  memories: { title: "记忆", subtitle: "Mavris 后续可使用的本地信息" },
  safety: { title: "审批", subtitle: "等待你确认的项目" },
  settings: { title: "设置", subtitle: "偏好、安全权限和运行配置" }
};

export function ShellFrame({
  activeView,
  connectionState,
  isLoading,
  onViewChange,
  onRefresh,
  onOpenApprovals,
  hasPendingApproval,
  children
}: {
  activeView: ViewKey;
  connectionState: ConnectionState;
  isLoading: boolean;
  onViewChange: (view: ViewKey) => void;
  onRefresh: () => void;
  onOpenApprovals: () => void;
  hasPendingApproval: boolean;
  children: ReactNode;
}) {
  const viewMeta = viewTitles[activeView];

  return (
    <div className="marvis-shell">
      <Sidebar
        activeView={activeView}
        onViewChange={onViewChange}
      />

      <main className="marvis-main">
        <WindowBar
          viewMeta={viewMeta}
          connectionState={connectionState}
          isLoading={isLoading}
          onRefresh={onRefresh}
          onOpenApprovals={onOpenApprovals}
          hasPendingApproval={hasPendingApproval}
        />
        {children}
      </main>
    </div>
  );
}

function Sidebar({
  activeView,
  onViewChange
}: {
  activeView: ViewKey;
  onViewChange: (view: ViewKey) => void;
}) {
  return (
    <aside className="marvis-sidebar">
      <div className="sidebar-brand">
        <XiaoMaAvatar className="sidebar-brand__logo" />
        <span className="sidebar-brand__text">
          <strong>Mavris</strong>
          <small>电脑助手</small>
        </span>
      </div>

      <nav className="primary-nav" aria-label="主导航">
        {primaryNavItems.map((item) => (
          <SideButton
            key={item.view}
            icon={item.icon}
            label={item.label}
            active={activeView === item.view}
            onClick={() => onViewChange(item.view)}
          />
        ))}
      </nav>

      <div className="sidebar-user">
        <XiaoMaAvatar className="mini-avatar" />
        <span className="sidebar-user__meta">
          <strong>Mavris</strong>
          <em>电脑助手</em>
        </span>
      </div>
    </aside>
  );
}

function XiaoMaAvatar({ className }: { className: string }) {
  return (
    <span className={className} aria-hidden="true">
      <img className="xiaoma-avatar-gif" src={xiaomaStandbyGif} alt="" draggable={false} />
    </span>
  );
}

function WindowBar({
  viewMeta,
  connectionState,
  isLoading,
  onRefresh,
  onOpenApprovals,
  hasPendingApproval
}: {
  viewMeta: ViewTitle;
  connectionState: ConnectionState;
  isLoading: boolean;
  onRefresh: () => void;
  onOpenApprovals: () => void;
  hasPendingApproval: boolean;
}) {
  return (
    <header className="window-bar">
      <div className="window-bar__left">
        <div className="window-bar__title">
          <span>{viewMeta.title}</span>
          <small>{viewMeta.subtitle}</small>
        </div>
      </div>
      <div className="window-actions">
        {connectionState !== "online" ? (
          <span className={`connection-pill connection-pill--${connectionState}`}>
            <span className="connection-pill__dot" />
            {connectionState === "checking" ? "正在检查连接" : "离线"}
          </span>
        ) : null}
        <button className="icon-button" aria-label="刷新" onClick={onRefresh} disabled={isLoading} type="button">
          {isLoading ? (
            <Loader2 size={15} aria-hidden="true" style={{ animation: "dot-spin 1s linear infinite" }} />
          ) : (
            <RefreshCw size={15} aria-hidden="true" />
          )}
        </button>
        {hasPendingApproval ? (
          <button className="icon-button" aria-label="有待审批项目" onClick={onOpenApprovals} type="button">
            <Bell size={15} aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </header>
  );
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
  return (
    <button className={active ? "side-button side-button--active" : "side-button"} onClick={onClick} type="button">
      <Icon size={15} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}
