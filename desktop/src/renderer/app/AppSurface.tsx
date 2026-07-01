import { inferActiveOfficeAgentId } from "../features/office";
import { ShellFrame } from "../features/shell";
import { localLibraryViewKeys } from "../views/localLibrarySections";
import { AppSurfaceApprovalDialog } from "./AppSurfaceApprovalDialog";
import type { AppSurfaceProps } from "./AppSurfaceTypes";
import { AgentsRoute } from "./routes/AgentsRoute";
import { BrowserRoute } from "./routes/BrowserRoute";
import { ChatRoute } from "./routes/ChatRoute";
import { ComputerRoute } from "./routes/ComputerRoute";
import { FilesRoute } from "./routes/FilesRoute";
import { HomeRoute } from "./routes/HomeRoute";
import { LocalLibraryRoute } from "./routes/LocalLibraryRoute";
import { MemoryRoute } from "./routes/MemoryRoute";
import { SafetyRoute } from "./routes/SafetyRoute";
import { SettingsRoute } from "./routes/SettingsRoute";
import { SkillsRoute } from "./routes/SkillsRoute";

export type { AppSurfaceProps } from "./AppSurfaceTypes";

export function AppSurface(props: AppSurfaceProps) {
  const {
    activeView,
    agentConversations,
    connectionState,
    isLoading,
    messages,
    pendingApprovals,
    plan,
    safetyReview,
    systemInfo,
    tasks,
    onOpenApprovals,
    onRefreshWorkspace,
    onViewChange
  } = props;
  const activeOfficeAgentId = inferActiveOfficeAgentId(tasks, plan, agentConversations, safetyReview.status);
  const safetyAlert = safetyReview.status === "needs_review" || safetyReview.status === "blocked";

  return (
    <>
      <ShellFrame
        activeView={activeView}
        connectionState={connectionState}
        isLoading={isLoading}
        onViewChange={onViewChange}
        onRefresh={onRefreshWorkspace}
        onOpenApprovals={onOpenApprovals}
        hasPendingApproval={pendingApprovals.length > 0}
        messages={messages}
        tasks={tasks}
        systemInfo={systemInfo}
      >
        {localLibraryViewKeys.has(activeView) ? <LocalLibraryRoute {...props} /> : null}
        {activeView === "home" ? (
          <HomeRoute {...props} activeOfficeAgentId={activeOfficeAgentId} safetyAlert={safetyAlert} />
        ) : null}
        {activeView === "chat" ? <ChatRoute {...props} /> : null}
        {activeView === "files" ? <FilesRoute {...props} /> : null}
        {activeView === "computer" ? <ComputerRoute {...props} /> : null}
        {activeView === "agents" ? <AgentsRoute {...props} /> : null}
        {activeView === "browser" ? <BrowserRoute {...props} /> : null}
        {activeView === "memories" ? <MemoryRoute {...props} /> : null}
        {activeView === "safety" ? <SafetyRoute {...props} /> : null}
        {activeView === "skills" ? <SkillsRoute {...props} /> : null}
        {activeView === "settings" ? <SettingsRoute {...props} /> : null}
      </ShellFrame>

      <AppSurfaceApprovalDialog {...props} />
    </>
  );
}
