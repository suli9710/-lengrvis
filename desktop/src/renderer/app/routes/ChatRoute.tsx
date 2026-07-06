import { lazy, Suspense } from "react";

import { ChatPanel } from "../../components/ChatPanel";
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
  onSendMessage
}: ChatRouteProps) {
  return (
    <section className="conversation-view">
      <ChatPanel
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
