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
  { icon: Home, label: "Home", view: "home" },
  { icon: MessageSquarePlus, label: "Chat", view: "chat" },
  { icon: BookOpenText, label: "Files", view: "files" },
  { icon: Laptop, label: "Computer", view: "computer" },
  { icon: Settings, label: "Settings", view: "settings" }
];

export const viewTitles: Record<ViewKey, ViewTitle> = {
  browser: { title: "Watch Mode", subtitle: "Embedded browser activity and takeover controls" },
  home: { title: "Home", subtitle: "Ask Mavris for help with your computer" },
  chat: { title: "Chat", subtitle: "Keep working with Mavris" },
  files: { title: "Files", subtitle: "Find and organize your documents" },
  computer: { title: "Computer", subtitle: "Check this device and open common tools" },
  agents: { title: "Progress", subtitle: "Work currently in progress" },
  memories: { title: "Saved details", subtitle: "Things Mavris can remember for later" },
  safety: { title: "Review", subtitle: "Items waiting for your permission" },
  settings: { title: "Settings", subtitle: "Preferences and permissions" }
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
          <small>Computer helper</small>
        </span>
      </div>

      <nav className="primary-nav" aria-label="Main navigation">
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
          <em>Computer helper</em>
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
            {connectionState === "checking" ? "Checking connection" : "Offline"}
          </span>
        ) : null}
        <button className="icon-button" aria-label="Refresh" onClick={onRefresh} disabled={isLoading} type="button">
          {isLoading ? (
            <Loader2 size={15} aria-hidden="true" style={{ animation: "dot-spin 1s linear infinite" }} />
          ) : (
            <RefreshCw size={15} aria-hidden="true" />
          )}
        </button>
        {hasPendingApproval ? (
          <button className="icon-button" aria-label="Review needed" onClick={onOpenApprovals} type="button">
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
