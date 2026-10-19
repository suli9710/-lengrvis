import { lazy, Suspense } from "react";

import { ChatPanel } from "../../components/ChatPanel";
import { ButlerChatContext } from "../../features/butler/ButlerChatContext";
import { PlanViewer } from "../../components/PlanViewer";
import { RouteLoading } from "../../appViewModel";
import type { AppSurfaceProps } from "../AppSurfaceTypes";

const TaskTimeline = lazy(() => import("../../components/TaskTimeline").then((module) => ({ default: module.TaskTimeline })));

type ChatRouteProps = Pick<
  AppSurfaceProps,
  | "api"
  | "connectionState"
  | "focusedTaskId"
  | "intentSuggestions"
  | "messages"
  | "onTaskPilotAction"
  | "plan"
  | "tasks"
  | "onExecuteSuggestion"
  | "onSendMessage"
  | "butler"
  | "onOpenButlerApproval"
  | "onViewChange"
>;

export function ChatRoute({
  api,
  connectionState,
  focusedTaskId,
  intentSuggestions,
  messages,
  onTaskPilotAction,
  plan,
  tasks,
  onExecuteSuggestion,
  onSendMessage,
  butler,
  onOpenButlerApproval,
  onViewChange
}: ChatRouteProps) {
  return (
    <section className="conversation-view">
      <ChatPanel
        context={butler ? <ButlerChatContext butler={butler} onOpenApproval={(matter) => onOpenButlerApproval?.(matter)} onOpenBrowser={() => onViewChange("browser")} onOpenOverview={() => onViewChange("butler")} /> : undefined}
        messages={messages}
        connectionState={connectionState}
        onSend={onSendMessage}
        onExecuteSuggestion={onExecuteSuggestion}
        suggestions={intentSuggestions}
        autoFocus
        api={api}
      />
      <div className="conversation-side">
        <PlanViewer plan={plan} />
        <Suspense fallback={<RouteLoading />}>
          <TaskTimeline tasks={tasks} api={api} focusedTaskId={focusedTaskId} onTaskPilotAction={onTaskPilotAction} />
        </Suspense>
      </div>
    </section>
  );
}
