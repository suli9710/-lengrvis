import { lazy, Suspense } from "react";

import { inferActiveOfficeAgentId } from "../features/office";
import { ShellFrame } from "../features/shell";
import { RouteLoading } from "../appViewModel";
import { localLibraryViewKeys } from "../views/localLibrarySections";
import type { AppSurfaceProps } from "./AppSurfaceTypes";
import {
  ApprovalLoadFailure,
  ApprovalLoadState,
  RouteLoadFailure,
  SurfaceErrorBoundary
} from "./SurfaceErrorBoundary";
import { HomeRoute } from "./routes/HomeRoute";

const AgentsRoute = lazy(() => import("./routes/AgentsRoute").then((module) => ({ default: module.AgentsRoute })));
const AppSurfaceApprovalDialog = lazy(() => import("./AppSurfaceApprovalDialog").then((module) => ({ default: module.AppSurfaceApprovalDialog })));
const BrowserRoute = lazy(() => import("./routes/BrowserRoute").then((module) => ({ default: module.BrowserRoute })));
const ChatRoute = lazy(() => import("./routes/ChatRoute").then((module) => ({ default: module.ChatRoute })));
const ComputerRoute = lazy(() => import("./routes/ComputerRoute").then((module) => ({ default: module.ComputerRoute })));
const FilesRoute = lazy(() => import("./routes/FilesRoute").then((module) => ({ default: module.FilesRoute })));
const LocalLibraryRoute = lazy(() => import("./routes/LocalLibraryRoute").then((module) => ({ default: module.LocalLibraryRoute })));
const MemoryRoute = lazy(() => import("./routes/MemoryRoute").then((module) => ({ default: module.MemoryRoute })));
const SafetyRoute = lazy(() => import("./routes/SafetyRoute").then((module) => ({ default: module.SafetyRoute })));
const SettingsRoute = lazy(() => import("./routes/SettingsRoute").then((module) => ({ default: module.SettingsRoute })));
const SkillsRoute = lazy(() => import("./routes/SkillsRoute").then((module) => ({ default: module.SkillsRoute })));

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
        <SurfaceErrorBoundary key={activeView} fallback={<RouteLoadFailure />}>
          <Suspense fallback={<RouteLoading />}>
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
          </Suspense>
        </SurfaceErrorBoundary>
      </ShellFrame>

      {pendingApprovals.length > 0 || props.isApprovalOpen ? (
        <SurfaceErrorBoundary
          key={props.pendingApproval?.id ?? "approval"}
          fallback={props.isApprovalOpen ? <ApprovalLoadFailure onClose={props.onCloseApproval} /> : null}
        >
          <Suspense fallback={props.isApprovalOpen ? <ApprovalLoadState /> : null}>
            <AppSurfaceApprovalDialog {...props} />
          </Suspense>
        </SurfaceErrorBoundary>
      ) : null}
    </>
  );
}
